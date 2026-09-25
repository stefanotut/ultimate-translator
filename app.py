"""
ULTIMATE TRANSLATOR - Main Application

Traduttore AI per libri (EPUB, PDF, DOCX, TXT/Markdown) che restituisce il file
nello stesso formato e con la stessa impaginazione dell'originale.

Architettura:
  - lo stato dei job vive in SQLite (store.py), non in memoria: piu' worker
    vedono gli stessi job e un riavvio non perde niente;
  - i worker riprendono i job interrotti riusando la cache per blocco, quindi
    una ripresa non ripaga l'AI per il lavoro gia' fatto;
  - l'accesso e' riservato agli indirizzi in ALLOWED_EMAILS via OTP email;
  - un thread di manutenzione cancella file e record scaduti.
"""

import os
import re
import sys
import uuid
import time
import atexit
import shutil
import collections
import signal
import logging
import threading

from flask import (Flask, render_template, request, jsonify, send_file,
                   redirect, make_response)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import store
import auth
import covers
import tts

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# I file statici (lettore, librerie) restano in cache ma vengono rivalidati:
# il valore predefinito di Flask e' 12 ore, abbastanza per lasciare un
# telefono con la versione vecchia del lettore dopo un aggiornamento.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Il piano free di Render ha 512 MB di RAM e PyMuPDF carica il PDF in memoria:
# 100 MB di upload erano un OOM annunciato. Configurabile via env.
MAX_UPLOAD_MB = int(os.environ.get('MAX_UPLOAD_MB', '40'))
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_MB * 1024 * 1024

WORKER_THREADS = int(os.environ.get('WORKER_THREADS', '2'))
RETENTION_HOURS = int(os.environ.get('RETENTION_HOURS', '48'))
CLEANUP_INTERVAL = int(os.environ.get('CLEANUP_INTERVAL_SECONDS', '3600'))

DEFAULT_TARGET_LANG = os.environ.get('DEFAULT_TARGET_LANG', 'Italian')

# Tetto ai job contemporanei per utente: senza, un doppio click o uno script
# impazzito accoda decine di libri e brucia credito API senza accorgersene.
MAX_ACTIVE_JOBS = int(os.environ.get('MAX_ACTIVE_JOBS', '5'))

# Spazio libero minimo prima di accettare un upload: se il disco si riempie
# a meta' traduzione si perde il lavoro in modo confuso.
MIN_FREE_DISK_MB = int(os.environ.get('MIN_FREE_DISK_MB', '500'))


# ---------------------------------------------------------------------------
# Registro dei formati supportati
# ---------------------------------------------------------------------------

def _epub_mod():
    import epub_handler
    return (epub_handler.analyze_epub, epub_handler.translate_epub, None)


def _pdf_mod():
    import pdf_handler
    return (pdf_handler.analyze_pdf, pdf_handler.translate_pdf,
            pdf_handler.extract_text_sample)


def _docx_mod():
    import docx_handler
    return (docx_handler.analyze_docx, docx_handler.translate_docx,
            docx_handler.extract_text_sample)


def _txt_mod():
    import txt_handler
    return (txt_handler.analyze_txt, txt_handler.translate_txt,
            txt_handler.extract_text_sample)


FORMATS = {
    'epub': _epub_mod,
    'pdf':  _pdf_mod,
    'docx': _docx_mod,
    'txt':  _txt_mod,
    'md':   _txt_mod,
    'markdown': _txt_mod,
}
ALLOWED_EXTENSIONS = set(FORMATS)


def get_extension(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''


def _epub_text_sample(path, max_chars=1000):
    """EPUB has no standalone sampler in its handler — read the first chapters."""
    import ebooklib
    from ebooklib import epub as epub_lib
    from bs4 import BeautifulSoup

    book = epub_lib.read_epub(path, options={'ignore_ncx': True})
    for doc_item in list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))[:5]:
        soup = BeautifulSoup(doc_item.get_content().decode('utf-8', 'replace'),
                             'html.parser')
        for tag in soup.find_all(['script', 'style', 'meta', 'link']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        if len(text) > 50:
            return text[:max_chars]
    return ""


def text_sample_for(ext, path, max_chars=1000):
    if ext == 'epub':
        return _epub_text_sample(path, max_chars)
    _, _, sampler = FORMATS[ext]()
    return sampler(path, max_chars=max_chars) if sampler else ""


def _check_api_keys():
    openai_key = os.getenv('OPENAI_API_KEY', '')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY', '')
    return {
        'openai': bool(openai_key and not openai_key.startswith('sk-xxxx')),
        'anthropic': bool(anthropic_key and not anthropic_key.startswith('sk-ant-xxxx')),
    }


class JobCanceled(Exception):
    """Raised inside a worker when the user cancels the job."""


# ---------------------------------------------------------------------------
# Difese trasversali
# ---------------------------------------------------------------------------

@app.after_request
def log_request(response):
    """
    Registra ogni chiamata alle API.

    Senza questo era impossibile sapere se un click fosse mai arrivato al
    server: un upload sparito non lasciava alcuna traccia.
    """
    if request.path.startswith('/api/'):
        logger.info('%s %s -> %d  [%s]', request.method, request.full_path.rstrip('?'),
                    response.status_code, auth.current_user() or 'anonimo')
    return response


@app.after_request
def no_stale_html(response):
    """
    Le pagine HTML non vanno mai servite dalla cache del browser.

    Senza questo, dopo un aggiornamento il browser continua a eseguire il
    vecchio JavaScript: e' successo davvero — il lettore restava bianco
    perche' il telefono usava ancora la versione precedente alla correzione.
    I file in /static conservano la cache ma con rivalidazione (ETag), cosi'
    un aggiornamento viene raccolto subito senza riscaricare tutto ogni volta.
    """
    tipo = (response.headers.get('Content-Type') or '')
    if tipo.startswith('text/html'):
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response


@app.after_request
def security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy',
                                'geolocation=(), microphone=(), camera=()')
    # Il portale e' privato: non deve finire in nessun indice.
    response.headers.setdefault('X-Robots-Tag', 'noindex, nofollow')
    if request.headers.get('X-Forwarded-Proto', request.scheme) == 'https':
        response.headers.setdefault('Strict-Transport-Security',
                                    'max-age=31536000; includeSubDomains')
    return response


def client_ip():
    """Real client IP behind the tunnel / Render proxy."""
    fwd = request.headers.get('X-Forwarded-For', '')
    return (fwd.split(',')[0].strip() if fwd else (request.remote_addr or '?'))


def rate_limited(bucket, limit, window, subject=None):
    """
    True se la richiesta va rifiutata.

    Si limita sia per IP sia per `subject` (l'email presa di mira). Il secondo
    e' quello che conta davvero: dietro un proxy come il tunnel Cloudflare
    l'IP visto dal server cambia a ogni richiesta, quindi da solo non
    limiterebbe niente. L'email invece e' stabile ed e' il vero bersaglio.
    """
    keys = [('%s:ip:%s' % (bucket, client_ip()), limit)]
    if subject:
        keys.append(('%s:sub:%s' % (bucket, subject.strip().lower()[:120]), limit))
    # Valutare tutte le chiavi (niente short-circuit) cosi' ogni contatore
    # avanza e una richiesta non "scappa" al limite dell'altra.
    return any([not store.rate_limit_ok(k, lim, window) for k, lim in keys])


def free_disk_mb(path):
    try:
        return shutil.disk_usage(path).free / (1024 * 1024)
    except OSError:
        return float('inf')


# ---------------------------------------------------------------------------
# Worker: esegue i job presi dalla coda persistente
# ---------------------------------------------------------------------------

_running_jobs = set()
_running_lock = threading.Lock()


def _release_running_jobs():
    """
    Su spegnimento, restituisce subito alla coda i job di questo processo.

    Senza questo un deploy lascerebbe i job fermi fino alla scadenza
    dell'heartbeat (STALE_JOB_SECONDS); cosi' ripartono appena il nuovo
    processo e' su, riusando la cache.
    """
    with _running_lock:
        ids = list(_running_jobs)
    for job_id in ids:
        try:
            store.update_job(job_id, status='pending', heartbeat_at=None,
                             status_text='Interrotto dal riavvio, riprendo...')
        except Exception:
            pass
    if ids:
        logger.info('Rimessi in coda %d job prima dello spegnimento', len(ids))


def _install_shutdown_hooks():
    atexit.register(_release_running_jobs)

    def handler(signum, frame):
        _release_running_jobs()
        # Gunicorn ha il suo handler per lo shutdown pulito: va richiamato,
        # altrimenti il worker non termina come dovrebbe.
        if callable(previous.get(signum)):
            previous[signum](signum, frame)
        else:
            raise SystemExit(0)

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # non siamo nel thread principale: pazienza, resta atexit


def _run_job(job):
    from translator import set_job_context, clear_job_context

    job_id = job['id']
    with _running_lock:
        _running_jobs.add(job_id)
    stop_beat = threading.Event()

    def beat():
        # Senza heartbeat un job lungo verrebbe scambiato per morto e ripreso
        # da un altro worker mentre e' ancora vivo. L'intervallo e' derivato
        # dalla soglia di scadenza (store.py), non fissato a mano.
        while not stop_beat.wait(store.HEARTBEAT_INTERVAL):
            store.heartbeat(job_id)

    threading.Thread(target=beat, daemon=True).start()

    try:
        resumed = store.cache_size(job_id)
        if job['attempts'] > 1 and resumed:
            store.add_log(job_id, "Ripresa dopo interruzione: %d blocchi gia' "
                                  "tradotti riusati dalla cache." % resumed)
        else:
            store.add_log(job_id, 'Inizio traduzione: %s' % job['original_filename'])
        store.add_log(job_id, 'Da %s a %s' % (job['source_lang'], job['target_lang']))
        store.add_log(job_id, 'Modello: %s (%s)' % (job['model'], job['provider']))

        set_job_context(job_id)

        last_pct = [-1]

        def progress_callback(progress, status_text):
            current = store.get_job(job_id)
            if current and current['status'] == 'canceled':
                raise JobCanceled()
            store.update_job(job_id, progress=float(progress),
                             status_text=status_text)
            pct = int(progress * 100)
            if pct != last_pct[0]:
                last_pct[0] = pct
                store.add_log(job_id, status_text)

        _, translate_fn, _ = FORMATS[job['file_type']]()
        store.add_log(job_id, 'Formato %s - analisi struttura...'
                      % job['file_type'].upper())

        translate_fn(
            job['input_path'],
            job['output_path'],
            source_lang=job['source_lang'],
            target_lang=job['target_lang'],
            provider=job['provider'],
            model=job['model'],
            progress_callback=progress_callback,
        )

        name, ext = os.path.splitext(job['original_filename'])
        out_name = '%s_tradotto_%s%s' % (name, job['target_lang'], ext)

        try:
            covers.genera(job_id, job['output_path'], job['file_type'],
                          job['original_filename'])
        except Exception as e:
            logger.warning('Copertina non generata: %s', str(e)[:80])

        # Controllo di fedelta': il libro tradotto deve avere la stessa ossatura
        # dell'originale. Se ha perso capitoli, paragrafi, titoli o immagini lo
        # si scrive nel registro del libro, cosi' si vede nella scheda invece di
        # scoprirlo leggendo. Non blocca nulla: e' un referto, non un veto.
        try:
            # I moduli dei formati si importano solo quando servono (vedi FORMATS),
            # quindi qui vanno chiesti per nome: usarli come variabili globali
            # darebbe NameError, e il try qui sotto lo nasconderebbe.
            controllore = None
            if job['file_type'] == 'epub':
                import epub_handler as controllore
            elif job['file_type'] == 'pdf':
                import pdf_handler as controllore
            if controllore and hasattr(controllore, 'controlla_fedelta') \
                    and job['input_path'] and os.path.exists(job['input_path']):
                ok, righe = controllore.controlla_fedelta(job['input_path'],
                                                          job['output_path'])
                for r in righe:
                    store.add_log(job_id, r, 'success' if ok else 'warning')
                if not ok:
                    logger.warning('Fedelta\' impaginazione %s: %s', job_id, ' | '.join(righe))
        except Exception as e:
            logger.warning('Controllo di fedelta\' fallito: %s', str(e)[:120])

        if (job.get('file_type') or '').lower() == 'pdf':
            _linearizza_pdf(job['output_path'])
        store.update_job(job_id, status='completed', progress=1.0,
                         status_text='Completato!', output_filename=out_name,
                         error=None)
        store.add_log(job_id, 'Traduzione completata con successo!', 'success')

        if AUDIOLIBRO_AUTO:
            _accoda_audiolibro(job_id, job['owner'])
            store.add_log(job_id, 'Audiolibro messo in coda: sara\' pronto fra poco.')

    except JobCanceled:
        store.update_job(job_id, status='canceled', status_text='Annullato')
        store.add_log(job_id, "Job annullato dall'utente.", 'warning')

    except Exception as e:
        logger.exception('Job %s fallito', job_id)
        # Un errore transitorio (rete, rate limit) merita un altro tentativo;
        # dopo 3 tentativi il job si ferma per non bruciare credito a vuoto.
        if job['attempts'] < 3:
            store.update_job(job_id, status='pending',
                             status_text='Errore, nuovo tentativo in corso...',
                             error=str(e)[:500], heartbeat_at=None)
            store.add_log(job_id, 'Errore: %s - riprovo (tentativo %d/3)'
                          % (str(e)[:200], job['attempts'] + 1), 'warning')
        else:
            store.update_job(job_id, status='error', error=str(e)[:500],
                             status_text='Errore')
            store.add_log(job_id, 'Errore definitivo: %s' % str(e)[:300], 'error')

    finally:
        clear_job_context()
        stop_beat.set()
        with _running_lock:
            _running_jobs.discard(job_id)


def _worker_loop(index):
    logger.info('Worker %d avviato', index)
    while True:
        try:
            job = store.claim_next_job()
            if job is None:
                time.sleep(2)
                continue
            _run_job(job)
        except Exception:
            logger.exception('Errore nel worker %d', index)
            time.sleep(5)


# Tetto allo spazio occupato dall'audio gia' sintetizzato. Un libro intero letto
# ad alta voce sta fra i 400 e gli 800 MB: senza un limite la cartella cresce
# finche' il disco non finisce, e su Render il disco e' piccolo.
MAX_AUDIO_MB = int(os.environ.get('TTS_CACHE_MAX_MB', '4000'))


def _pota_audio():
    """Butta i pezzi audio meno ascoltati di recente, finche' si rientra.

    A differenza dei file caricati, questi non scadono col tempo: scadono per
    spazio. Rigenerarli costa denaro, quindi si toccano solo quando serve
    davvero, e si comincia da quelli che nessuno riascolta da piu' tempo.
    """
    da_buttare = store.audio_da_buttare(MAX_AUDIO_MB * 1024 * 1024)
    if not da_buttare:
        return
    tolti, liberati = [], 0
    for riga in da_buttare:
        try:
            if os.path.exists(riga['percorso']):
                os.remove(riga['percorso'])
            tolti.append(riga['chiave'])
            liberati += riga['byte']
        except OSError:
            # Se il file non si cancella, la riga resta: il database non deve
            # dire che un audio non c'e' piu' mentre e' ancora sul disco.
            logger.warning('Audio non rimosso: %s', riga['percorso'])
    store.audio_dimentica(tolti)
    logger.info('Pulizia audio: %d pezzi, %.0f MB liberati',
                len(tolti), liberati / 1048576)


# Creare l'audiolibro subito dopo la traduzione o il caricamento. Si puo'
# spegnere con AUDIOLIBRO_AUTO=0.
AUDIOLIBRO_AUTO = os.environ.get('AUDIOLIBRO_AUTO', '1') != '0'


def _accoda_audiolibro(job_id, owner, priorita=0):
    """Mette un libro in coda per l'audio, senza far fallire nulla se non si puo'."""
    try:
        store.audiolibro_accoda(job_id, owner, priorita)
    except Exception as e:
        logger.warning('Audiolibro non accodato per %s: %s', job_id, str(e)[:100])


def _audiolibro_loop():
    """Un solo audiolibro alla volta, in sottofondo.

    Perche' uno solo: la voce gira sul Mac dell'utente. Farne due insieme
    raddoppierebbe il calore e dimezzerebbe la reattivita' del portale senza
    finire prima, perche' il collo di bottiglia e' la CPU.

    Perche' solo col motore locale: l'audio di un libro sono centinaia di
    migliaia di caratteri. Con il motore a pagamento una coda automatica
    spenderebbe decine di dollari senza che nessuno l'abbia chiesto. Se il
    motore locale non c'e', la coda aspetta e lo dice.
    """
    import audiolibro
    while True:
        try:
            if tts.motore_attivo() != 'locale':
                time.sleep(60)
                continue
            prossimo = store.audiolibro_prossimo()
            if not prossimo:
                time.sleep(15)
                continue

            job_id, owner = prossimo['job_id'], prossimo['owner']
            job = store.get_job(job_id, owner=owner)
            if not job or job['status'] != 'completed':
                store.audiolibro_aggiorna(job_id, owner, stato='errore',
                                          errore='libro non disponibile')
                continue

            voce = (store.get_ascolto(job_id, owner).get('voce')
                    or tts.voce_predefinita('locale'))
            logger.info('Audiolibro: comincio "%s"', (job['original_filename'] or '')[:60])

            def avanzamento(fatti, totali):
                store.audiolibro_aggiorna(job_id, owner, capitoli=totali,
                                          capitoli_fatti=fatti)

            esito = audiolibro.genera(job, voce, store, avanzamento=avanzamento)
            store.audiolibro_aggiorna(
                job_id, owner, stato='pronto', errore=None, voce=voce,
                capitoli=esito['capitoli'], capitoli_fatti=esito['capitoli_fatti'],
                byte=esito['byte'], secondi=esito['secondi'])
            store.add_log(job_id, 'Audiolibro pronto: %d capitoli, %d minuti.'
                          % (esito['capitoli_fatti'], round(esito['secondi'] / 60)), 'success')
            logger.info('Audiolibro pronto: %s (%d capitoli, %.0f MB)',
                        job_id, esito['capitoli_fatti'], esito['byte'] / 1048576)
            audiolibro.pota(store)
        except Exception as e:
            logger.exception('Audiolibro fallito')
            try:
                store.audiolibro_aggiorna(prossimo['job_id'], prossimo['owner'],
                                          stato='errore', errore=str(e)[:300])
            except Exception:
                pass
            time.sleep(5)


def _cleanup_loop():
    while True:
        try:
            files = store.purge_expired(retention_hours=RETENTION_HOURS)
            orphans = store.purge_orphan_files([UPLOAD_DIR, OUTPUT_DIR],
                                               retention_hours=RETENTION_HOURS)
            if files or orphans:
                logger.info('Pulizia: %d file scaduti, %d orfani rimossi',
                            files, orphans)
            _pota_audio()
        except Exception:
            logger.exception('Errore nella pulizia')
        time.sleep(CLEANUP_INTERVAL)


_bg_started = False
_bg_lock = threading.Lock()


def start_background():
    """Start workers and the janitor exactly once per process."""
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        _bg_started = True

    store.db()
    store.requeue_stale_jobs()
    _install_shutdown_hooks()

    for i in range(WORKER_THREADS):
        threading.Thread(target=_worker_loop, args=(i + 1,), daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    # Un solo filo per gli audiolibri: la voce gira sulla CPU di questo Mac.
    threading.Thread(target=_audiolibro_loop, daemon=True).start()
    logger.info('Avviati %d worker + pulizia (retention %dh) + audiolibri',
                WORKER_THREADS, RETENTION_HOURS)


# ---------------------------------------------------------------------------
# Autenticazione
# ---------------------------------------------------------------------------

@app.route('/login')
def login_page():
    if auth.current_user():
        return redirect('/')
    return render_template('login.html', smtp_ready=auth.smtp_configured())


@app.route('/api/auth/request-otp', methods=['POST'])
def api_request_otp():
    data = request.get_json(silent=True) or request.form
    email = data.get('email', '')
    # 10 richieste in 10 minuti: sopra c'e' qualcuno che sonda, non tu.
    if rate_limited('otp-req', 10, 600, subject=email):
        return jsonify({'ok': False,
                        'message': 'Troppe richieste. Riprova fra qualche minuto.'}), 429
    ok, message, by_email = auth.request_otp(email, client_ip=client_ip())
    return jsonify({'ok': ok, 'message': message,
                    'sent_by_email': by_email}), (200 if ok else 429)


@app.route('/api/auth/verify', methods=['POST'])
def api_verify_otp():
    data = request.get_json(silent=True) or request.form
    email = data.get('email', '')
    # Argine al brute force sui codici, oltre ai 5 tentativi per singolo OTP.
    if rate_limited('otp-verify', 20, 600, subject=email):
        return jsonify({'ok': False,
                        'message': 'Troppi tentativi. Riprova fra qualche minuto.'}), 429
    token, message = auth.verify_otp(email, data.get('code', ''))
    if not token:
        return jsonify({'ok': False, 'message': message}), 401
    resp = make_response(jsonify({'ok': True, 'message': message}))
    return auth.set_session_cookie(resp, token)


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    auth.destroy_session(request.cookies.get(auth.COOKIE_NAME))
    return auth.clear_session_cookie(make_response(jsonify({'ok': True})))


@app.route('/api/auth/me')
def api_me():
    email = auth.current_user()
    return jsonify({'authenticated': bool(email), 'email': email})


# ---------------------------------------------------------------------------
# Pagine e API
# ---------------------------------------------------------------------------

@app.route('/')
@auth.page_login_required
def index():
    return render_template('index.html')


@app.route('/libreria')
@auth.page_login_required
def libreria():
    # UNA libreria: cartelle, tag e tutto quello che c'era (vedi libreria_ponte.py);
    # la pagina di prima resta su /libreria/classica
    return render_template('library.html')


@app.route('/healthz')
def healthz():
    try:
        store.db().execute('SELECT 1')
        return jsonify({'ok': True}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/models')
@auth.login_required
def api_models():
    from translator import MODEL_PRICING
    keys = _check_api_keys()

    models = []
    for model_id, info in MODEL_PRICING.items():
        if not keys.get(info['provider']):
            continue
        models.append({
            'id': model_id,
            'provider': info['provider'],
            'display_name': info['display_name'],
            'quality': info['quality'],
            'speed': info['speed'],
            'cost_indicator': info['cost_indicator'],
            'quality_badge': info['quality_badge'],
            'input_cost': info['input_cost'],
            'output_cost': info['output_cost'],
            'description': info.get('description', ''),
        })

    return jsonify({
        'models': models,
        'api_status': keys,
        'default_model': ('claude-sonnet-5' if keys.get('anthropic')
                          else ('gpt-4.1' if keys.get('openai') else None)),
        'default_target_lang': DEFAULT_TARGET_LANG,
        'supported_formats': sorted(ALLOWED_EXTENSIONS),
        'max_upload_mb': MAX_UPLOAD_MB,
    })


def _save_temp(file, prefix):
    temp_path = os.path.join(
        UPLOAD_DIR,
        '%s_%s_%s' % (prefix, uuid.uuid4(), secure_filename(file.filename)))
    file.save(temp_path)
    return temp_path


def _validated_upload():
    """Shared validation for every endpoint that accepts a file."""
    if 'file' not in request.files:
        return None, None, (jsonify({'error': 'Nessun file caricato'}), 400)
    file = request.files['file']
    if not file.filename:
        return None, None, (jsonify({'error': 'Nessun file selezionato'}), 400)
    ext = get_extension(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        return None, None, (jsonify({
            'error': 'Formato non supportato: .%s. Formati validi: %s'
                     % (ext, ', '.join(sorted(ALLOWED_EXTENSIONS)))
        }), 400)
    return file, ext, None


@app.route('/api/analyze', methods=['POST'])
@auth.login_required
def api_analyze():
    """Stats + token count for the price preview. No AI call, so it's free."""
    file, ext, err = _validated_upload()
    if err:
        return err

    temp_path = _save_temp(file, 'analyze')
    try:
        analyze_fn, _, _ = FORMATS[ext]()
        analysis = analyze_fn(temp_path)
        analysis['file_type'] = ext
        logger.info('Analisi OK: %s (%s, %d parole)', file.filename, ext,
                    analysis.get('total_words', 0))
        return jsonify(analysis)
    except Exception as e:
        logger.exception('Analisi fallita per %s', file.filename)
        return jsonify({'error': str(e)}), 500
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


@app.route('/api/detect-language', methods=['POST'])
@auth.login_required
def api_detect_language():
    file, ext, err = _validated_upload()
    if err:
        return err

    keys = _check_api_keys()
    provider = request.form.get('provider', '')
    model = request.form.get('model', '')
    if not provider:
        if keys.get('anthropic'):
            provider, model = 'anthropic', model or 'claude-haiku-4-5'
        elif keys.get('openai'):
            provider, model = 'openai', model or 'gpt-4o-mini'
        else:
            return jsonify({'error': 'Nessuna chiave API configurata'}), 400
    if not model:
        model = 'claude-haiku-4-5' if provider == 'anthropic' else 'gpt-4o-mini'

    temp_path = _save_temp(file, 'detect')
    try:
        sample = text_sample_for(ext, temp_path, max_chars=1000)
        if not sample or len(sample.strip()) < 20:
            return jsonify({'error': 'Testo insufficiente per il rilevamento'}), 400

        from translator import detect_language
        detected = detect_language(sample, provider=provider, model=model)
        if detected == 'Unknown':
            return jsonify({'error': 'Lingua non riconosciuta'}), 400
        return jsonify({'language': detected})
    except Exception as e:
        logger.error('Rilevamento lingua fallito: %s', e)
        return jsonify({'error': str(e)}), 500
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


@app.route('/api/translate', methods=['POST'])
@auth.login_required
def api_translate():
    file, ext, err = _validated_upload()
    if err:
        return err

    source_lang = request.form.get('source_lang', 'English')
    target_lang = request.form.get('target_lang', DEFAULT_TARGET_LANG)
    model = request.form.get('model', 'claude-sonnet-5')

    if source_lang == target_lang:
        return jsonify({'error': 'Lingua di partenza e di arrivo devono essere diverse'}), 400

    from translator import MODEL_PRICING
    if model not in MODEL_PRICING:
        return jsonify({'error': 'Modello non valido: %s' % model}), 400
    provider = MODEL_PRICING[model]['provider']

    if not _check_api_keys().get(provider):
        return jsonify({'error': 'Chiave API %s non configurata' % provider}), 400

    owner = auth.current_user()
    active = store.count_active_jobs(owner)
    if active >= MAX_ACTIVE_JOBS:
        return jsonify({'error': 'Hai gia\' %d traduzioni in coda (limite %d). '
                                 'Aspetta che finiscano o annullane una.'
                                 % (active, MAX_ACTIVE_JOBS)}), 429

    if free_disk_mb(UPLOAD_DIR) < MIN_FREE_DISK_MB:
        logger.error('Spazio disco insufficiente: %.0f MB liberi', free_disk_mb(UPLOAD_DIR))
        return jsonify({'error': 'Spazio su disco insufficiente sul server. '
                                 'Riprova piu\' tardi.'}), 507

    job_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename) or ('libro.%s' % ext)
    input_path = os.path.join(UPLOAD_DIR, '%s_%s' % (job_id, safe_name))
    output_path = os.path.join(OUTPUT_DIR, '%s_translated.%s' % (job_id, ext))

    file.save(input_path)
    logger.info('File salvato: %s (%d byte)', input_path, os.path.getsize(input_path))

    store.create_job(
        id=job_id,
        owner=owner,
        original_filename=file.filename,
        input_path=input_path,
        output_path=output_path,
        file_type=ext,
        source_lang=source_lang,
        target_lang=target_lang,
        provider=provider,
        model=model,
    )
    store.add_log(job_id, 'Job creato e messo in coda.')

    return jsonify({'task_id': job_id, 'status': 'queued'})


@app.route('/api/importa', methods=['POST'])
@auth.login_required
def api_importa():
    """Mette in libreria un libro GIA' pronto, senza tradurlo.

    Serve a far diventare il portale la propria libreria e non solo un
    traduttore: libri gia' in italiano, traduzioni fatte altrove, roba propria.
    Una volta dentro si leggono, si ascoltano, si annotano e si cercano come
    tutti gli altri.

    Il file va dritto in `outputs/`, che e' la cartella dei risultati: quella
    degli upload viene svuotata dopo `RETENTION_HOURS` e un libro importato non
    deve sparire per un file che non e' mai stato di passaggio.
    """
    file, ext, err = _validated_upload()
    if err:
        return err

    owner = auth.current_user()
    if free_disk_mb(OUTPUT_DIR) < MIN_FREE_DISK_MB:
        return jsonify({'error': 'Spazio su disco insufficiente sul server.'}), 507

    # Stesso argine degli altri caricamenti: un doppio click non deve riempire
    # il disco di copie dello stesso libro.
    if rate_limited('importa', 30, 600, subject=owner):
        return jsonify({'error': 'Troppi caricamenti di fila. Aspetta un minuto.'}), 429

    lingua = (request.form.get('lingua') or 'Italiano').strip()[:40]
    job_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename) or ('libro.%s' % ext)
    output_path = os.path.join(OUTPUT_DIR, '%s_%s' % (job_id, safe_name))
    file.save(output_path)

    dimensione = os.path.getsize(output_path)
    if dimensione == 0:
        os.remove(output_path)
        return jsonify({'error': 'Il file e\' vuoto.'}), 400
    if ext == 'pdf':
        # prima di rispondere, non dopo: il lettore lo apre un attimo dopo, e
        # sostituire il file mentre pdf.js lo legge a pezzi lo manderebbe in tilt
        _linearizza_pdf(output_path)

    store.create_job(
        id=job_id, owner=owner,
        original_filename=file.filename,
        input_path=None,                    # non c'e' un originale da buttare
        output_path=output_path,
        output_filename=file.filename,
        file_type=ext,
        source_lang=lingua, target_lang=lingua,
        provider='importato', model='importato',
        status='completed', progress=1.0, status_text='Importato',
    )
    store.add_log(job_id, 'Libro importato nella libreria (nessuna traduzione).', 'success')

    try:
        covers.genera(job_id, output_path, ext, file.filename)
    except Exception as e:
        logger.warning('Copertina non generata per l\'importazione: %s', str(e)[:80])

    # Un libro caricato dal telefono non ha mai visto il Mac: l'audio glielo
    # prepara la coda, senza che l'utente debba chiederlo.
    if AUDIOLIBRO_AUTO and ext in ('epub', 'pdf'):
        _accoda_audiolibro(job_id, owner)

    logger.info('Importato: %s (%d byte) come %s', file.filename, dimensione, job_id)
    return jsonify({'task_id': job_id, 'status': 'completed',
                    'leggibile': ext in ('epub', 'pdf')})


@app.route('/api/audiolibro/<task_id>', methods=['GET', 'POST', 'DELETE'])
@auth.login_required
def api_audiolibro(task_id):
    """Stato, richiesta e cancellazione dell'audiolibro di un libro."""
    import audiolibro
    owner = auth.current_user()
    job = store.get_job(task_id, owner=owner)
    if not job:
        return jsonify({'error': 'Non trovato'}), 404

    if request.method == 'POST':
        if job['file_type'] not in ('epub', 'pdf'):
            return jsonify({'error': 'Formato senza audiolibro: %s' % job['file_type']}), 400
        # Chi lo chiede a mano passa avanti a quelli messi in coda da soli.
        store.audiolibro_accoda(task_id, owner, priorita=10)
        return jsonify({'ok': True, 'stato': 'in_coda'})

    if request.method == 'DELETE':
        audiolibro.elimina(task_id)
        store.audiolibro_dimentica(task_id, owner)
        return jsonify({'ok': True})

    stato = store.audiolibro_stato(task_id, owner) or {}
    return jsonify({
        'stato': stato.get('stato', 'assente'),
        'capitoli': stato.get('capitoli', 0),
        'capitoli_fatti': stato.get('capitoli_fatti', 0),
        'byte': stato.get('byte', 0),
        'minuti': round((stato.get('secondi') or 0) / 60),
        'voce': stato.get('voce'),
        'errore': stato.get('errore'),
        'tracce': audiolibro.indice(task_id),
        'motore': tts.motore_attivo(),
    })


@app.route('/api/audiolibro/<task_id>/<nome>')
@auth.login_required
def api_audiolibro_traccia(task_id, nome):
    """Serve un capitolo dell'audiolibro. Range abilitato: il telefono salta
    avanti e indietro senza riscaricare tutto."""
    import audiolibro
    if not store.get_job(task_id, owner=auth.current_user()):
        return jsonify({'error': 'Non trovato'}), 404
    nome = secure_filename(nome)
    percorso = os.path.join(audiolibro.cartella_di(task_id), nome)
    if not nome.endswith('.mp3') or not os.path.exists(percorso):
        return jsonify({'error': 'Traccia non trovata'}), 404
    store.audiolibro_ascoltato(task_id, auth.current_user())
    resp = send_file(percorso, mimetype='audio/mpeg', conditional=True)
    resp.headers['Cache-Control'] = 'private, max-age=604800'
    return resp


@app.route('/api/audiolibri', methods=['GET', 'POST'])
@auth.login_required
def api_audiolibri():
    """GET: quanto spazio occupano. POST: crea l'audio dei libri che non l'hanno."""
    import audiolibro
    owner = auth.current_user()
    if request.method == 'GET':
        s = store.audiolibri_spazio()
        return jsonify({
            'pronti': s['pronti'], 'in_coda': s['in_coda'],
            'byte': s['byte'], 'ore': round((s['secondi'] or 0) / 3600, 1),
            'tetto_gb': audiolibro.MAX_GB,
            'motore': tts.motore_attivo(),
            'automatico': AUDIOLIBRO_AUTO,
        })

    # "Crea audio mancanti": serve soprattutto ai libri caricati dal telefono,
    # che non sono mai passati dalla coda della traduzione.
    mappa = store.audiolibro_mappa(owner)
    accodati = []
    for job in store.list_jobs(owner, limit=1000, offset=0):
        if job['status'] != 'completed' or job['file_type'] not in ('epub', 'pdf'):
            continue
        if not (job['output_path'] and os.path.exists(job['output_path'])):
            continue                      # libro senza file: niente da leggere
        stato = (mappa.get(job['id']) or {}).get('stato')
        if stato in ('pronto', 'in_coda', 'in_corso'):
            continue
        store.audiolibro_accoda(job['id'], owner, priorita=0)
        accodati.append(job['id'])
    return jsonify({'accodati': len(accodati),
                    'motore': tts.motore_attivo(),
                    'avviso': None if tts.motore_attivo() == 'locale' else
                              'La voce locale non e\' attiva: la coda aspetta '
                              'invece di spendere sul servizio a pagamento.'})


@app.route('/api/libro/<task_id>', methods=['DELETE'])
@auth.login_required
def api_elimina_libro(task_id):
    """Toglie un libro dalla libreria: file, copertina, posizione, annotazioni.

    Chi puo' aggiungere libri deve poterli togliere, altrimenti la libreria
    cresce e basta. E' la sola cancellazione voluta che esista: tutto il resto
    del sistema ormai non cancella piu' nulla da solo.
    """
    owner = auth.current_user()
    job = store.get_job(task_id, owner=owner)
    if not job:
        return jsonify({'error': 'Non trovato'}), 404
    if job['status'] in ('running', 'pending'):
        return jsonify({'error': 'Traduzione in corso: annullala prima.'}), 409

    rimossi = 0
    for percorso in (job['output_path'], job['input_path']):
        if percorso and os.path.exists(percorso):
            try:
                os.remove(percorso)
                rimossi += 1
            except OSError as e:
                logger.warning('Non rimosso %s: %s', percorso, e)
    try:
        copertina = covers.percorso_esistente(task_id)
        if copertina and os.path.exists(copertina):
            os.remove(copertina)
    except Exception:
        pass

    _dimentica_pagine(task_id)          # jpg tradotti, .ocr.json, coda: vanno via col libro
    store.delete_job(task_id, owner)
    logger.info('Libro eliminato: %s (%s), %d file rimossi',
                job['original_filename'], task_id, rimossi)
    return jsonify({'ok': True})


def _job_payload(job, after_log=0):
    return {
        'task_id': job['id'],
        'status': job['status'],
        'progress': job['progress'],
        'status_text': job['status_text'],
        'error': job['error'],
        'output_filename': job['output_filename'],
        'original_filename': job['original_filename'],
        'file_type': job['file_type'],
        'target_lang': job['target_lang'],
        'model': job['model'],
        'created_at': job['created_at'],
        'logs': store.get_logs(job['id'], after_id=after_log),
    }


@app.route('/api/status/<task_id>')
@auth.login_required
def api_status(task_id):
    job = store.get_job(task_id, owner=auth.current_user())
    if not job:
        return jsonify({'error': 'Task non trovato'}), 404
    after = request.args.get('after_log', type=int) or 0
    return jsonify(_job_payload(job, after_log=after))


@app.route('/api/jobs')
@auth.login_required
def api_jobs():
    owner = auth.current_user()
    jobs = store.list_jobs(
        owner,
        limit=min(request.args.get('limit', type=int) or 50, 200),
        offset=request.args.get('offset', type=int) or 0,
        search=request.args.get('q'),
        status=request.args.get('status'),
    )
    letture = store.reading_map(owner)
    note = store.annotation_counts(owner)
    audio_map = store.audiolibro_mappa(owner)

    payload = []
    for job in jobs:
        item = _job_payload(job)
        item['logs'] = []          # la lista non ha bisogno dei log completi
        lettura = letture.get(job['id'], {})
        item['read_percent'] = lettura.get('percent') or 0
        item['read_label'] = lettura.get('label')
        item['read_at'] = lettura.get('updated_at')
        item['annotations'] = note.get(job['id'], 0)
        # Un libro senza file non e' leggibile, per quanto risulti "completato".
        # Dirlo qui evita che la libreria offra di aprirlo per poi mostrare un
        # errore: e' successo davvero, dopo che la pulizia aveva cancellato i
        # tradotti, e sembrava che il portale fosse rotto.
        c_e_il_file = bool(job['output_path'] and os.path.exists(job['output_path']))
        item['file_presente'] = c_e_il_file
        item['readable'] = c_e_il_file and job['file_type'] in ('epub', 'pdf')
        item['importato'] = job['model'] == 'importato'
        a = audio_map.get(job['id'])
        item['audio'] = {
            'stato': (a or {}).get('stato', 'assente'),
            'capitoli': (a or {}).get('capitoli', 0),
            'fatti': (a or {}).get('capitoli_fatti', 0),
            'minuti': round(((a or {}).get('secondi') or 0) / 60),
        }
        payload.append(item)
    return jsonify({'jobs': payload, 'counts': store.job_counts(owner)})


@app.route('/api/cancel/<task_id>', methods=['POST'])
@auth.login_required
def api_cancel(task_id):
    job = store.get_job(task_id, owner=auth.current_user())
    if not job:
        return jsonify({'error': 'Task non trovato'}), 404
    if job['status'] in ('completed', 'error', 'canceled'):
        return jsonify({'error': "Job gia' concluso"}), 400
    store.update_job(task_id, status='canceled', status_text='Annullato')
    return jsonify({'ok': True})


@app.route('/api/download/<task_id>')
@auth.login_required
def api_download(task_id):
    job = store.get_job(task_id, owner=auth.current_user())
    if not job:
        return jsonify({'error': 'Task non trovato'}), 404
    if job['status'] != 'completed':
        return jsonify({'error': 'Traduzione non ancora completata'}), 400
    if not job['output_path'] or not os.path.exists(job['output_path']):
        return jsonify({'error': 'File scaduto o rimosso (retention %dh)'
                                 % RETENTION_HOURS}), 410
    return send_file(job['output_path'], as_attachment=True,
                     download_name=job['output_filename'])


# ---------------------------------------------------------------------------
# Lettore: copertine, file del libro, segnalibro, annotazioni
# ---------------------------------------------------------------------------

@app.route('/leggi/<task_id>')
@auth.page_login_required
def leggi(task_id):
    job = store.get_job(task_id, owner=auth.current_user())
    if not job or job['status'] != 'completed':
        return redirect('/libreria')
    return render_template('lettore.html', job_id=task_id,
                           titolo=job['original_filename'],
                           formato=job['file_type'])


@app.route('/api/cover/<task_id>')
@auth.login_required
def api_cover(task_id):
    job = store.get_job(task_id, owner=auth.current_user())
    if not job:
        return jsonify({'error': 'Non trovato'}), 404

    path = covers.percorso_esistente(task_id)
    if not path:
        sorgente = job['output_path'] if (job['output_path'] and
                                          os.path.exists(job['output_path'])) \
                   else job['input_path']
        path = covers.genera(task_id, sorgente, job['file_type'],
                             job['original_filename'])
    if not path:
        return jsonify({'error': 'Copertina non disponibile'}), 404
    resp = send_file(path, mimetype='image/jpeg')
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@app.route('/api/book/<task_id>/file')
@auth.login_required
def api_book_file(task_id):
    """Il libro tradotto, servito al lettore nel browser (non come scaricamento)."""
    job = store.get_job(task_id, owner=auth.current_user())
    if not job or job['status'] != 'completed':
        return jsonify({'error': 'Libro non disponibile'}), 404
    if not job['output_path'] or not os.path.exists(job['output_path']):
        return jsonify({'error': 'File scaduto'}), 410
    tipi = {'epub': 'application/epub+zip', 'pdf': 'application/pdf',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'txt': 'text/plain; charset=utf-8', 'md': 'text/plain; charset=utf-8'}
    resp = send_file(job['output_path'],
                     mimetype=tipi.get(job['file_type'], 'application/octet-stream'),
                     conditional=True)          # Range: il lettore carica a pezzi
    # werkzeug mette Accept-Ranges SOLO sulle risposte 206, ma pdf.js decide se
    # usare i Range guardando la PRIMA risposta (quella intera, 200): senza
    # questo header scaricava tutto il libro in sequenza prima di aprirlo.
    resp.headers['Accept-Ranges'] = 'bytes'
    return resp


# ---------------------------------------------------------------------------
# Ascolto: il libro letto ad alta voce
# ---------------------------------------------------------------------------

@app.route('/api/voci')
@auth.login_required
def api_voci():
    """Le voci disponibili, la scelta fatta per questo libro, e quanto resta oggi."""
    task_id = request.args.get('libro') or ''
    scelta = {'voce': None, 'velocita': 1.0}
    if task_id and store.get_job(task_id, owner=auth.current_user()):
        scelta = store.get_ascolto(task_id, auth.current_user())
    usati = store.tts_caratteri_oggi()
    motore = tts.motore_attivo()
    return jsonify({
        'voci': tts.voci_disponibili(motore),
        'predefinita': tts.voce_predefinita(motore),
        'scelta': scelta,
        # L'interfaccia deve poter dire all'utente se sta spendendo o no.
        'motore': motore,
        'gratis': motore == 'locale',
        'disponibile': (motore == 'locale') or bool(os.environ.get('OPENAI_API_KEY')),
        'tetto': {'usati': usati, 'massimo': tts.MAX_CARATTERI_GIORNO,
                  'restanti': max(0, tts.MAX_CARATTERI_GIORNO - usati)},
    })


@app.route('/api/voce/campione/<voce>')
@auth.login_required
def api_voce_campione(voce):
    """Una frase di prova, per scegliere la voce ascoltandola invece che leggendone il nome."""
    try:
        percorso = tts.campione(tts.voce_valida(voce), store=store)
    except tts.TettoRaggiunto as e:
        return jsonify({'error': str(e)}), 429
    except Exception as e:
        logger.exception('Campione voce non generato')
        return jsonify({'error': str(e)}), 502
    resp = send_file(percorso, mimetype='audio/mpeg', conditional=True)
    # I campioni non cambiano mai: tenerli nel browser evita di rigenerare
    # (e ripagare) la stessa frase a ogni apertura del pannello.
    resp.headers['Cache-Control'] = 'private, max-age=604800'
    return resp



def _cloud_attivo():
    """Vero se il motore a consumo e' configurato e ha ancora credito.

    Non solleva mai: se qualcosa non va risponde False e si torna al motore
    gratuito. Un errore qui non deve impedire di ascoltare, deve solo far
    tornare lenti.
    """
    try:
        import voce_cloud
        return voce_cloud.disponibile() and voce_cloud.residuo(store) > 0
    except Exception:
        logger.debug('cloud non disponibile', exc_info=True)
        return False


@app.route('/api/voce', methods=['POST'])
@auth.login_required
def api_voce():
    """Sintetizza UNA frase e restituisce l'mp3.

    Il lettore chiama questa rotta una frase per volta, con qualche frase di
    anticipo. Non esiste una rotta che legga un libro intero in un colpo: cosi'
    chi ascolta due pagine paga due pagine, e l'audio gia' prodotto resta in
    cache per sempre.
    """
    utente = auth.current_user()
    # Un ciclo impazzito nel lettore brucerebbe il tetto giornaliero in pochi
    # minuti. A voce normale una frase dura qualche secondo: 600 richieste in
    # cinque minuti sono gia' molto piu' di un ascolto vero con precaricamento.
    if rate_limited('voce', 600, 300, subject=utente):
        return jsonify({'error': 'Troppe richieste di lettura, rallenta un attimo.'}), 429

    dati = request.get_json(silent=True) or {}
    testo = dati.get('testo') or ''
    voce = tts.voce_valida(dati.get('voce'))

    # Se l'audio non e' gia' pronto, chi sta ascoltando NON puo' aspettare la
    # macchina gratuita: misurato, 20-60 secondi per una frase che ne dura 7.
    # Il motore a consumo la fa in uno. Il gratuito serve a riempire in
    # anticipo, non a far aspettare chi ha appena premuto Play.
    try:
        percorso, in_cache = tts.sintetizza(testo, voce, store=store,
                                            cloud=_cloud_attivo())
    except tts.TroppoLungo as e:
        return jsonify({'error': str(e)}), 413
    except tts.TettoRaggiunto as e:
        return jsonify({'error': str(e)}), 429
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('Sintesi vocale fallita')
        return jsonify({'error': 'Voce non disponibile in questo momento.'}), 502

    resp = send_file(percorso, mimetype='audio/mpeg', conditional=True)
    resp.headers['Cache-Control'] = 'private, max-age=604800'
    # Serve al lettore per mostrare quando sta pagando e quando no.
    resp.headers['X-Dalla-Cache'] = '1' if in_cache else '0'
    return resp


# --- preparazione in anticipo ------------------------------------------------
#
# Sulla macchina gratuita una frase richiede ~27 secondi di calcolo e ne dura 7:
# ascoltare "dal vivo" e' impossibile, e nessuna ottimizzazione lo cambia. La
# fluidita' non si ottiene generando piu' in fretta, ma arrivando PRIMA.
#
# Il lettore sa quali frasi verranno dopo (le ha gia' divise per evidenziarle) e
# le manda qui. Il server le prepara in sottofondo mentre l'utente ascolta la
# frase corrente. Quando ci arrivera', saranno gia' in cache: 0,02 secondi.
#
# La coda e' limitata apposta: un lettore impazzito non deve poter accodare
# mezzo libro e tenere occupata la macchina per ore.
_CODA_MAX = 400
_coda_prepara = collections.deque()
_coda_viste = set()                 # evita di accodare due volte la stessa frase
_coda_segnale = threading.Condition()
_coda_avviata = False


def _operaio_prepara():
    """Genera in sottofondo, una frase alla volta, in ordine di lettura.

    Un solo operaio, non un gruppo: la macchina ha due CPU e il modello ne usa
    gia' due. Aggiungerne altri farebbe solo litigare i processi fra loro, e
    rallenterebbe anche la frase che l'utente sta aspettando davvero.
    """
    while True:
        with _coda_segnale:
            while not _coda_prepara:
                _coda_segnale.wait()
            testo, voce = _coda_prepara.popleft()
        try:
            tts.sintetizza(testo, voce, store=store, cloud=_cloud_attivo())
        except tts.TettoRaggiunto:
            # tetto giornaliero: si svuota la coda, riprovare non serve
            with _coda_segnale:
                _coda_prepara.clear()
                _coda_viste.clear()
            logger.info('Preparazione sospesa: tetto giornaliero raggiunto')
        except Exception:
            # una frase che non si riesce a generare non deve fermare le altre
            logger.debug('Preparazione fallita per una frase', exc_info=True)


def _avvia_operaio():
    global _coda_avviata
    if not _coda_avviata:
        _coda_avviata = True
        threading.Thread(target=_operaio_prepara, name='prepara',
                         daemon=True).start()


@app.route('/api/voce/prepara', methods=['POST'])
@auth.login_required
def api_voce_prepara():
    """Accoda le prossime frasi. Risponde subito, non aspetta la generazione."""
    utente = auth.current_user()
    if rate_limited('prepara', 120, 300, subject=utente):
        return jsonify({'error': 'Troppe richieste.'}), 429

    dati = request.get_json(silent=True) or {}
    frasi = dati.get('frasi') or []
    if not isinstance(frasi, list):
        return jsonify({'error': 'Serve un elenco di frasi.'}), 400
    voce = tts.voce_valida(dati.get('voce'))

    _avvia_operaio()
    accodate = gia_pronte = 0
    for f in frasi[:100]:
        if not isinstance(f, str):
            continue
        testo = tts.normalizza(f)
        if not testo or len(testo) > tts.MAX_CARATTERI_RICHIESTA:
            continue
        # gia' in cache: non c'e' niente da preparare
        if os.path.exists(tts.percorso(tts.chiave(testo, voce,
                                                 tts.profilo_locale()))):
            gia_pronte += 1
            continue
        with _coda_segnale:
            if (testo, voce) in _coda_viste or len(_coda_prepara) >= _CODA_MAX:
                continue
            _coda_prepara.append((testo, voce))
            _coda_viste.add((testo, voce))
            _coda_segnale.notify()
        accodate += 1

    with _coda_segnale:
        in_coda = len(_coda_prepara)
    return jsonify({'accodate': accodate, 'gia_pronte': gia_pronte,
                    'in_coda': in_coda})


@app.route('/api/anteprima/<job_id>')
@auth.login_required
def api_anteprima(job_id):
    """Le frasi gia' tradotte, mentre la traduzione e' ancora in corso.

    E' quello che permette di premere Play senza aspettare il libro intero: il
    traduttore salva ogni blocco appena lo finisce, e qui si legge fin dove e'
    arrivato. Si ferma al primo blocco mancante — chi ascolta va in linea retta,
    e un buco in mezzo sarebbe peggio di una coda piu' corta.
    """
    utente = auth.current_user()
    job = store.get_job(job_id)
    if not job or job.get('owner') != utente:
        return jsonify({'error': 'Libro non trovato.'}), 404

    da = int(request.args.get('da') or 0)
    quante = min(int(request.args.get('quante') or 60), 200)
    try:
        import anteprima
        frasi, blocchi_pronti, blocchi_totali, complete = anteprima.frasi_pronte(
            job, store, tts)
    except Exception:
        logger.exception('anteprima fallita per %s', job_id)
        return jsonify({'error': 'Non riesco a leggere il testo tradotto.'}), 500

    # Un libro gia' finito non ha piu' l'originale su disco (e' un file di
    # passaggio) e i blocchi non si possono ricostruire: si dice com'e' invece
    # di mostrare una pagina vuota che sembra un guasto.
    if job.get('status') == 'completed' and not blocchi_totali:
        return jsonify({
            'titolo': job.get('original_filename') or job_id,
            'stato': 'completed', 'gia_pronto': True,
            'messaggio': 'Questo libro e\' gia\' tradotto: aprilo nel lettore.',
            'frasi': [], 'frasi_totali': 0, 'blocchi_pronti': 0,
            'blocchi_totali': 0, 'traduzione_finita': True, 'da': 0,
            'progresso': 1.0,
        })

    return jsonify({
        'titolo': job.get('original_filename') or job_id,
        'stato': job.get('status'),
        'progresso': round(float(job.get('progress') or 0), 3),
        'blocchi_pronti': blocchi_pronti,
        'blocchi_totali': blocchi_totali,
        'traduzione_finita': complete,
        'frasi_totali': len(frasi),
        'frasi': frasi[da:da + quante],
        'da': da,
    })


@app.route('/ascolta/<job_id>')
@auth.login_required
def pagina_ascolta(job_id):
    job = store.get_job(job_id)
    if not job or job.get('owner') != auth.current_user():
        return redirect('/libreria')
    return render_template('ascolta.html', job_id=job_id,
                           titolo=job.get('original_filename') or job_id)


# --- pagina tradotta su richiesta (PDF scansionati) --------------------------
#
# Un libro illustrato scansionato non si traduce tutto insieme: il testo e'
# dentro la fotografia della pagina, e ogni tentativo in blocco ha prodotto
# pagine illeggibili. Qui si traduce la pagina che l'utente sta GUARDANDO,
# quando la chiede, e la si conserva: la seconda volta e' gratis.
#
# Come si fa a essere veloci su una macchina lenta: il lavoro e' in due parti,
# il riconoscimento del testo (lento, ma GRATIS) e la traduzione (a pagamento,
# ~10 s). Un operaio in sottofondo prepara il riconoscimento delle pagine
# SUCCESSIVE a quella appena tradotta, cosi' alla richiesta resta solo la
# traduzione. E con "traduci le prossime N" l'operaio le traduce tutte in
# ordine, mentre l'utente legge.
#
# Ogni pagina gira in un processo a parte: il riconoscimento mangia memoria, e
# su una macchina da 1 GB se muore deve morire lui, non il portale.
PAGINE_DIR = os.path.join(BASE_DIR, 'data', 'pagine')
PAGINE_PREPARA_AVANTI = int(os.environ.get('PAGINE_PREPARA_AVANTI', '3'))  # OCR gratis in anticipo
PAGINE_MAX_CODA = int(os.environ.get('PAGINE_MAX_CODA', '60'))             # traduzioni in coda per utente
PAGINE_MAX_RICHIESTA = 20                                                    # "prossime N": N massimo
PAGINE_TIMEOUT = int(os.environ.get('PAGINE_TIMEOUT', '300'))

_pagine_lock = threading.Lock()
_pagine_evento = threading.Condition(_pagine_lock)
_pagine_coda = collections.deque()      # dict(job, n, owner, pdf, modo)   modo: 'traduci' | 'ocr'
_pagine_in_corso = None                 # (job, n, modo) che l'operaio sta lavorando
_pagine_dirette = set()                 # (job, n) in lavorazione da una richiesta diretta
_pagine_operaio = None
_pagine_semaforo = threading.BoundedSemaphore(2)   # richieste dirette insieme: la macchina ha 1 GB
_pagine_conteggio = {}                  # job -> numero di pagine del PDF


def _pagina_file(job_id, n):
    """Il file della pagina tradotta se esiste (jpg, o png delle prime versioni),
    altrimenti il percorso jpg da creare."""
    base = os.path.join(PAGINE_DIR, job_id, '%04d' % n)
    for ext in ('.jpg', '.png'):
        if os.path.exists(base + ext):
            return base + ext
    return base + '.jpg'


def _pagina_pronta(job_id, n):
    return os.path.exists(_pagina_file(job_id, n))


def _pagina_ocr_pronto(job_id, n):
    return os.path.exists(os.path.join(PAGINE_DIR, job_id, '%04d.ocr.json' % n))


def _numero_pagine(job_id, pdf):
    if job_id not in _pagine_conteggio:
        try:
            import fitz
            with fitz.open(pdf) as d:
                _pagine_conteggio[job_id] = len(d)
        except Exception:
            return None
    return _pagine_conteggio[job_id]


def _lavora_pagina(pdf, job_id, n, modo, in_sottofondo):
    """Esegue pagina_tradotta.py in un processo a parte. Ritorna (ok, nota)."""
    import subprocess
    uscita = os.path.join(PAGINE_DIR, job_id, '%04d.jpg' % n)
    cmd = [sys.executable, os.path.join(BASE_DIR, 'pagina_tradotta.py'), pdf, str(n), uscita]
    if modo == 'ocr':
        cmd.append('--solo-ocr')
    if in_sottofondo and os.path.exists('/usr/bin/nice'):
        # il lavoro anticipato non deve rallentare chi sta usando il portale
        cmd = ['/usr/bin/nice', '-n', '10'] + cmd
    # Sessione propria: al timeout si uccide TUTTO il gruppo (python E tesseract),
    # altrimenti tesseract restava orfano a mangiare CPU per minuti.
    import signal
    proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=PAGINE_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        return False, 'tempo scaduto (%ds)' % PAGINE_TIMEOUT

    class _R:
        pass
    r = _R(); r.returncode, r.stdout, r.stderr = proc.returncode, out, err
    tempi = next((l for l in reversed((r.stderr or '').splitlines()) if 'tempi:' in l), '')
    if r.returncode != 0:
        return False, (r.stderr or '')[-400:]
    if modo == 'traduci' and not os.path.exists(uscita):
        return False, 'il processo non ha prodotto la pagina'
    return True, tempi.strip()


def _in_coda(job_id, n, modo=None):
    return any(e['job'] == job_id and e['n'] == n and (modo is None or e['modo'] == modo)
               for e in _pagine_coda)


def _togli_dalla_coda(job_id, n):
    global _pagine_coda
    _pagine_coda = collections.deque(e for e in _pagine_coda if not (e['job'] == job_id and e['n'] == n))


def _dimentica_pagine(job_id):
    """Col libro se ne vanno anche le pagine tradotte, il testo riconosciuto e
    la coda: sono SUE, e senza il libro sarebbero solo spazio perso."""
    global _pagine_coda
    with _pagine_evento:
        _pagine_coda = collections.deque(e for e in _pagine_coda if e['job'] != job_id)
        _pagine_conteggio.pop(job_id, None)
    import shutil
    shutil.rmtree(os.path.join(PAGINE_DIR, job_id), ignore_errors=True)


def _svuota_coda_traduzioni(job_id):
    """Da chiamare col lock preso: toglie le traduzioni in attesa di questo libro."""
    global _pagine_coda
    _pagine_coda = collections.deque(e for e in _pagine_coda
                                     if not (e['job'] == job_id and e['modo'] == 'traduci'))


def _accoda_pagina(job_id, n, owner, pdf, modo):
    """Da chiamare col lock preso. Non duplica, e non mette un OCR dove c'e' gia' una traduzione."""
    if (job_id, n) in _pagine_dirette:
        return False                              # la sta gia' facendo una richiesta diretta
    if modo == 'traduci':
        if _pagina_pronta(job_id, n) or _in_coda(job_id, n, 'traduci') or _pagine_in_corso == (job_id, n, 'traduci'):
            return False
        _togli_dalla_coda(job_id, n)              # un eventuale OCR in attesa e' superato
    else:
        if (_pagina_pronta(job_id, n) or _pagina_ocr_pronto(job_id, n) or _in_coda(job_id, n)
                or (_pagine_in_corso and _pagine_in_corso[:2] == (job_id, n))):
            return False
    _pagine_coda.append({'job': job_id, 'n': n, 'owner': owner, 'pdf': pdf, 'modo': modo})
    return True


def _operaio_pagine():
    global _pagine_in_corso
    while True:
        with _pagine_evento:
            while not _pagine_coda:
                _pagine_evento.wait()
            e = _pagine_coda.popleft()
            _pagine_in_corso = (e['job'], e['n'], e['modo'])
        try:
            if e['modo'] == 'traduci' and _pagina_pronta(e['job'], e['n']):
                continue
            if e['modo'] == 'ocr' and (_pagina_ocr_pronto(e['job'], e['n']) or _pagina_pronta(e['job'], e['n'])):
                continue
            if not os.path.exists(e['pdf']):
                continue
            with _pagine_lock:
                if (e['job'], e['n']) in _pagine_dirette:
                    continue                      # ci pensa la richiesta diretta, gia' in corso
            ok, nota = _lavora_pagina(e['pdf'], e['job'], e['n'], e['modo'], in_sottofondo=True)
            if ok:
                logger.info('Pagina %s p.%d (%s) pronta in sottofondo %s', e['job'][:8], e['n'], e['modo'], nota)
            else:
                logger.warning('Pagina %s p.%d (%s) fallita in sottofondo: %s', e['job'][:8], e['n'], e['modo'], nota)
        except Exception:
            logger.exception('operaio pagine')
        finally:
            with _pagine_evento:
                _pagine_in_corso = None
                _pagine_evento.notify_all()


def _avvia_operaio_pagine():
    global _pagine_operaio
    with _pagine_lock:
        if _pagine_operaio is None or not _pagine_operaio.is_alive():
            _pagine_operaio = threading.Thread(target=_operaio_pagine, name='pagine', daemon=True)
            _pagine_operaio.start()


def _stato_pagine(job_id):
    cartella = os.path.join(PAGINE_DIR, job_id)
    pronte = set()
    if os.path.isdir(cartella):
        for f in os.listdir(cartella):
            # solo "0016.jpg"/"0016.png": i provvisori dell'OCR ("0016.ocr.1234.png")
            # finivano contati come pagine pronte e il lettore chiedeva un jpg inesistente
            m = re.match(r'^(\d{4})\.(jpg|png)$', f)
            if m:
                pronte.add(int(m.group(1)))
    with _pagine_lock:
        in_coda = sorted(e['n'] for e in _pagine_coda if e['job'] == job_id and e['modo'] == 'traduci')
        in_corso = None
        if _pagine_in_corso and _pagine_in_corso[0] == job_id and _pagine_in_corso[2] == 'traduci':
            in_corso = _pagine_in_corso[1]
        dirette = sorted(n for (j, n) in _pagine_dirette if j == job_id)
    if in_corso is None and dirette:
        in_corso = dirette[0]
    return {'pronte': sorted(pronte), 'in_coda': in_coda, 'in_corso': in_corso}


def _pdf_del_libro(job_id):
    """(job, pdf, errore): controlla proprietario e che il libro sia un PDF ancora sul disco."""
    job = store.get_job(job_id)
    if not job or job.get('owner') != auth.current_user():
        return None, None, (jsonify({'error': 'Libro non trovato.'}), 404)
    if (job.get('file_type') or '').lower() != 'pdf':
        return job, None, (jsonify({'error': 'Solo per i PDF.'}), 400)
    pdf = job.get('output_path') or job.get('input_path')
    if not pdf or not os.path.exists(pdf):
        return job, None, (jsonify({'error': 'Il file del libro non c\'e\' piu\'.'}), 404)
    return job, pdf, None


@app.route('/api/pagina/<job_id>/tradotte')
@auth.login_required
def api_pagine_tradotte(job_id):
    job, pdf, errore = _pdf_del_libro(job_id)
    if errore and not job:
        return errore
    return jsonify(_stato_pagine(job_id))


@app.route('/api/pagina/<job_id>/<int:n>.jpg')
@app.route('/api/pagina/<job_id>/<int:n>.png')
@auth.login_required
def api_pagina_immagine(job_id, n):
    job = store.get_job(job_id)
    if not job or job.get('owner') != auth.current_user():
        return jsonify({'error': 'Libro non trovato.'}), 404
    p = _pagina_file(job_id, n)
    if not os.path.exists(p):
        return jsonify({'error': 'Pagina non ancora tradotta.'}), 404
    resp = send_file(p, mimetype='image/jpeg' if p.endswith('.jpg') else 'image/png', conditional=True)
    resp.headers['Cache-Control'] = 'private, max-age=604800'
    return resp


@app.route('/api/pagina/<job_id>/<int:n>/traduci', methods=['POST'])
@auth.login_required
def api_pagina_traduci(job_id, n):
    """Traduce la pagina `n` (da 1) e risponde con l'indirizzo dell'immagine.

    Se l'operaio la sta gia' facendo si aspetta lui; se e' solo in coda la si
    fa SUBITO qui, con la precedenza a chi sta guardando la pagina.
    """
    utente = auth.current_user()
    job, pdf, errore = _pdf_del_libro(job_id)
    if errore:
        return errore
    if rate_limited('pagina', 60, 600, subject=utente):
        return jsonify({'error': 'Troppe pagine in poco tempo, rallenta un attimo.'}), 429
    totale = _numero_pagine(job_id, pdf)
    if n < 1 or (totale and n > totale):
        return jsonify({'error': 'Pagina inesistente.'}), 400

    def risposta(in_cache):
        return jsonify({'url': '/api/pagina/%s/%d.jpg' % (job_id, n), 'in_cache': in_cache})

    if _pagina_pronta(job_id, n):
        return risposta(True)

    chiave = (job_id, n)
    scadenza = time.time() + PAGINE_TIMEOUT
    with _pagine_evento:
        while True:
            if _pagina_pronta(job_id, n):
                return risposta(True)
            occupata = ((_pagine_in_corso is not None and _pagine_in_corso[:2] == chiave)
                        or chiave in _pagine_dirette)
            if not occupata:
                break
            # l'operaio (o un'altra richiesta) ci sta gia' lavorando: si aspetta lui
            resto = scadenza - time.time()
            if resto <= 0:
                return jsonify({'error': 'La pagina ci sta mettendo troppo: riprova.'}), 504
            _pagine_evento.wait(timeout=min(resto, 5))
        _togli_dalla_coda(job_id, n)             # se era in coda, la facciamo ora
        _pagine_dirette.add(chiave)
    try:
        with _pagine_semaforo:
            ok, nota = _lavora_pagina(pdf, job_id, n, 'traduci', in_sottofondo=False)
        if not ok:
            logger.error('Pagina %d di %s non tradotta: %s', n, job_id, nota)
            if 'tempo scaduto' in nota:
                return jsonify({'error': 'La pagina ci sta mettendo troppo: riprova.'}), 504
            return jsonify({'error': 'Non sono riuscito a tradurre questa pagina.'}), 502
        logger.info('Pagina tradotta: %s p.%d %s', job_id[:8], n, nota)
    finally:
        with _pagine_evento:
            _pagine_dirette.discard(chiave)
            # le prossime pagine: riconoscimento GRATIS in anticipo, cosi' alla
            # prossima richiesta resta solo la traduzione
            for k in range(n + 1, n + 1 + PAGINE_PREPARA_AVANTI):
                if totale and k > totale:
                    break
                _accoda_pagina(job_id, k, utente, pdf, 'ocr')
            _pagine_evento.notify_all()
    _avvia_operaio_pagine()
    return risposta(False)


@app.route('/api/pagina/<job_id>/coda', methods=['POST', 'DELETE'])
@auth.login_required
def api_pagina_coda(job_id):
    """POST {da, quante}: traduce in sottofondo le prossime `quante` pagine da `da`.
    DELETE: toglie dalla coda le pagine di questo libro non ancora iniziate."""
    utente = auth.current_user()
    job, pdf, errore = _pdf_del_libro(job_id)
    if errore:
        return errore
    if request.method == 'DELETE':
        with _pagine_evento:
            _svuota_coda_traduzioni(job_id)
        return jsonify(_stato_pagine(job_id))
    if rate_limited('pagina-coda', 30, 600, subject=utente):
        return jsonify({'error': 'Troppe richieste in poco tempo, rallenta un attimo.'}), 429
    dati = request.get_json(silent=True) or {}
    try:
        da = int(dati.get('da') or 1)
        quante = int(dati.get('quante') or 10)
    except (TypeError, ValueError, OverflowError):
        return jsonify({'error': 'Richiesta non valida.'}), 400
    quante = max(1, min(PAGINE_MAX_RICHIESTA, quante))
    totale = _numero_pagine(job_id, pdf)
    if da < 1 or (totale and da > totale):
        return jsonify({'error': 'Pagina inesistente.'}), 400
    fine = min(da + quante - 1, totale) if totale else da + quante - 1
    with _pagine_evento:
        gia = sum(1 for e in _pagine_coda if e['owner'] == utente and e['modo'] == 'traduci')
        if gia + (fine - da + 1) > PAGINE_MAX_CODA:
            return jsonify({'error': 'Hai gia\' %d pagine in coda: aspetta che finiscano.' % gia}), 429
        aggiunte = [k for k in range(da, fine + 1) if _accoda_pagina(job_id, k, utente, pdf, 'traduci')]
        _pagine_evento.notify_all()
    _avvia_operaio_pagine()
    logger.info('Coda pagine %s: +%d (%s)', job_id[:8], len(aggiunte), utente)
    stato = _stato_pagine(job_id)
    stato['aggiunte'] = aggiunte
    return jsonify(stato)


def _linearizza_pdf(percorso):
    """Riscrive il PDF con i dizionari di tutte le pagine in testa al file
    (pdf_lineare.py): pdf.js li legge tutti all'apertura, e cosi' gli bastano
    2-3 richieste invece di 190. NON e' la linearizzazione classica, che li
    sparge accanto alle immagini e rende l'apertura lentissima. In un processo
    a parte (memoria), mai bloccante per chi chiama: se fallisce il file resta
    com'era."""
    import subprocess
    modulo = os.path.join(BASE_DIR, 'pdf_lineare.py')
    if not percorso or not percorso.lower().endswith('.pdf') or not os.path.exists(modulo):
        return False
    cmd = [sys.executable, modulo, percorso]
    if os.path.exists('/usr/bin/nice'):
        cmd = ['/usr/bin/nice', '-n', '10'] + cmd
    try:
        r = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=900)
        esito = (r.stdout or '').strip().splitlines()
        logger.info('Preparazione PDF %s: rc=%d %s', os.path.basename(percorso)[:40], r.returncode,
                    esito[-1][:160] if esito else (r.stderr or '')[-160:])
        return r.returncode == 0
    except Exception as e:
        logger.warning('Preparazione PDF fallita per %s: %s', os.path.basename(percorso)[:40], str(e)[:120])
        return False


@app.route('/api/spesa')
@auth.login_required
def api_spesa():
    """Quanto e' costato ascoltare, e quanto resta.

    Serve a non dover mai chiedere "ma quanto sto spendendo?": il numero sta
    dove si ascolta. Se il motore a consumo non e' configurato risponde
    comunque, dicendo che e' tutto gratuito — non e' un errore, e' uno stato.
    """
    try:
        import voce_cloud as vc
    except Exception:
        return jsonify({'attivo': False, 'motivo': 'modulo non disponibile'})
    if not vc.disponibile():
        return jsonify({'attivo': False, 'motivo': 'nessun token: tutto gratuito'})

    mese = time.strftime('%Y-%m')
    r = store.cloud_riepilogo(mese)
    speso = max(float(r.get('speso') or 0), float(r.get('prenotato') or 0))
    residuo = max(0.0, vc.TETTO_MENSILE - speso)
    avviso = float(os.environ.get('TTS_CLOUD_WARN_USD', '8'))
    avviso2 = float(os.environ.get('TTS_CLOUD_WARN2_USD', '10'))
    return jsonify({
        'attivo': True,
        'mese': mese,
        'speso': round(speso, 4),
        'tetto': vc.TETTO_MENSILE,
        'residuo': round(residuo, 2),
        'caratteri': r.get('caratteri') or 0,
        'richieste': r.get('richieste') or 0,
        # a quante ore di ascolto corrisponde cio' che resta: e' il numero che
        # dice davvero qualcosa, molto piu' dei dollari
        'ore_residue': round(residuo / (3600 * 17.6 * vc.PREZZO_PER_CARATTERE), 1),
        'costo_ora': round(3600 * 17.6 * vc.PREZZO_PER_CARATTERE, 3),
        'allarme': ('forte' if speso >= avviso2 else
                    'attenzione' if speso >= avviso else None),
    })


@app.route('/api/voce/ferma', methods=['POST'])
@auth.login_required
def api_voce_ferma():
    """Svuota la coda di preparazione: l'utente ha messo in pausa.

    Senza questo, chi apre un libro, ascolta due minuti e chiude si ritrova ad
    aver pagato la generazione di tutto cio' che era stato annunciato. Il
    precaricamento serve a non farlo aspettare, non a comprargli il libro.
    """
    with _coda_segnale:
        quante = len(_coda_prepara)
        _coda_prepara.clear()
        _coda_viste.clear()
    if quante:
        logger.info('Preparazione fermata: %d frasi tolte dalla coda', quante)
    return jsonify({'tolte': quante})


@app.route('/api/frasi', methods=['POST'])
@auth.login_required
def api_frasi():
    """Divide in frasi il testo di una pagina PDF, dicendo dove ognuna comincia.

    Le regole dell'italiano (abbreviazioni, numeri col punto, iniziali puntate)
    stanno in un posto solo, qui: duplicarle nel browser avrebbe significato
    correggerle due volte, e sbagliarle una.
    """
    dati = request.get_json(silent=True) or {}
    testo = dati.get('testo') or ''
    if len(testo) > 200000:
        return jsonify({'error': 'Testo troppo lungo'}), 413
    return jsonify({'frasi': tts.frasi_con_posizioni(testo)})


@app.route('/api/book/<task_id>/ascolto', methods=['GET', 'PUT', 'POST'])
@auth.login_required
def api_ascolto(task_id):
    """Voce e velocita' scelte per questo libro. La POSIZIONE non sta qui:
    e' una sola, in /progress, condivisa fra lettura e ascolto."""
    owner = auth.current_user()
    if not store.get_job(task_id, owner=owner):
        return jsonify({'error': 'Non trovato'}), 404
    if request.method == 'GET':
        return jsonify(store.get_ascolto(task_id, owner))
    dati = request.get_json(silent=True) or {}
    voce = dati.get('voce')
    return jsonify(store.save_ascolto(task_id, owner,
                                      voce=tts.voce_valida(voce) if voce else None,
                                      velocita=dati.get('velocita')))


# POST oltre a PUT: quando si chiude la pagina la posizione viene inviata con
# navigator.sendBeacon, che puo' usare soltanto POST. Senza questo il salvataggio
# di chiusura rispondeva 405 e l'ultima posizione andava persa in silenzio.
@app.route('/api/book/<task_id>/progress', methods=['GET', 'PUT', 'POST'])
@auth.login_required
def api_progress(task_id):
    owner = auth.current_user()
    if not store.get_job(task_id, owner=owner):
        return jsonify({'error': 'Non trovato'}), 404

    if request.method == 'GET':
        return jsonify(store.get_reading(task_id, owner))

    data = request.get_json(silent=True) or {}
    store.save_reading(task_id, owner, data.get('location'),
                       data.get('percent', 0), data.get('label'))
    return jsonify({'ok': True})


@app.route('/api/book/<task_id>/annotations', methods=['GET', 'POST'])
@auth.login_required
def api_annotations(task_id):
    owner = auth.current_user()
    if not store.get_job(task_id, owner=owner):
        return jsonify({'error': 'Non trovato'}), 404

    if request.method == 'GET':
        return jsonify({'annotations': store.list_annotations(owner, job_id=task_id)})

    data = request.get_json(silent=True) or {}
    testo = (data.get('text') or '').strip()
    if not testo:
        return jsonify({'error': 'Nessun testo selezionato'}), 400

    ann_id = str(uuid.uuid4())
    store.add_annotation(
        id=ann_id, job_id=task_id, owner=owner,
        location=data.get('location') or '', text=testo[:8000],
        note=(data.get('note') or '')[:4000],
        color=data.get('color') or 'giallo',
        chapter=(data.get('chapter') or '')[:200],
        percent=data.get('percent'),
    )
    return jsonify({'ok': True, 'id': ann_id})


@app.route('/api/annotations/<ann_id>', methods=['PATCH', 'DELETE'])
@auth.login_required
def api_annotation(ann_id):
    owner = auth.current_user()
    if request.method == 'DELETE':
        return (jsonify({'ok': True}) if store.delete_annotation(ann_id, owner)
                else (jsonify({'error': 'Non trovata'}), 404))
    data = request.get_json(silent=True) or {}
    ok = store.update_annotation(ann_id, owner, note=data.get('note'),
                                 color=data.get('color'))
    return (jsonify({'ok': True}) if ok else (jsonify({'error': 'Non trovata'}), 404))


@app.route('/api/annotations')
@auth.login_required
def api_all_annotations(  ):
    owner = auth.current_user()
    voci = store.list_annotations(owner, search=request.args.get('q'))
    titoli = {j['id']: j['original_filename'] for j in store.list_jobs(owner, limit=200)}
    for v in voci:
        v['book_title'] = titoli.get(v['job_id'], '(libro rimosso)')
    return jsonify({'annotations': voci})


@app.route('/appunti')
@auth.page_login_required
def appunti():
    return render_template('appunti.html')


@app.route('/api/diagnostica', methods=['POST'])
@auth.login_required
def api_diagnostica():
    """
    Il lettore racconta al server perche' non e' riuscito a mostrare il libro.

    Senza questo l'unica fonte era "esce tutto bianco": impossibile capire se
    fosse il browser, la dimensione della finestra o il libro.
    """
    d = request.get_json(silent=True) or {}
    logger.warning('DIAGNOSTICA LETTORE  %s', {
        k: str(v)[:300] for k, v in d.items()
    })
    return jsonify({'ok': True})


@app.errorhandler(413)
def too_large(_e):
    return jsonify({'error': 'File troppo grande. Limite: %d MB.' % MAX_UPLOAD_MB}), 413


# La libreria a cartelle e tag (dal branch claude/intelligent-clarke-0d4k7s),
# ACCANTO a /libreria, che resta com'era: vedi libreria_ponte.py. Se non parte,
# il portale parte lo stesso, con tutte le funzioni di prima.
try:
    import libreria_ponte
    libreria_ponte.init(app, BASE_DIR, prepara_pdf=_linearizza_pdf, dimentica_pagine=_dimentica_pagine)
except Exception:
    logger.exception('Libreria a cartelle non disponibile: il portale continua senza')

# I worker devono partire sia sotto gunicorn sia con `python app.py`.
start_background()


if __name__ == '__main__':
    print('\n' + '=' * 60)
    print('  ULTIMATE TRANSLATOR')
    print('  EPUB - PDF - DOCX - TXT  |  OpenAI + Anthropic')
    print('=' * 60)

    keys = _check_api_keys()
    print('\n  OpenAI:    %s' % ('CONNESSO' if keys['openai'] else 'NON CONFIGURATO'))
    print('  Anthropic: %s' % ('CONNESSO' if keys['anthropic'] else 'NON CONFIGURATO'))
    print('  Email OTP: %s' % ('SMTP CONFIGURATO' if auth.smtp_configured()
                               else 'SMTP MANCANTE (codice nel log)'))
    print('  Accesso consentito a: %s' % ', '.join(sorted(auth.allowed_emails())))

    if not keys['openai'] and not keys['anthropic']:
        print('\n  ATTENZIONE: nessuna chiave API configurata!')

    port = int(os.environ.get('PORT', 5001))
    print('\n  Apri il browser: http://localhost:%d\n' % port)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
