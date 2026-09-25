"""
ULTIMATE TRANSLATOR - Estrazione delle copertine

Una copertina per ogni libro, come in una libreria vera: dall'EPUB si prende
l'immagine dichiarata nei metadati, dal PDF si rende la prima pagina, e per i
formati senza copertina se ne disegna una col titolo.
"""

import io
import os
import logging

import fitz

logger = logging.getLogger(__name__)

COVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "covers")
LARGHEZZA = 400          # px: basta per la griglia anche su schermi retina


def _percorso(job_id):
    return os.path.join(COVER_DIR, "%s.jpg" % job_id)


def _salva(pix, job_id):
    os.makedirs(COVER_DIR, exist_ok=True)
    if pix.width > LARGHEZZA:
        fattore = LARGHEZZA / pix.width
        pix = fitz.Pixmap(pix, 0)              # via canale alpha, se c'e'
        matrice = fitz.Matrix(fattore, fattore)
        # Pixmap non ridimensiona da solo: si passa da un documento temporaneo.
        doc = fitz.open()
        pagina = doc.new_page(width=pix.width, height=pix.height)
        pagina.insert_image(pagina.rect, pixmap=pix)
        pix = pagina.get_pixmap(matrix=matrice)
        doc.close()
    if pix.alpha:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(_percorso(job_id), "jpeg", jpg_quality=82)
    return _percorso(job_id)


def _da_pdf(path, job_id):
    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            return None
        pix = doc[0].get_pixmap(dpi=100)
        return _salva(pix, job_id)
    finally:
        doc.close()


def _immagine_copertina_epub(book):
    """L'immagine di copertina secondo i metadati EPUB, con più ripieghi."""
    import ebooklib

    # 1) proprieta' standard EPUB 3
    for item in book.get_items():
        if "cover-image" in (getattr(item, "properties", "") or ""):
            return item.get_content()

    # 2) <meta name="cover" content="id-immagine"> di EPUB 2
    for _tipo, valori in (book.get_metadata("OPF", "cover") or [(None, {})]):
        rif = (valori or {}).get("content")
        if rif:
            item = book.get_item_with_id(rif)
            if item is not None:
                return item.get_content()

    # 3) un'immagine il cui nome contiene "cover"
    immagini = list(book.get_items_of_type(ebooklib.ITEM_IMAGE))
    for item in immagini:
        if "cover" in item.get_name().lower():
            return item.get_content()

    # 4) la prima immagine del libro
    return immagini[0].get_content() if immagini else None


def _da_epub(path, job_id):
    from ebooklib import epub as epub_lib

    book = epub_lib.read_epub(path, options={"ignore_ncx": True})
    dati = _immagine_copertina_epub(book)
    if not dati:
        return None
    try:
        return _salva(fitz.Pixmap(io.BytesIO(dati)), job_id)
    except Exception as e:
        logger.warning("Immagine di copertina illeggibile: %s", str(e)[:80])
        return None


def _segnaposto(titolo, job_id):
    """Copertina disegnata: per DOCX/TXT, che una copertina non ce l'hanno."""
    doc = fitz.open()
    pagina = doc.new_page(width=400, height=600)
    pagina.draw_rect(pagina.rect, color=None, fill=(0.09, 0.05, 0.17))
    pagina.draw_rect(fitz.Rect(0, 0, 400, 8), color=None, fill=(0.49, 0.29, 0.92))
    nome = os.path.splitext(os.path.basename(titolo or "Libro"))[0]
    try:
        pagina.insert_htmlbox(
            fitz.Rect(34, 90, 366, 500),
            '<div style="font-family:Georgia,serif;font-size:27px;line-height:1.3;'
            'color:#F1EEFF;text-align:left">%s</div>'
            % (nome[:120].replace("&", "&amp;").replace("<", "&lt;")),
            scale_low=0.4)
    except Exception:
        pass
    pix = pagina.get_pixmap(dpi=96)
    doc.close()
    return _salva(pix, job_id)


def genera(job_id, path, file_type, titolo=None, forza=False):
    """Crea la copertina se manca. Ritorna il percorso, o None."""
    esistente = _percorso(job_id)
    if os.path.exists(esistente) and not forza:
        return esistente
    if not path or not os.path.exists(path):
        return None
    try:
        if file_type == "pdf":
            out = _da_pdf(path, job_id)
        elif file_type == "epub":
            out = _da_epub(path, job_id)
        else:
            out = None
        return out or _segnaposto(titolo, job_id)
    except Exception as e:
        logger.warning("Copertina non generata per %s: %s", job_id[:8], str(e)[:100])
        try:
            return _segnaposto(titolo, job_id)
        except Exception:
            return None


def percorso_esistente(job_id):
    p = _percorso(job_id)
    return p if os.path.exists(p) else None
