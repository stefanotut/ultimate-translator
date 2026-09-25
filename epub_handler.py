"""
ULTIMATE TRANSLATOR - EPUB Handler
Translates EPUB files preserving all formatting, images, styles, and structure.
Uses context-aware translation for coherent book-length output.
Supports both OpenAI and Anthropic providers.
"""

import os
import copy
import logging
import re
from bs4 import BeautifulSoup, NavigableString, Comment, Tag
import ebooklib
from ebooklib import epub
from translator import translate_text, translate_blocks, estimate_tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------------

SKIP_TAGS = frozenset({
    'script', 'style', 'code', 'pre', 'svg', 'math', 'img', 'video',
    'audio', 'source', 'link', 'meta', 'noscript', 'object', 'embed',
    'iframe', 'canvas', 'map', 'area',
})

INLINE_TAGS = frozenset({
    'a', 'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'data', 'dfn', 'em',
    'i', 'kbd', 'mark', 'q', 'rb', 'rp', 'rt', 'rtc', 'ruby', 's',
    'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var',
    'wbr', 'del', 'ins',
})

BLOCK_TAGS = frozenset({
    'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'dt',
    'dd', 'blockquote', 'figcaption', 'caption', 'summary', 'label',
    'legend', 'title', 'div', 'section', 'article', 'aside', 'header',
    'footer', 'main', 'details',
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_translatable(text: str) -> bool:
    """Check if text contains translatable alphabetic content."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(c.isalpha() for c in stripped)


def _has_only_inline_children(tag: Tag) -> bool:
    """Return True if a tag contains only inline elements and text nodes."""
    for child in tag.children:
        if isinstance(child, Tag):
            if child.name not in INLINE_TAGS and child.name not in SKIP_TAGS:
                return False
    return True


def _is_inside_skip_tag(tag: Tag) -> bool:
    """Check if a tag is nested inside a SKIP_TAGS ancestor."""
    for parent in tag.parents:
        if isinstance(parent, Tag) and parent.name in SKIP_TAGS:
            return True
    return False


def _extract_text_sample(html_content: str, max_chars: int = 1000) -> str:
    """Extract a plain text sample from HTML for language detection."""
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    return text[:max_chars]


# ---------------------------------------------------------------------------
# EPUB analysis (for cost estimation)
# ---------------------------------------------------------------------------

def analyze_epub(input_path: str) -> dict:
    """
    Analyze an EPUB file and return statistics for cost estimation.

    Returns:
        dict with: total_chars, total_words, num_chapters, chapter_names, estimated_tokens
    """
    book = epub.read_epub(input_path, options={'ignore_ncx': True})
    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    total_chars = 0
    total_words = 0
    chapter_names = []
    all_text = ""

    for doc_item in documents:
        try:
            content = doc_item.get_content().decode('utf-8', errors='replace')
        except Exception:
            continue

        text = _extract_text_sample(content, max_chars=999999)
        if text and len(text.strip()) > 10:
            total_chars += len(text)
            total_words += len(text.split())
            all_text += text + " "

            name = doc_item.get_name()
            # Try to extract a readable chapter name from the content
            soup = BeautifulSoup(content, 'html.parser')
            heading = soup.find(['h1', 'h2', 'h3'])
            if heading and heading.get_text(strip=True):
                chapter_names.append(heading.get_text(strip=True)[:80])
            else:
                chapter_names.append(name)

    estimated_tokens = estimate_tokens(all_text[:50000])
    # Scale up if text was truncated
    if len(all_text) > 50000:
        ratio = len(all_text) / 50000
        estimated_tokens = int(estimated_tokens * ratio)

    return {
        "total_chars": total_chars,
        "total_words": total_words,
        "num_chapters": len(chapter_names),
        "chapter_names": chapter_names[:30],  # Limit for API response
        "estimated_tokens": estimated_tokens,
    }


# ---------------------------------------------------------------------------
# Block-level HTML translation
# ---------------------------------------------------------------------------

def _translate_block_html(
    html_str: str,
    source_lang: str,
    target_lang: str,
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    previous_context: str = "",
) -> str:
    """
    Translate the inner HTML of a block element as a single unit,
    preserving inline tags. Passes previous context for coherence.
    """
    if not _is_translatable(html_str):
        return html_str

    translated = translate_text(
        html_str,
        source_lang,
        target_lang,
        provider=provider,
        model=model,
        previous_context=previous_context,
    )
    return translated


# ---------------------------------------------------------------------------
# Chapter translation
# ---------------------------------------------------------------------------

# Elenchi puntati persi nella conversione.
#
# Molti EPUB che girano in rete nascono da una conversione automatica di un PDF
# o di un DOC. Se l'elenco puntato usava un font di simboli (Wingdings e simili)
# la conversione perde il font e lascia la LETTERA che in quel font disegnava il
# pallino. Il risultato, nel libro, e' una riga come:
#
#     r  E' fatta di legno.
#
# Misurato su un libro vero: 354 righe cosi'. Il difetto e' nel file di
# partenza, non nella traduzione, ma lasciarlo passare vorrebbe dire consegnare
# un libro con la stessa bruttura. Qui si rimette il pallino.
#
# Le lettere sotto sono quelle che in Wingdings/Symbol disegnano un punto
# elenco. Si interviene solo quando la riga COMINCIA con quel carattere isolato,
# seguito da spazio, e poi c'e' del testo vero: cosi' una parola che comincia
# per "r" non viene mai toccata.
_LETTERE_PALLINO = {"r", "l", "n", "u", "F", "v", "q", "§", "·", "Ø", "o"}


def _ripara_pallini(soup):
    """Rimette i punti elenco persi dalla conversione. Restituisce quanti."""
    riparati = 0
    for p in soup.find_all(["p", "div", "li"]):
        primo = None
        for f in p.children:
            if isinstance(f, NavigableString) and not str(f).strip():
                continue
            primo = f
            break
        if primo is None or not isinstance(primo, Tag) or primo.name != "span":
            continue
        segno = primo.get_text().strip()
        if segno not in _LETTERE_PALLINO:
            continue
        # dopo il segno ci deve essere davvero del testo, e uno stacco
        resto = p.get_text()[len(segno):]
        if len(resto.strip()) < 8 or not resto[:1].isspace():
            continue
        primo.string = "•"
        riparati += 1
    return riparati


# Tabelle finte, fatte con gli spazi.
#
# La stessa conversione automatica che perde i pallini perde anche le tabelle:
# al posto di <table> lascia un paragrafo con due <span> separati da una fila di
# spazi. L'HTML gli spazi li collassa, quindi le due colonne finiscono
# appiccicate su una riga sola:
#
#     Caratteristiche Benefici
#     E' fatta di legno Si tempera facilmente
#
# Misurato su un libro vero: 560 righe cosi'. Le celle pero' sono ancora due
# <span> distinti, quindi si possono rimettere in colonna.
#
# Per non rovinare un paragrafo normale che per caso contiene due <span>, si
# interviene solo su SERIE di almeno due righe consecutive fatte allo stesso
# modo: una tabella ha piu' di una riga, una frase no.
def _ripara_finte_colonne(soup):
    """Rimette in colonna le tabelle appiattite dalla conversione. Torna quante righe."""
    def e_riga(tag):
        if not isinstance(tag, Tag) or tag.name != "p":
            return 0
        celle = [f for f in tag.children if isinstance(f, Tag)]
        if len(celle) < 2 or any(c.name != "span" for c in celle):
            return 0
        # fra una cella e l'altra ci deve essere solo spazio
        for f in tag.children:
            if isinstance(f, NavigableString) and f.strip():
                return 0
        if any(not c.get_text(strip=True) for c in celle):
            return 0
        return len(celle)

    riparate = 0
    for genitore in soup.find_all(True):
        figli = [f for f in genitore.children if isinstance(f, Tag)]
        i = 0
        while i < len(figli):
            n = e_riga(figli[i])
            if not n:
                i += 1
                continue
            j = i
            while j < len(figli) and e_riga(figli[j]) == n:
                j += 1
            if j - i >= 2:                       # almeno due righe: e' una tabella
                for riga in figli[i:j]:
                    stile = riga.get("style", "")
                    riga["style"] = (stile + ";" if stile else "") + \
                        "display:grid;grid-template-columns:repeat(%d,1fr);gap:0 1.5em" % n
                    riparate += 1
            i = max(j, i + 1)
    return riparate


def _scrivi_conservando_il_pacchetto(originale, destinazione, tradotti, lingua):
    """Riscrive l'EPUB copiando l'originale e sostituendo SOLO il corpo tradotto.

    Perche' esiste.
    --------------
    `epub.write_epub` non riscrive il pacchetto: lo RIGENERA. E rigenerando la
    testa di ogni pagina la perde. Misurato su un libro vero:

        originale   379 pagine, 756 <link rel=stylesheet>, 379 <title>,
                    378 <body class=...>
        rigenerato  379 pagine,   0 <link>,                  0 <title>,
                      0 <body class=...>

    I due file .css restavano dentro il pacchetto, ma nessuna pagina li
    richiamava piu': il libro tradotto perdeva rientri, centrature, margini,
    corpo dei titoli — tutto cio' che non fosse scritto inline. E' questa la
    causa vera dell'"impaginazione strana", non il file di partenza.

    Qui invece si copia l'archivio originale voce per voce, byte per byte, e si
    tocca soltanto il contenuto di <body> delle pagine tradotte. Tutto il resto
    — testa, fogli di stile, font, immagini, indice, OPF — resta identico.

    `tradotti` e' {nome_del_file_nell_epub: soup_tradotta}.
    """
    import zipfile

    def corpo_di(html):
        i = html.lower().find('<body')
        if i < 0:
            return None
        j = html.find('>', i)
        k = html.lower().rfind('</body>')
        return (i, j, k) if j > 0 and k > j else None

    with zipfile.ZipFile(originale) as zin, \
            zipfile.ZipFile(destinazione, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            dati = zin.read(info.filename)
            soup = tradotti.get(info.filename)

            if soup is not None:
                vecchio = dati.decode('utf-8', 'replace')
                punti = corpo_di(vecchio)
                nuovo_corpo = soup.body.decode_contents() if soup.body else str(soup)
                if punti:
                    i, j, k = punti
                    # si sostituisce solo cio' che sta FRA <body ...> e </body>:
                    # gli attributi del body (le classi che reggono i margini)
                    # restano quelli dell'originale.
                    dati = (vecchio[:j + 1] + nuovo_corpo + vecchio[k:]).encode('utf-8')
                else:
                    dati = str(soup).encode('utf-8')

            elif info.filename.lower().endswith('.opf') and lingua:
                testo = dati.decode('utf-8', 'replace')
                nuovo = re.sub(r'(<dc:language[^>]*>)[^<]*(</dc:language>)',
                               r'\g<1>%s\g<2>' % lingua, testo, count=1)
                if nuovo != testo:
                    dati = nuovo.encode('utf-8')

            # `mimetype` deve restare non compresso, altrimenti l'EPUB non e' valido
            zout.writestr(info, dati,
                          zipfile.ZIP_STORED if info.filename == 'mimetype'
                          else zipfile.ZIP_DEFLATED)


def controlla_fedelta(originale, tradotto):
    """Confronta la FORMA del libro tradotto con quella dell'originale.

    Non guarda le parole: guarda l'ossatura. Se il tradotto ha meno capitoli,
    meno paragrafi, meno titoli o meno immagini dell'originale, qualcosa si e'
    perso per strada — e va detto subito, nella scheda del libro, invece di
    farlo scoprire all'utente dieci pagine dopo.

    Restituisce (va_bene, righe_da_scrivere_nel_registro).
    """
    def conta(percorso):
        # ATTENZIONE: l'archivio si legge con zipfile, NON con ebooklib.
        # `get_content()` rigenera la testa della pagina da un modello, quindi
        # riporterebbe zero <link> e zero <title> anche per l'originale: il
        # controllo non scatterebbe mai proprio dove il danno e' totale.
        import zipfile
        n = {'sezioni': 0, 'paragrafi': 0, 'titoli': 0, 'immagini': 0,
             'elenchi': 0, 'caratteri': 0, 'fogli_di_stile': 0, 'intestazioni': 0}
        with zipfile.ZipFile(percorso) as z:
            for nome in z.namelist():
                if not nome.lower().endswith(('.html', '.xhtml', '.htm')):
                    continue
                n['sezioni'] += 1
                testo = z.read(nome).decode('utf-8', 'replace')
                n['fogli_di_stile'] += len(re.findall(r'<link[^>]+stylesheet', testo, re.I))
                n['intestazioni'] += len(re.findall(r'<title', testo, re.I))
                s = BeautifulSoup(testo, 'html.parser')
                n['paragrafi'] += len(s.find_all('p'))
                n['titoli'] += len(s.find_all(['h1', 'h2', 'h3', 'h4']))
                n['immagini'] += len(s.find_all('img'))
                n['elenchi'] += len(s.find_all('li'))
                n['caratteri'] += len(s.get_text(' ', strip=True))
        return n

    try:
        a, b = conta(originale), conta(tradotto)
    except Exception as e:
        return True, ['Controllo di fedelta\' non eseguito: %s' % str(e)[:120]]

    righe = []
    va_bene = True
    # I collegamenti ai fogli di stile sono la cosa piu' importante da guardare:
    # perderli significa perdere rientri, centrature e margini, cioe' tutta
    # l'impaginazione, pur restando il testo al suo posto. Qui basta UNA perdita.
    if a['fogli_di_stile'] and b['fogli_di_stile'] < a['fogli_di_stile']:
        va_bene = False
        righe.append("ATTENZIONE: persi %d collegamenti ai fogli di stile su %d: "
                     "il libro perde rientri, centrature e margini."
                     % (a['fogli_di_stile'] - b['fogli_di_stile'], a['fogli_di_stile']))

    for chiave, etichetta in (('sezioni', 'capitoli'), ('paragrafi', 'paragrafi'),
                              ('titoli', 'titoli'), ('immagini', 'immagini'),
                              ('elenchi', 'voci di elenco')):
        if a[chiave] and b[chiave] < a[chiave]:
            persi = a[chiave] - b[chiave]
            # Uno o due elementi di scarto capitano per differenze di parsing;
            # oltre l'1% e' una perdita vera.
            if persi > max(2, a[chiave] * 0.01):
                va_bene = False
                righe.append('ATTENZIONE: %d %s in meno rispetto all\'originale '
                             '(%d contro %d)' % (persi, etichetta, b[chiave], a[chiave]))

    # Il testo tradotto in italiano e' quasi sempre PIU' lungo dell'inglese.
    # Se e' molto piu' corto, e' segno che qualcosa non e' stato tradotto o e'
    # andato perso.
    if a['caratteri'] and b['caratteri'] < a['caratteri'] * 0.85:
        va_bene = False
        righe.append('ATTENZIONE: il testo tradotto e\' piu\' corto del previsto '
                     '(%d caratteri contro %d)' % (b['caratteri'], a['caratteri']))

    if va_bene:
        righe.append('Impaginazione verificata: %d capitoli, %d paragrafi, %d titoli, '
                     '%d immagini, %d fogli di stile collegati — come l\'originale.'
                     % (b['sezioni'], b['paragrafi'], b['titoli'], b['immagini'],
                        b['fogli_di_stile']))
    return va_bene, righe


def _collect_blocks(soup):
    """Return the leaf block tags whose inner HTML should be translated."""
    blocks = []
    for tag in soup.find_all(True):
        if tag.name in SKIP_TAGS:
            continue
        if _is_inside_skip_tag(tag):
            continue
        if tag.name in BLOCK_TAGS:
            if _has_only_inline_children(tag):
                inner = tag.decode_contents()
                if _is_translatable(inner):
                    blocks.append(tag)

    # Deduplicate: remove blocks that are ancestors of other blocks
    block_set = set(id(b) for b in blocks)
    filtered_blocks = []
    for block in blocks:
        has_child_block = False
        for desc in block.descendants:
            if isinstance(desc, Tag) and id(desc) in block_set and id(desc) != id(block):
                has_child_block = True
                break
        if not has_child_block and _is_translatable(block.decode_contents()):
            filtered_blocks.append(block)

    return filtered_blocks


def _apply_translation(block, translated_html):
    """Replace a tag's inner HTML, keeping the tag itself and its attributes."""
    new_contents = BeautifulSoup(translated_html, 'html.parser')
    block.clear()
    for child in list(new_contents.children):
        block.append(copy.copy(child))


def translate_html_content(
    html_content: str,
    source_lang: str,
    target_lang: str,
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    progress_callback=None,
) -> str:
    """Translate a single chapter's HTML (used standalone / by tests)."""
    soup = BeautifulSoup(html_content, 'html.parser')
    blocks = _collect_blocks(soup)
    if not blocks:
        return str(soup)

    translated = translate_blocks(
        [b.decode_contents() for b in blocks],
        source_lang, target_lang, provider=provider, model=model,
        progress_callback=progress_callback,
    )
    for block, html in zip(blocks, translated):
        _apply_translation(block, html)
    return str(soup)


# ---------------------------------------------------------------------------
# Full EPUB translation
# ---------------------------------------------------------------------------

def translate_epub(
    input_path: str,
    output_path: str,
    source_lang: str = "English",
    target_lang: str = "Italian",
    provider: str = "anthropic",
    model: str = "claude-sonnet-5",
    progress_callback=None,
):
    """
    Translate an entire EPUB file.
    Preserves: images, CSS, fonts, metadata structure, cover, TOC, NCX.
    Translates: all text content in HTML documents.
    """
    book = epub.read_epub(input_path, options={'ignore_ncx': False})

    documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    total_docs = len(documents)

    logger.info(f"EPUB has {total_docs} HTML documents to translate")

    if total_docs == 0:
        logger.warning("No HTML documents found in EPUB")
        epub.write_epub(output_path, book)
        if progress_callback:
            progress_callback(1.0, "Completato! (nessun contenuto testuale trovato)")
        return

    # Si raccolgono i blocchi di TUTTO il libro prima di tradurre: cosi' il
    # motore puo' raggrupparli e mandarli in parallelo senza fermarsi al
    # confine di ogni capitolo. E' qui che si guadagna il grosso del tempo.
    soups = {}
    all_blocks = []          # elementi (doc_idx, tag)
    pallini = 0              # punti elenco rimessi a posto (vedi _ripara_pallini)
    for doc_idx, item in enumerate(documents):
        try:
            content = item.get_content().decode('utf-8', errors='replace')
        except Exception as e:
            logger.warning("Documento %d non decodificabile: %s", doc_idx + 1, e)
            continue
        if not _is_translatable(_extract_text_sample(content)):
            continue
        soup = BeautifulSoup(content, 'html.parser')
        pallini += _ripara_pallini(soup)
        soups[doc_idx] = soup
        for tag in _collect_blocks(soup):
            all_blocks.append((doc_idx, tag))

    total_blocks = len(all_blocks)
    logger.info("EPUB: %d capitoli, %d blocchi da tradurre", total_docs, total_blocks)
    if pallini:
        logger.info("EPUB: %d punti elenco rimessi (erano lettere lasciate dalla "
                    "conversione del file di partenza)", pallini)

    if total_blocks == 0:
        epub.write_epub(output_path, book)
        if progress_callback:
            progress_callback(1.0, "Completato! (nessun testo trovato)")
        return

    def on_progress(done, total):
        if progress_callback:
            # Si lascia l'ultimo 3% alla riscrittura e al salvataggio del file.
            progress_callback(min(done / max(total, 1) * 0.97, 0.97),
                              "Tradotti %d/%d blocchi" % (done, total))

    translated = translate_blocks(
        [tag.decode_contents() for _, tag in all_blocks],
        source_lang, target_lang, provider=provider, model=model,
        progress_callback=on_progress,
    )

    if progress_callback:
        progress_callback(0.98, "Ricostruzione capitoli...")

    for (doc_idx, tag), html in zip(all_blocks, translated):
        try:
            _apply_translation(tag, html)
        except Exception as e:
            logger.error("Blocco non riscritto nel capitolo %d: %s", doc_idx + 1, e)

    # Le tabelle finte si rimettono in colonna DOPO la traduzione: prima le celle
    # sono separate da file di spazi che il modello puo' restituire diversamente.
    colonne = 0
    for soup in soups.values():
        colonne += _ripara_finte_colonne(soup)
    if colonne:
        logger.info("EPUB: %d righe rimesse in colonna (tabelle che la conversione "
                    "del file di partenza aveva appiattito)", colonne)

    if progress_callback:
        progress_callback(0.99, "Salvataggio EPUB...")

    lang_code = target_lang[:2].lower() if len(target_lang) > 2 else target_lang.lower()

    # Si riscrive copiando l'archivio originale invece di rigenerarlo con
    # ebooklib: rigenerandolo si perdevano i collegamenti ai fogli di stile e con
    # essi tutta l'impaginazione del libro. Vedi
    # _scrivi_conservando_il_pacchetto per la misura.
    import zipfile as _zip
    with _zip.ZipFile(input_path) as _z:
        nomi = _z.namelist()

    def nome_nell_archivio(item):
        atteso = item.get_name()
        if atteso in nomi:
            return atteso
        coda = atteso.split('/')[-1]
        for n in nomi:
            if n.split('/')[-1] == coda:
                return n
        return None

    tradotti = {}
    mancanti = 0
    for doc_idx, soup in soups.items():
        n = nome_nell_archivio(documents[doc_idx])
        if n:
            tradotti[n] = soup
        else:
            mancanti += 1
    if mancanti:
        logger.warning("EPUB: %d capitoli non ritrovati nell'archivio originale", mancanti)

    _scrivi_conservando_il_pacchetto(input_path, output_path, tradotti, lang_code)
    logger.info(f"Translated EPUB saved to: {output_path}")

    if progress_callback:
        progress_callback(1.0, "Completato!")
