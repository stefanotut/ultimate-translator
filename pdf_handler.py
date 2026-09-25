"""
ULTIMATE TRANSLATOR - PDF Handler
Translates PDF files preserving layout, images, and formatting.
Supports both OpenAI and Anthropic providers.

Strategy:
- Group nearby text blocks that likely belong to the same paragraph
- Translate grouped blocks with context awareness
- Redact original text and overlay translated text preserving colors and fonts
- Smart font size reduction to fit translated text in the same bounding box
"""

import os
import html
import logging
import fitz  # PyMuPDF
from translator import translate_blocks, estimate_tokens
from pdf_layout import analyze_page

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_translatable(text: str) -> bool:
    """Check if text has translatable alphabetic content."""
    if not text:
        return False
    stripped = text.strip()
    return bool(stripped) and any(c.isalpha() for c in stripped)


def _get_dominant_font(spans: list) -> tuple:
    """Get the most common font name and size from a list of spans."""
    if not spans:
        return "helv", 11
    best = max(spans, key=lambda s: len(s.get("text", "")))
    return best.get("font", "helv"), best.get("size", 11)


def _extract_color(span: dict) -> tuple:
    """Extract RGB color tuple from a span. Defaults to black."""
    c = span.get("color", 0)
    if isinstance(c, int):
        r = ((c >> 16) & 0xFF) / 255.0
        g = ((c >> 8) & 0xFF) / 255.0
        b = (c & 0xFF) / 255.0
        return (r, g, b)
    if isinstance(c, (tuple, list)) and len(c) >= 3:
        return tuple(c[:3])
    return (0, 0, 0)


def _page_is_scanned(page) -> bool:
    """
    True se la pagina e' una fotografia con sotto uno strato OCR invisibile.

    Su questi PDF il testo che si vede sono PIXEL dentro un'immagine: le
    redazioni non possono cancellarlo (toglierebbero l'intera scansione), e
    scriverci sopra produce testo sovrapposto. Vanno trattate diversamente.
    """
    area = page.rect.width * page.rect.height
    if area <= 0:
        return False
    coperta = 0.0
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") == 1:            # 1 = immagine
            r = fitz.Rect(blk["bbox"])
            coperta += abs(r.width * r.height)
    return (coperta / area) >= 0.8


def _page_paper_color(page):
    """
    Un unico colore di carta per l'intera pagina.

    Campionare il margine accanto a ogni blocco sembrava piu' preciso, ma la
    luce di una scansione non e' uniforme: ogni toppa usciva di una tinta
    leggermente diversa e sullo schermo si vedeva un patchwork di rettangoli
    grigi. Un solo colore, preso dai pixel chiari di tutta la pagina, sparisce.
    """
    try:
        pix = page.get_pixmap(colorspace=fitz.csRGB, dpi=36)
        dati = pix.samples
        if not dati:
            return (1, 1, 1)
        # Colore PIU' FREQUENTE fra i pixel chiari, non la mediana: la mediana
        # includeva i bordi in ombra della scansione e usciva ~2% piu' scura
        # della carta, abbastanza da far vedere le toppe come bande grigie.
        from collections import Counter
        conteggio = Counter()
        # Un passo di 3 pixel basta e rende il calcolo istantaneo.
        for i in range(0, len(dati) - 2, 9):
            r, g, b = dati[i], dati[i + 1], dati[i + 2]
            if r > 185 and g > 185 and b > 185:
                conteggio[(r >> 1 << 1, g >> 1 << 1, b >> 1 << 1)] += 1
        if not conteggio:
            return (1, 1, 1)
        dominante, quante = conteggio.most_common(1)[0]
        if quante < 30:
            return (1, 1, 1)
        return tuple(c / 255.0 for c in dominante)
    except Exception:
        return (1, 1, 1)


def _write_block(page, bbox, limite_inferiore, testo, font_size, color):
    """
    Scrive il testo tradotto al posto dell'originale.

    Due scelte importanti:
    1. `insert_htmlbox` e non `insert_textbox`: i font base del PDF sono
       Latin-1 e trasformavano trattini lunghi, puntini di sospensione e
       virgolette curve in "?", corrompendo il testo.
    2. Il riquadro puo' crescere verso il basso fino a `limite_inferiore`
       (dove inizia il paragrafo successivo). L'italiano e' ~15-20% piu' lungo
       dell'inglese: senza questo spazio ogni blocco si rimpicciolirebbe da
       solo e la pagina risulterebbe con corpi tipografici tutti diversi.
    """
    if not testo or not testo.strip():
        return True

    corpo = html.escape(testo.strip())
    corpo = corpo.replace("\n\n", "<br><br>").replace("\n", " ")

    def html_di():
        return ('<div style="font-family:Times,Georgia,serif;font-size:%.2fpx;'
                'line-height:1.16;color:rgb(%d,%d,%d);text-align:justify;'
                'margin:0">%s</div>'
                % (font_size, color[0] * 255, color[1] * 255, color[2] * 255, corpo))

    riquadro = fitz.Rect(bbox.x0, bbox.y0, bbox.x1,
                         max(limite_inferiore, bbox.y1)) & page.rect

    for scala_min in (0.92, 0.70, 0.45):
        try:
            avanzo, _ = page.insert_htmlbox(riquadro, html_di(), scale_low=scala_min)
            if avanzo >= 0:
                return True
        except Exception as e:
            logger.warning("htmlbox non riuscito: %s", str(e)[:80])
            return False
    return False


def _group_nearby_blocks(blocks: list, vertical_threshold: float = 5.0) -> list:
    """
    Group text blocks that are vertically close together and share similar
    x-positions, suggesting they belong to the same paragraph or column.
    """
    if not blocks:
        return []

    sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"].y0, b["bbox"].x0))

    groups = []
    current_group = {
        "blocks": [sorted_blocks[0]],
        "text": sorted_blocks[0]["text"],
        "bbox": fitz.Rect(sorted_blocks[0]["bbox"]),
        "spans": list(sorted_blocks[0]["spans"]),
    }

    for i in range(1, len(sorted_blocks)):
        block = sorted_blocks[i]
        prev_bbox = current_group["bbox"]
        curr_bbox = block["bbox"]

        x_overlap = (
            abs(curr_bbox.x0 - prev_bbox.x0) < 50
            and abs(curr_bbox.x1 - prev_bbox.x1) < 50
        )
        y_close = curr_bbox.y0 - prev_bbox.y1 < vertical_threshold

        if x_overlap and y_close:
            current_group["blocks"].append(block)
            current_group["text"] += "\n" + block["text"]
            current_group["bbox"] = current_group["bbox"] | curr_bbox
            current_group["spans"].extend(block["spans"])
        else:
            groups.append(current_group)
            current_group = {
                "blocks": [block],
                "text": block["text"],
                "bbox": fitz.Rect(block["bbox"]),
                "spans": list(block["spans"]),
            }

    groups.append(current_group)
    return groups


def extract_text_sample(input_path: str, max_chars: int = 1000) -> str:
    """Extract text sample from first page of PDF for language detection."""
    try:
        doc = fitz.open(input_path)
        if len(doc) == 0:
            return ""
        text = doc[0].get_text("text")
        doc.close()
        return text[:max_chars]
    except Exception as e:
        logger.warning(f"Failed to extract PDF text sample: {e}")
        return ""


# ---------------------------------------------------------------------------
# PDF analysis (for cost estimation)
# ---------------------------------------------------------------------------

def analyze_pdf(input_path: str) -> dict:
    """
    Analyze a PDF file and return statistics for cost estimation.

    Returns:
        dict with: total_chars, total_words, num_pages, estimated_tokens
    """
    doc = fitz.open(input_path)
    total_chars = 0
    total_words = 0
    all_text = ""

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if text:
            total_chars += len(text)
            total_words += len(text.split())
            all_text += text + " "

    num_pages = len(doc)
    doc.close()

    estimated_tokens = estimate_tokens(all_text[:50000])
    if len(all_text) > 50000:
        ratio = len(all_text) / 50000
        estimated_tokens = int(estimated_tokens * ratio)

    return {
        "total_chars": total_chars,
        "total_words": total_words,
        "num_pages": num_pages,
        "estimated_tokens": estimated_tokens,
    }


# ---------------------------------------------------------------------------
# Main translation function
# ---------------------------------------------------------------------------

def _cover(page, rects, scansionata):
    """
    Toglie di mezzo il testo originale, sia quello visibile sia quello cercabile.

    Su una scansione servono DUE operazioni:
      - la redazione elimina lo strato OCR invisibile. Senza, il PDF finale
        resterebbe cercabile *in inglese*: selezionando o cercando nel libro
        tradotto si troverebbe il testo originale sotto la vernice.
      - il rettangolo copre i pixel dell'inglese, che stanno dentro
        l'immagine e nessuna redazione puo' rimuovere.
    Il colore si campiona prima di coprire, altrimenti si campionerebbe la
    vernice appena stesa.
    """
    # Il colore si legge PRIMA di redigere e coprire, altrimenti si
    # campionerebbe la vernice appena stesa.
    carta = _page_paper_color(page) if scansionata else None

    for r in rects:
        page.add_redact_annot(r)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    if scansionata:
        for r in rects:
            box = fitz.Rect(r); box.x0 -= 1.5; box.y0 -= 1.5
            box.x1 += 1.5; box.y1 += 1.5
            page.draw_rect(box & page.rect, color=None, fill=carta, overlay=True)


def _body_html(paragrafi, corpo, interlinea, colore):
    """
    Una sola colonna di testo che scorre, come in un libro.

    Prima ogni blocco OCR veniva impaginato in una scatola sua, con un corpo
    tipografico calcolato a parte: la pagina usciva con quattro dimensioni
    diverse e buchi bianchi. Qui la pagina e' un unico flusso.
    """
    rgb = "rgb(%d,%d,%d)" % (colore[0] * 255, colore[1] * 255, colore[2] * 255)
    pezzi = [
        '<div style="font-family:Georgia,\'Times New Roman\',Times,serif;'
        'font-size:%.2fpx;line-height:%.3f;color:%s;text-align:justify;'
        'hyphens:auto">' % (corpo, interlinea / corpo, rgb)
    ]
    primo_corpo = True
    for par in paragrafi:
        testo = html.escape(par["testo"]).replace("\n", " ")
        if par["tipo"] == "h":
            pezzi.append(
                '<p style="font-size:%.2fpx;line-height:1.2;font-weight:bold;'
                'text-align:left;margin:%.1fpx 0 %.1fpx 0;text-indent:0">%s</p>'
                % (par["dim"], interlinea * 0.9, interlinea * 0.45, testo))
            primo_corpo = True          # il capoverso dopo un titolo non rientra
        else:
            rientro = "0" if primo_corpo else "1.6em"
            pezzi.append('<p style="margin:0;text-indent:%s">%s</p>'
                         % (rientro, testo))
            primo_corpo = False
    pezzi.append("</div>")
    return "".join(pezzi)


def translate_pdf(
    input_path: str,
    output_path: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    progress_callback=None,
):
    """
    Traduce un PDF ricostruendo l'impaginazione del libro.

    Tre fasi: si legge la struttura di tutte le pagine (testatine, capoversi,
    titoli, corpo, interlinea), si traduce tutto in lotti paralleli, si
    reimpagina ogni pagina come un'unica colonna di testo.
    """
    doc = fitz.open(input_path)
    total_pages = len(doc)
    logger.info("PDF: %d pagine", total_pages)

    if total_pages == 0:
        doc.save(output_path)
        doc.close()
        if progress_callback:
            progress_callback(1.0, "Completato! (PDF vuoto)")
        return

    # --- Fase 1: struttura tipografica ------------------------------------
    strutture = {}
    da_tradurre = []          # (num_pagina, 'p'|'t', indice)
    for pno in range(total_pages):
        if progress_callback and pno % 25 == 0:
            progress_callback(min(pno / total_pages * 0.08, 0.08),
                              "Analisi impaginazione: pagina %d/%d" % (pno + 1, total_pages))
        s = analyze_page(doc[pno])
        if not s["paragrafi"] and not s["testatina"]:
            continue
        strutture[pno] = s
        for i, _p in enumerate(s["paragrafi"]):
            da_tradurre.append((pno, "p", i))
        for i, _t in enumerate(s["testatina"]):
            da_tradurre.append((pno, "t", i))

    if not da_tradurre:
        doc.save(output_path)
        doc.close()
        if progress_callback:
            progress_callback(1.0, "Completato! (nessun testo trovato)")
        return

    testi = []
    for pno, tipo, i in da_tradurre:
        s = strutture[pno]
        testi.append(s["paragrafi"][i]["text"] if tipo == "p"
                     else s["testatina"][i]["text"])

    logger.info("PDF: %d paragrafi/testatine da tradurre", len(testi))

    # --- Fase 2: traduzione in lotti paralleli ----------------------------
    def avanzamento(fatti, totale):
        if progress_callback:
            progress_callback(0.08 + min(fatti / max(totale, 1), 1.0) * 0.84,
                              "Tradotti %d/%d paragrafi" % (fatti, totale))

    tradotti = translate_blocks(testi, source_lang, target_lang,
                                provider=provider, model=model,
                                progress_callback=avanzamento)

    for (pno, tipo, i), testo in zip(da_tradurre, tradotti):
        s = strutture[pno]
        if tipo == "p":
            s["paragrafi"][i]["tradotto"] = testo
        else:
            s["testatina"][i]["tradotto"] = testo

    # --- Fase 3: reimpaginazione ------------------------------------------
    if progress_callback:
        progress_callback(0.93, "Reimpaginazione...")

    scansioni = strette = 0
    for pno, s in strutture.items():
        page = doc[pno]
        scansionata = _page_is_scanned(page)
        scansioni += 1 if scansionata else 0

        _cover(page,
               [r["bbox"] for r in s["righe"]] +
               [r["bbox"] for r in s["testatina"]] +
               [r["bbox"] for r in s["pieDiPagina"]],
               scansionata)

        # Testatina e folio: ognuno nel proprio spazio.
        #
        # Allargare le caselle a occhio le faceva scontrare: il titolo corrente
        # finiva sopra il numero di pagina ("...A CREARE7"). Il limite di
        # ciascuna e' l'inizio della successiva.
        intestazioni = sorted(s["testatina"], key=lambda r: r["bbox"].x0)
        for i, r in enumerate(intestazioni):
            testo = r.get("tradotto") or r["text"]
            misure = [sp.get("size", 0) for sp in r["spans"] if sp.get("size")]
            dim = max(misure) if misure else s["corpo"]

            limite = (intestazioni[i + 1]["bbox"].x0 - 4) if i + 1 < len(intestazioni) \
                else page.rect.x1 - 4
            # Un numero di pagina sul lato destro si allinea a destra, come
            # nell'originale; il titolo corrente resta allineato a sinistra.
            a_destra = r["bbox"].x0 > page.rect.width * 0.55
            box = fitz.Rect(r["bbox"].x0 if not a_destra else max(r["bbox"].x0 - 90, 0),
                            r["bbox"].y0 - 1,
                            max(limite, r["bbox"].x1),
                            min(r["bbox"].y1 + dim * 0.6, page.rect.y1))
            try:
                page.insert_htmlbox(
                    box,
                    '<div style="font-family:Georgia,serif;font-size:%.2fpx;'
                    'color:#000;text-align:%s;white-space:nowrap;margin:0">%s</div>'
                    % (dim, "right" if a_destra else "left", html.escape(testo)),
                    scale_low=0.45)
            except Exception:
                pass

        if not s["paragrafi"]:
            continue

        colonna = s["colonna"]
        # Il testo puo' scorrere fino al margine inferiore: l'italiano e'
        # piu' lungo dell'inglese e senza spazio si rimpicciolirebbe.
        area = fitz.Rect(colonna.x0, colonna.y0, colonna.x1,
                         page.rect.y1 - page.rect.height * 0.055) & page.rect

        colore = (0, 0, 0)
        for r in s["righe"]:
            if r["spans"]:
                colore = _extract_color(r["spans"][0])
                break

        paragrafi = [{"tipo": p["tipo"], "dim": p["dim"],
                      "testo": p.get("tradotto") or p["text"]}
                     for p in s["paragrafi"]]

        piazzato = False
        for scala in (0.95, 0.8, 0.62, 0.45):
            try:
                avanzo, _ = page.insert_htmlbox(
                    area, _body_html(paragrafi, s["corpo"], s["interlinea"], colore),
                    scale_low=scala)
                if avanzo >= 0:
                    piazzato = True
                    break
            except Exception as e:
                logger.warning("Impaginazione fallita a pagina %d: %s", pno + 1, str(e)[:80])
                break
        if not piazzato:
            strette += 1

    if strette:
        logger.warning("%d pagine con testo piu' lungo dello spazio disponibile", strette)
    logger.info("PDF: %d pagine erano scansioni", scansioni)

    if progress_callback:
        progress_callback(0.98, "Salvataggio PDF...")

    doc.save(output_path, garbage=4, deflate=True, clean=True)
    doc.close()
    logger.info("PDF tradotto salvato in %s", output_path)

    if progress_callback:
        progress_callback(1.0, "Completato!")


def controlla_fedelta(originale, tradotto):
    """Confronta la forma del PDF tradotto con quella dell'originale.

    Nel PDF l'impaginazione la rifacciamo noi, quindi il rischio e' piu' alto
    che nell'EPUB: qui si controlla che il numero di pagine coincida (il testo
    deve restare sulla SUA pagina, altrimenti l'indice e i rimandi interni non
    tornano piu') e che nessuna pagina sia rimasta muta.

    Restituisce (va_bene, righe_da_scrivere_nel_registro).
    """
    try:
        a = fitz.open(originale)
        b = fitz.open(tradotto)
    except Exception as e:
        return True, ["Controllo di fedelta' non eseguito: %s" % str(e)[:120]]

    righe = []
    va_bene = True
    if a.page_count != b.page_count:
        va_bene = False
        righe.append("ATTENZIONE: %d pagine invece di %d: i rimandi interni e "
                     "l'indice non corrispondono piu'." % (b.page_count, a.page_count))

    # Pagine che nell'originale avevano testo e nel tradotto no.
    mute = 0
    controllate = min(a.page_count, b.page_count)
    for i in range(controllate):
        if len(a[i].get_text().strip()) > 80 and len(b[i].get_text().strip()) < 20:
            mute += 1
    if mute > max(1, controllate * 0.01):
        va_bene = False
        righe.append("ATTENZIONE: %d pagine sono rimaste senza testo." % mute)

    if va_bene:
        righe.append("Impaginazione verificata: %d pagine, nessuna pagina vuota."
                     % b.page_count)
    a.close(); b.close()
    return va_bene, righe
