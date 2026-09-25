"""Il testo tradotto FINORA, mentre la traduzione e' ancora in corso.

Perche' si puo' fare senza toccare il traduttore
------------------------------------------------
Il traduttore salva ogni blocco appena tradotto in `tcache`, con chiave
`cache_key(testo, modello, lingua_origine, lingua_destinazione)`. Quella cache
esiste per non ripagare l'AI dopo un crash — ma ha un secondo uso, gratuito:
**dice cosa e' gia' pronto**. Non serve una pipeline nuova, serve saper leggere
quella che c'e'.

La regola che rende tutto questo possibile o inutile
----------------------------------------------------
I blocchi vanno ricostruiti **nello stesso identico ordine e con lo stesso
identico contenuto** del traduttore, altrimenti le chiavi non combaciano e la
cache sembra vuota anche quando e' piena. Per questo qui non si reimplementa
niente: si chiamano le STESSE funzioni di `epub_handler`. Se un giorno cambia
il modo di raccogliere i blocchi, cambia da sola anche l'anteprima.
"""
import os
import logging

logger = logging.getLogger(__name__)


def _blocchi_epub(percorso):
    """I blocchi del libro, nell'ordine esatto in cui il traduttore li manda.

    Usa le funzioni di `epub_handler`, non una copia: una copia divergerebbe
    silenziosamente e l'anteprima smetterebbe di trovare la cache.
    """
    import warnings
    warnings.filterwarnings("ignore")
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    import epub_handler as eh

    libro = epub.read_epub(percorso, options={"ignore_ncx": True})
    documenti = list(libro.get_items_of_type(ebooklib.ITEM_DOCUMENT))
    fuori = []
    for item in documenti:
        try:
            contenuto = item.get_content().decode("utf-8", errors="replace")
        except Exception:
            continue
        if not eh._is_translatable(eh._extract_text_sample(contenuto)):
            continue
        soup = BeautifulSoup(contenuto, "html.parser")
        eh._ripara_pallini(soup)
        for tag in eh._collect_blocks(soup):
            testo = tag.get_text(" ", strip=True) if hasattr(tag, "get_text") else str(tag)
            if testo:
                fuori.append(testo)
    return fuori


def testo_tradotto_finora(job, store, massimo_blocchi=None):
    """Restituisce (blocchi_tradotti, totale, tutti_pronti).

    I blocchi tornano in ordine e si fermano al primo NON ancora tradotto: chi
    ascolta va avanti in linea retta, e un buco in mezzo sarebbe peggio di una
    coda piu' corta.
    """
    # Il primo percorso che ESISTE, non il primo non vuoto: sul server gli
    # originali caricati vengono cancellati dopo la traduzione (sono file di
    # passaggio), quindi `input_path` e' spesso un nome che non punta a nulla.
    # Sceglierlo per "verita'" invece che per esistenza faceva sembrare vuota
    # una cache piena.
    percorso = next((p for p in (job.get("input_path"), job.get("output_path"))
                     if p and os.path.exists(p)), None)
    if not percorso:
        return [], 0, False
    if (job.get("file_type") or "").lower() != "epub":
        return [], 0, False            # PDF e DOCX: da fare, vedi nota in fondo

    try:
        sorgente = _blocchi_epub(percorso)
    except Exception:
        logger.exception("non riesco a rileggere i blocchi del libro")
        return [], 0, False

    modello = job.get("model") or ""
    lingua_da = job.get("source_lang") or ""
    lingua_a = job.get("target_lang") or ""
    pronti = []
    for b in sorgente:
        if not b.strip():
            continue
        chiave = store.cache_key(b, modello, lingua_da, lingua_a)
        tradotto = store.cache_get(job["id"], chiave)
        if tradotto is None:
            break                      # da qui in poi non c'e' ancora niente
        pronti.append(tradotto)
        if massimo_blocchi and len(pronti) >= massimo_blocchi:
            break
    return pronti, len(sorgente), len(pronti) >= len(sorgente)


def frasi_pronte(job, store, tts, massimo_blocchi=None):
    """Le frasi gia' ascoltabili, pronte da mandare alla voce."""
    blocchi, totale, complete = testo_tradotto_finora(job, store, massimo_blocchi)
    if not blocchi:
        return [], 0, 0, complete
    frasi = tts.dividi_in_frasi("\n".join(blocchi))
    return frasi, len(blocchi), totale, complete


# Nota su PDF e DOCX: la stessa cosa si fa allo stesso modo, ma i loro
# estrattori lavorano per pagina e per paragrafo con una struttura diversa.
# Vanno aggiunti quando servira'; l'EPUB copre i libri veri della libreria.
