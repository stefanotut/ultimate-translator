"""
ULTIMATE TRANSLATOR - Analisi dell'impaginazione di un PDF

Ricostruisce la struttura tipografica di una pagina (testatina, capoversi,
titoletti, corpo, interlinea) invece di trattare i blocchi OCR come scatole
indipendenti.

Perche' serve: i blocchi che l'OCR restituisce non sono i paragrafi del libro.
Spezzano le frasi a meta' e, se ognuno viene impaginato per conto suo, la
pagina esce con corpi diversi, buchi bianchi e capoversi persi.
"""

import re
import logging
from statistics import median

import fitz

logger = logging.getLogger(__name__)

# Un rientro di capoverso vale ~1 quadratone (12-25 pt). La deriva dovuta
# alla scansione storta e' di 1-3 pt: la soglia sta comodamente in mezzo.
INDENT_MIN = 9.0

# Una riga piu' grande del corpo e' un titolo. Misurato sul libro reale:
# titoletto di sezione 1,13x, titolo di capitolo 2,55x il corpo.
HEADING_RATIO = 1.08


def _line_text(line):
    return "".join(s.get("text", "") for s in line.get("spans", [])).strip()


def _is_junk(testo):
    """Segni sparsi dell'OCR ('\\', '.', '~') che non sono testo."""
    return len(testo) <= 2 and not any(c.isalnum() for c in testo)


def extract_lines(page):
    """Righe di testo della pagina, ordinate dall'alto in basso."""
    righe = []
    for blocco in page.get_text("dict").get("blocks", []):
        if blocco.get("type") != 0:
            continue
        for linea in blocco.get("lines", []):
            testo = _line_text(linea)
            if not testo or _is_junk(testo):
                continue
            righe.append({
                "bbox": fitz.Rect(linea["bbox"]),
                "text": testo,
                "spans": linea.get("spans", []),
            })
    righe.sort(key=lambda r: (r["bbox"].y0, r["bbox"].x0))
    return righe


def _local_margin(righe, i, finestra=4):
    """
    Margine sinistro nell'intorno di una riga.

    La scansione e' storta: il margine slitta di alcuni punti scendendo nella
    pagina. Confrontare con un margine unico farebbe sembrare rientrate le
    righe in alto e sporgenti quelle in basso.
    """
    inizio = max(0, i - finestra)
    fine = min(len(righe), i + finestra + 1)
    return median([r["bbox"].x0 for r in righe[inizio:fine]])


def _dehyphenate(prima, dopo):
    """
    Unisce due righe spezzate da un trattino di a-capo.

    'tal-' + 'ent' -> 'talent'. Si toglie il trattino solo se la riga dopo
    comincia in minuscolo: 'Mary Kay-' + 'Ash' resta com'e'.
    """
    if prima.endswith(("-", "‐", "­")) and dopo[:1].islower():
        return prima[:-1] + dopo
    return prima + " " + dopo


def analyze_page(page):
    """
    Struttura della pagina.

    Ritorna un dizionario con:
      testatina  -> [{bbox, text}]      righe in cima (titolo corrente, folio)
      pieDiPagina-> [{bbox, text}]
      paragrafi  -> [{tipo: 'p'|'h', text}]
      colonna    -> Rect della colonna di testo
      corpo      -> dimensione del carattere del testo corrente
      interlinea -> passo fra le righe, in punti
      righe      -> tutte le righe del corpo (per coprirle una a una)
    """
    righe = extract_lines(page)
    vuota = {"testatina": [], "pieDiPagina": [], "paragrafi": [], "colonna": None,
             "corpo": 10.0, "interlinea": 14.0, "righe": []}
    if not righe:
        return vuota

    h = page.rect.height
    limite_alto = page.rect.y0 + h * 0.085
    limite_basso = page.rect.y1 - h * 0.055

    testatina = [r for r in righe if r["bbox"].y1 <= limite_alto]
    pie = [r for r in righe if r["bbox"].y0 >= limite_basso]
    corpo_righe = [r for r in righe if r not in testatina and r not in pie]
    if not corpo_righe:
        corpo_righe, testatina, pie = righe, [], []

    # Corpo tipografico e interlinea, misurati sull'originale.
    dimensioni = [s.get("size", 0) for r in corpo_righe for s in r["spans"]
                  if s.get("size")]
    corpo = round(median(dimensioni), 2) if dimensioni else 10.0

    passi = [corpo_righe[i + 1]["bbox"].y0 - corpo_righe[i]["bbox"].y0
             for i in range(len(corpo_righe) - 1)]
    passi = [p for p in passi if 0 < p < corpo * 3]
    interlinea = round(median(passi), 2) if passi else corpo * 1.4

    colonna = fitz.Rect(
        min(r["bbox"].x0 for r in corpo_righe),
        min(r["bbox"].y0 for r in corpo_righe),
        max(r["bbox"].x1 for r in corpo_righe),
        max(r["bbox"].y1 for r in corpo_righe),
    )
    larghezza = colonna.width or 1

    # Ricostruzione dei paragrafi.
    #
    # Il criterio decisivo e' la DIMENSIONE del carattere, non la larghezza
    # della riga: sul libro reale il titoletto di sezione occupa il 90% della
    # riga (quindi "corto" non lo riconosce) ma e' 1,13x il corpo.
    paragrafi = []
    corrente = None
    for i, r in enumerate(corpo_righe):
        misure = [s.get("size", 0) for s in r["spans"] if s.get("size")]
        dim = median(misure) if misure else corpo
        titolo = dim > corpo * HEADING_RATIO
        rientro = r["bbox"].x0 - _local_margin(corpo_righe, i)

        if corrente is None:
            nuovo = True
        elif titolo != (corrente["tipo"] == "h"):
            nuovo = True                       # si passa da titolo a testo o viceversa
        elif titolo:
            # Titolo su piu' righe: si uniscono solo se hanno lo stesso corpo
            # (altrimenti si fonderebbero titolo di capitolo e sottotitolo).
            nuovo = abs(dim - corrente["dim"]) > corrente["dim"] * 0.12
        else:
            nuovo = rientro > INDENT_MIN

        if nuovo:
            if corrente:
                paragrafi.append(corrente)
            corrente = {"tipo": "h" if titolo else "p", "text": r["text"], "dim": dim}
        else:
            corrente["text"] = _dehyphenate(corrente["text"], r["text"])

    if corrente:
        paragrafi.append(corrente)

    return {
        "testatina": testatina,
        "pieDiPagina": pie,
        "paragrafi": paragrafi,
        "colonna": colonna,
        "corpo": corpo,
        "interlinea": interlinea,
        "righe": corpo_righe,
    }
