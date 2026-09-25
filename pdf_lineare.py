"""
PDF PRONTO PER IL LETTORE — i dizionari delle pagine in testa al file.

PERCHE'. Il lettore web (pdf.js 6.2) all'apertura, prima ancora di risolvere
`getDocument`, controlla l'ultima pagina (`PDFDocument.checkLastPage` ->
`Catalog.getPageDict(numPages-1)`) e in quel percorso lancia `xref.fetchAsync`
su TUTTI i figli del nodo /Pages radice. Ogni dizionario di pagina che sta in
un pezzo diverso del file costa una richiesta Range da `rangeChunkSize` byte.
Non usa mai le tabelle di hint della linearizzazione.

La linearizzazione (qpdf, "fast web view") e' quindi CONTROPRODUCENTE per
pdf.js: per specifica mette il dizionario di ogni pagina nella sezione della
pagina, cioe' li sparge per tutto il file. Misurato (Node 22, pdfjs-dist
6.2.108, chunk 256 KB, server Range locale) sul libro scansionato da 127 MB:
    linearizzato:  192 richieste, 47,8 MB prima che si veda la prima pagina
    riscritto:       3 richieste,  0,6 MB per aprire, 1,3 MB con pagina 1 e 100
e sul PDF tradotto da 12 MB: linearizzato 48 richieste = 11,8 MB (tutto il
file); riscritto 3 richieste, 0,6 MB. I dizionari delle pagine devono stare
tutti insieme, in testa: e' quello che fa qpdf quando NON linearizza (scrive
gli oggetti in ordine di visita: catalogo, /Pages, tutte le pagine, poi i
contenuti), con gli object stream disattivati perche' cosi' i dizionari sono
in chiaro e il controllo qui sotto li puo' vedere.

STRUMENTO. `pikepdf`, che porta qpdf dentro il venv. Se manca il portale
funziona lo stesso, solo con l'apertura lenta: qui si logga e si ritorna False.

MISURATO sul Mac: riscrittura del 127 MB in 0,2 s, picco RSS 36 MB (pikepdf
copia gli stream senza ricomprimerli); il grosso della memoria resta il render
di controllo della pagina 1.

SICUREZZA. Si scrive SEMPRE in `<file>.lineare.tmp`, si verifica il risultato
(si apre, stesse pagine, dizionari in testa, la pagina 1 si disegna, non e'
cresciuto oltre il 10%) e solo allora `os.replace` lo mette al posto
dell'originale, in un colpo solo. Qualunque cosa vada storta l'originale resta
com'era e chi chiama riceve False, mai un'eccezione.

USO da riga di comando (e' cosi' che lo lancia app.py, in un processo a parte):
    python pdf_lineare.py <pdf> [<pdf>...]
stampa una riga per file (prima/dopo, MB, secondi, picco RSS) ed esce con 0 se
tutti i file sono pronti alla fine, 1 altrimenti.
"""

import logging
import os
import re
import shutil
import stat
import sys
import time

import pymupdf

logger = logging.getLogger(__name__)

SUFFISSO_TMP = '.lineare.tmp'
# Oltre questo il file non e' "lo stesso libro" un po' riordinato: qualcosa e'
# andato storto (stream espansi, oggetti duplicati) e non lo si accetta.
# La soglia assoluta serve ai file piccoli: gli object stream sciolti costano
# pochi KB fissi, che su un PDF di 5 pagine sono gia' il 15% senza che nulla
# sia andato storto
CRESCITA_MAX = 0.10
CRESCITA_MIN_BYTE = 512 * 1024
# Un .tmp piu' vecchio di cosi' e' il resto di un processo morto, non un lavoro
# in corso: pikepdf scrive di continuo, quindi un tmp vivo e' sempre fresco
TMP_STANTIO_SECONDI = 30 * 60
# Spazio libero richiesto: il tmp e' grande quanto l'originale, piu' un margine
MARGINE_DISCO = 1.2
# Deve combaciare con `rangeChunkSize` in templates/lettore.html: e' la
# grandezza del pezzo che pdf.js scarica per ogni dizionario di pagina
CHUNK_LETTORE = 256 * 1024
# I dizionari delle pagine vanno cercati solo qui, in testa: piu' in la' il
# file non e' pronto comunque, inutile leggere 127 MB per dirlo
TESTA_BYTE = 8 * 1024 * 1024
# Pronto = tutte le pagine nella testa e in al massimo questi pezzi distinti
# (qpdf ne usa 1; il margine copre cataloghi con indici e metadati grossi)
CHUNK_MAX = 4

_OGGETTO = re.compile(rb'(?:^|[\r\n])(\d+) \d+ obj\s*<<')


def _pezzi_dei_dizionari(percorso):
    """Dove stanno i dizionari delle pagine. Ritorna (pezzi_distinti,
    pagine_fuori_testa, pagine), oppure None se il file non si apre."""
    try:
        with pymupdf.open(percorso) as doc:
            xrefs = [pagina.xref for pagina in doc]
        with open(percorso, 'rb') as f:
            testa = f.read(TESTA_BYTE)
    except Exception as e:
        logger.warning('Non riesco a leggere %s: %s', os.path.basename(percorso), str(e)[:120])
        return None
    offset = {}
    for m in _OGGETTO.finditer(testa):
        offset.setdefault(int(m.group(1)), m.start() + 1)
    pezzi = set()
    fuori = 0
    for xref in xrefs:
        if xref in offset:
            pezzi.add(offset[xref] // CHUNK_LETTORE)
        else:
            fuori += 1        # oltre la testa, o dentro un object stream
    return len(pezzi), fuori, len(xrefs)


def e_pronto(percorso):
    """True se i dizionari delle pagine sono in testa al file, cioe' pdf.js lo
    apre con poche richieste. False anche se non si apre."""
    esito = _pezzi_dei_dizionari(percorso)
    return bool(esito) and esito[1] == 0 and esito[0] <= CHUNK_MAX


def _verifica(tmp, pagine_attese, dimensione_originale):
    """Controlla il file riscritto prima di fidarsene. Ritorna il motivo del
    rifiuto come stringa, oppure None se va tutto bene."""
    dimensione = os.path.getsize(tmp)
    limite = max(dimensione_originale * (1 + CRESCITA_MAX), dimensione_originale + CRESCITA_MIN_BYTE)
    if dimensione > limite:
        return 'cresciuto di %d KB (%.0f%%), limite %d KB' % (
            (dimensione - dimensione_originale) // 1024,
            (dimensione - dimensione_originale) * 100.0 / dimensione_originale,
            (limite - dimensione_originale) // 1024)
    try:
        with pymupdf.open(tmp) as doc:
            if doc.page_count != pagine_attese:
                return 'pagine %d invece di %d' % (doc.page_count, pagine_attese)
            # Basta che si disegni senza eccezioni: la risoluzione e' minima
            # perche' conta il decode, non i pixel (vedi le misure in testa)
            doc[0].get_pixmap(dpi=24)
    except Exception as e:
        return 'il risultato non si apre o non si disegna: %s' % str(e)[:120]
    esito = _pezzi_dei_dizionari(tmp)
    if not esito:
        return 'il risultato non si lascia analizzare'
    pezzi, fuori, _ = esito
    if fuori or pezzi > CHUNK_MAX:
        return 'pikepdf ha scritto i dizionari delle pagine sparsi (%d fuori testa, %d pezzi)' % (fuori, pezzi)
    return None


def prepara(percorso):
    """Sostituisce `percorso` con una copia riscritta da qpdf, non linearizzata
    e senza object stream, in modo atomico.

    Ritorna True se alla fine il file e' pronto (anche se lo era gia'), False
    in ogni altro caso: l'originale resta intatto e il motivo va nel log. Non
    solleva mai eccezioni verso chi chiama.
    """
    nome = os.path.basename(percorso or '')
    try:
        if not percorso or not os.path.isfile(percorso):
            logger.warning('Preparazione: %s non esiste', nome)
            return False
        if e_pronto(percorso):
            return True

        try:
            import pikepdf
        except ImportError:
            logger.warning('Preparazione: pikepdf non installato, %s resta com\'e\'', nome)
            return False

        with pymupdf.open(percorso) as doc:
            pagine = doc.page_count
        if pagine == 0:
            logger.warning('Preparazione: %s non ha pagine', nome)
            return False

        st = os.stat(percorso)
        tmp = percorso + SUFFISSO_TMP
        if os.path.exists(tmp):
            if time.time() - os.path.getmtime(tmp) < TMP_STANTIO_SECONDI:
                logger.warning('Preparazione: %s e\' gia\' in lavorazione', nome)
                return False
            logger.info('Preparazione: tolgo un .tmp stantio di %s', nome)
            os.remove(tmp)

        libero = shutil.disk_usage(os.path.dirname(os.path.abspath(percorso))).free
        if libero < st.st_size * MARGINE_DISCO:
            logger.warning('Preparazione: disco insufficiente per %s (%d MB liberi)',
                           nome, libero // (1024 * 1024))
            return False

        try:
            # O_EXCL: la creazione del tmp e' anche il lucchetto fra processi.
            # Il file resta vuoto: pikepdf lo riapre per nome e lo tronca
            os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            logger.warning('Preparazione: %s e\' gia\' in lavorazione', nome)
            return False

        try:
            with pikepdf.open(percorso) as pdf:
                # NON linearize=True: sparge i dizionari delle pagine (vedi in
                # testa). Object stream sciolti: dizionari in chiaro, che
                # `_pezzi_dei_dizionari` sa vedere; costa +0..1% di dimensione
                pdf.save(tmp, linearize=False,
                         object_stream_mode=pikepdf.ObjectStreamMode.disable)
            motivo = _verifica(tmp, pagine, st.st_size)
            if motivo:
                logger.warning('Preparazione di %s rifiutata: %s', nome, motivo)
                return False
            # Stessi permessi dell'originale; la data invece DEVE essere nuova:
            # send_file(conditional=True) firma ETag e Last-Modified con la
            # data, e un browser con i Range vecchi in cache mischierebbe byte
            # di due file diversi
            os.chmod(tmp, stat.S_IMODE(st.st_mode))
            os.replace(tmp, percorso)
            logger.info('Preparato %s (%.1f MB)', nome, os.path.getsize(percorso) / 1e6)
            return True
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception as e:
        logger.warning('Preparazione di %s fallita: %s: %s', nome, type(e).__name__, str(e)[:160])
        return False


# Nomi vecchi, per chi importa ancora il modulo con il lessico della linearizzazione
linearizza = prepara
e_lineare = e_pronto


def _picco_rss_mb():
    """Picco di memoria del processo. Linux lo da' in KB, macOS in byte."""
    import resource
    picco = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return picco / (1024 * 1024) if sys.platform == 'darwin' else picco / 1024


def _descrivi(percorso):
    esito = _pezzi_dei_dizionari(percorso)
    if not esito:
        return 'illeggibile'
    pezzi, fuori, pagine = esito
    return 'dizionari in %d pezzi, %d pagine su %d fuori testa' % (pezzi, fuori, pagine)


def _cli(argomenti):
    if not argomenti:
        print('uso: python pdf_lineare.py <pdf> [<pdf>...]', file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(levelname)s %(message)s')
    falliti = 0
    for percorso in argomenti:
        nome = os.path.basename(percorso)
        if not os.path.isfile(percorso):
            print('%s: NON ESISTE' % nome)
            falliti += 1
            continue
        prima = _descrivi(percorso)
        prima_pronto = e_pronto(percorso)
        prima_mb = os.path.getsize(percorso) / 1e6
        t0 = time.perf_counter()
        ok = prepara(percorso)
        secondi = time.perf_counter() - t0
        dopo_mb = os.path.getsize(percorso) / 1e6
        if ok and prima_pronto:
            print('%s: gia\' pronto (%s, %.1f MB), %.1f s, picco RSS %.0f MB'
                  % (nome, prima, prima_mb, secondi, _picco_rss_mb()))
        elif ok:
            print('%s: prima %s, %.1f MB -> dopo %s, %.1f MB (%+.1f%%) in %.1f s, picco RSS %.0f MB'
                  % (nome, prima, prima_mb, _descrivi(percorso), dopo_mb,
                     (dopo_mb - prima_mb) * 100.0 / prima_mb, secondi, _picco_rss_mb()))
        else:
            falliti += 1
            print('%s: NON preparato (originale intatto, %s, %.1f MB), %.1f s, picco RSS %.0f MB'
                  % (nome, prima, dopo_mb, secondi, _picco_rss_mb()))
    if len(argomenti) > 1:
        print('%d su %d pronti' % (len(argomenti) - falliti, len(argomenti)))
    return 1 if falliti else 0


if __name__ == '__main__':
    sys.exit(_cli(sys.argv[1:]))
