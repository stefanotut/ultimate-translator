"""Ponte fra la libreria a cartelle e tag e il portale che gira in produzione.

Perche' esiste
--------------
Il sistema a cartelle e tag (library.py, library_db.py, tagger.py,
book_metadata.py, static/library.*) e' nato su un'altra sessione, a partire
da una copia vecchia del progetto: aveva una sua libreria, riconosciuta da un
codice nel cookie, e prendeva l'indirizzo /libreria. Il portale vero ha il
login e una libreria sua (la tabella dei job) con lettore, ascolto, appunti e
traduzione di pagina. Regola dell'utente: le funzioni che ci sono NON si
tolgono e NON si cambiano.

Quindi le cartelle stanno ACCANTO, non al posto:
- pagina nuova /libreria/cartelle; /libreria resta com'era;
- e' la libreria dell'account (login). Il "codice libreria" apre solo le tre
  chiamate che usa import_books.py, che l'OTP non lo puo' fare;
- ogni libro caricato nelle cartelle diventa anche un libro della libreria di
  sempre (un job "importato", come quelli di /api/importa): si legge, si
  ascolta, si annota e compare in /libreria;
- i libri che c'erano gia' compaiono anche nelle cartelle, senza copie: il
  libro delle cartelle punta allo stesso file;
- eliminare DEFINITIVAMENTE un libro dal cestino delle cartelle lo elimina
  anche dalla libreria di sempre, con gli stessi passi di "Elimina". Il
  contrario (eliminato da /libreria) lo sistema la sincronizzazione.

Cosa NON fa mai
---------------
- non elimina un libro della libreria di sempre per un'assenza o un errore:
  solo per un gesto esplicito (eliminazione definitiva dal cestino, o il
  cestino che si svuota da solo dopo 30 giorni);
- non accoda audiolibri: con cento libri caricati insieme consumerebbe il
  tetto mensile della voce su libri mai ascoltati. L'ascolto resta su
  richiesta, frase per frase, come per ogni altro libro.
"""
import logging
import os
import threading
import uuid

from flask import g, request

import auth
import covers
import store

logger = logging.getLogger(__name__)

PAGINA = '/libreria/cartelle'

# Le sole chiamate aperte al codice libreria (senza login): quelle di import_books.py.
CHIAMATE_COL_CODICE = {
    ('POST', '/api/library/key'),
    ('POST', '/api/library/folders/tree'),
    ('POST', '/api/library/books'),
}

# Il riconoscitore della libreria scrive la lingua per nome ("English"), i
# metadati EPUB per codice ("en"): si accettano tutti e due.
LINGUE = {'it': 'Italiano', 'en': 'Inglese', 'fr': 'Francese', 'de': 'Tedesco',
          'es': 'Spagnolo', 'pt': 'Portoghese', 'nl': 'Olandese',
          'italian': 'Italiano', 'italiano': 'Italiano', 'english': 'Inglese', 'inglese': 'Inglese',
          'french': 'Francese', 'francese': 'Francese', 'german': 'Tedesco', 'tedesco': 'Tedesco',
          'spanish': 'Spagnolo', 'spagnolo': 'Spagnolo', 'portuguese': 'Portoghese',
          'portoghese': 'Portoghese', 'dutch': 'Olandese', 'olandese': 'Olandese'}


def _lingua(valore):
    v = (valore or '').strip().lower()
    return LINGUE.get(v) or LINGUE.get(v[:2]) or 'Italiano'

_cfg = {}
_collega = threading.Lock()       # caricamento e sincronizzazione non collegano mai due volte lo stesso libro
_sync_in_corso = threading.Lock()


def init(app, base_dir, prepara_pdf, dimentica_pagine, data_dir=None, sync_all_avvio=None):
    """Monta la libreria a cartelle sul portale. Da chiamare una volta, all'avvio."""
    import library
    import library_db
    library.PAGE_PATH = PAGINA          # /libreria resta la pagina di sempre

    library_db.init(data_dir or os.environ.get('LIBRARY_DATA_DIR')
                    or os.path.join(base_dir, 'data', 'libreria'))
    _cfg.update(prepara_pdf=prepara_pdf, dimentica_pagine=dimentica_pagine)

    library.IDENTITY = _identita
    library.PAGE_ALLOWED = lambda: bool(auth.current_user())
    library.AFTER_IMPORT = _dopo_caricamento
    library.ON_MAINTENANCE = _sincronizza_in_sottofondo
    library_db.on_books_purged = _elimina_libri_del_portale
    app.register_blueprint(library.bp)
    pagine = [r.rule for r in app.url_map.iter_rules() if r.endpoint == 'library.library_page']
    if pagine != [PAGINA]:
        raise RuntimeError('Pagina delle cartelle registrata su %r invece di %s' % (pagine, PAGINA))

    # I libri che ci sono gia' compaiono nelle cartelle senza aspettare la prima visita.
    if sync_all_avvio is None:
        sync_all_avvio = os.environ.get('LIBRARY_SYNC_AT_START', '1') != '0'
    if sync_all_avvio:
        for email in sorted(auth.allowed_emails()):
            try:
                _sincronizza_in_sottofondo(library_db.library_for_owner(email)['id'])
            except Exception:
                logger.exception('Sincronizzazione iniziale delle cartelle non partita per %s', email)
            break          # una alla volta: le altre alla loro prima visita
    logger.info('Libreria a cartelle pronta su %s', library.PAGE_PATH)


# ---------------------------------------------------------------------------
# Chi e' la libreria
# ---------------------------------------------------------------------------

def _identita(create):
    import library
    import library_db
    email = auth.current_user()
    if email:
        g.library_via_login = True
        return library_db.library_for_owner(email, create=True)
    # Senza login: solo lo script di importazione, e solo con il codice giusto
    # di una libreria che appartiene a un account ancora autorizzato.
    if (request.method, request.path) not in CHIAMATE_COL_CODICE:
        return None
    code = request.cookies.get(library.COOKIE_NAME)
    if not code:
        return None
    lib = library_db.get_library_by_code(code)
    if lib and (lib.get('owner') or '').lower() in auth.allowed_emails():
        g.library_via_login = False
        return lib
    return None


# ---------------------------------------------------------------------------
# Libro nuovo nelle cartelle -> libro della libreria di sempre
# ---------------------------------------------------------------------------

def _crea_libro_del_portale(owner, percorso, nome_file, ext, lingua_codice):
    """Gli stessi passi di /api/importa, sul file che le cartelle hanno gia' salvato."""
    job_id = str(uuid.uuid4())
    if ext == 'pdf':
        # prima di renderlo leggibile: il lettore lo apre a pezzi, e il file
        # riscritto dopo lo manderebbe in tilt
        _cfg['prepara_pdf'](percorso)
    lingua = _lingua(lingua_codice)
    store.create_job(
        id=job_id, owner=owner,
        original_filename=nome_file,
        input_path=None,
        output_path=percorso,
        output_filename=nome_file,
        file_type=ext,
        source_lang=lingua, target_lang=lingua,
        provider='importato', model='importato',
        status='completed', progress=1.0, status_text='Importato',
    )
    store.add_log(job_id, 'Libro caricato nelle cartelle della libreria (nessuna traduzione).', 'success')
    try:
        covers.genera(job_id, percorso, ext, nome_file)
    except Exception as e:
        logger.warning('Copertina non generata per %s: %s', nome_file, str(e)[:80])
    return job_id


def _dopo_caricamento(library_id, book_id):
    import library_db
    owner = library_db.library_owner(library_id)
    if not owner:
        return
    with _collega:
        rec = library_db.get_book_record(library_id, book_id)
        if not rec or rec.get('job_id'):
            return
        percorso = library_db.abs_path(rec['file_path'])
        job_id = _crea_libro_del_portale(owner, percorso, rec['original_filename'],
                                         rec['file_type'], rec.get('language'))
        library_db.set_book_job(library_id, book_id, job_id)
    logger.info('Cartelle: libro %s collegato alla libreria come %s', book_id[:8], job_id[:8])


# ---------------------------------------------------------------------------
# Sincronizzazione (al massimo una al minuto, in sottofondo)
# ---------------------------------------------------------------------------

def _sincronizza_in_sottofondo(library_id):
    if not _sync_in_corso.acquire(blocking=False):
        return

    def lavoro():
        try:
            sincronizza(library_id)
        except Exception:
            logger.exception('Sincronizzazione delle cartelle fallita')
        finally:
            _sync_in_corso.release()

    threading.Thread(target=lavoro, name='cartelle-sync', daemon=True).start()


def sincronizza(library_id):
    """Allinea le cartelle alla libreria di sempre. Ritorna (indicizzati, sistemati)."""
    import library
    import library_db
    owner = library_db.library_owner(library_id)
    if not owner:
        return 0, 0
    indicizzati = sistemati = 0

    # 1) libri della libreria di sempre che le cartelle non conoscono ancora
    for job in store.list_jobs(owner, limit=100000, status='completed'):
        ext = (job.get('file_type') or '').lower()
        percorso = job.get('output_path')
        if ext not in library.ALLOWED_EXTENSIONS or not percorso or not os.path.exists(percorso):
            continue
        with _collega:
            legami = library_db.job_links(library_id)
            if any(l['job_id'] == job['id'] for l in legami):
                continue
            reale = os.path.realpath(percorso)
            if any(l['file_path'] and os.path.realpath(library_db.abs_path(l['file_path'])) == reale
                   for l in legami):
                continue       # e' un libro delle cartelle che si sta collegando proprio ora
            try:
                library.adopt_existing_file(library_id, percorso,
                                            job.get('original_filename') or os.path.basename(percorso),
                                            ext, job['id'])
                indicizzati += 1
            except Exception:
                logger.exception('Libro %s non indicizzato nelle cartelle', job['id'][:8])

    # 2) libri delle cartelle il cui libro e' stato eliminato da /libreria
    for l in library_db.job_links(library_id):
        if not l['job_id']:
            continue
        try:
            if store.get_job(l['job_id'], owner=owner) is not None:
                continue
        except Exception:
            continue           # un errore non e' un'assenza: non si tocca niente
        percorso = library_db.abs_path(l['file_path'])
        if percorso and os.path.exists(percorso):
            # Strano (il file c'e' ancora): nel cestino, visibile e recuperabile.
            if not l['deleted_at']:
                library_db.trash_items(library_id, book_ids=[l['id']])
                sistemati += 1
        else:
            library_db.purge_book_now(library_id, l['id'])
            sistemati += 1
    if indicizzati or sistemati:
        logger.info('Cartelle sincronizzate: %d libri indicizzati, %d sistemati', indicizzati, sistemati)
    return indicizzati, sistemati


# ---------------------------------------------------------------------------
# Eliminazione definitiva dal cestino delle cartelle
# ---------------------------------------------------------------------------

def _elimina_libri_del_portale(library_id, job_ids):
    """Gli stessi passi di DELETE /api/libro/<id>, per i libri collegati."""
    import library_db
    owner = library_db.library_owner(library_id)
    if not owner:
        return
    for job_id in job_ids:
        job = store.get_job(job_id, owner=owner)
        if not job:
            continue
        if job['status'] in ('running', 'pending'):
            logger.warning('Libro %s in traduzione: non eliminato dalla libreria', job_id[:8])
            continue
        for percorso in (job['output_path'], job['input_path']):
            if percorso and os.path.exists(percorso):
                try:
                    os.remove(percorso)
                except OSError as e:
                    logger.warning('Non rimosso %s: %s', percorso, e)
        try:
            copertina = covers.percorso_esistente(job_id)
            if copertina and os.path.exists(copertina):
                os.remove(copertina)
        except Exception:
            pass
        try:
            _cfg['dimentica_pagine'](job_id)
        except Exception:
            logger.exception('Pagine tradotte non rimosse per %s', job_id[:8])
        store.delete_job(job_id, owner)
        logger.info('Libro eliminato dal cestino delle cartelle: %s (%s)',
                    job['original_filename'], job_id)
