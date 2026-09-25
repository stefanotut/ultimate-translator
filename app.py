"""
ULTIMATE TRANSLATOR - Main Application
The best AI-powered EPUB and PDF translator.
Supports OpenAI and Anthropic providers.
"""

import os
import uuid
import time
import logging
import threading
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

import library_db
import library

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {'epub', 'pdf'}

# Personal library (SQLite + files, see LIBRARY_DATA_DIR)
library_db.init()
app.register_blueprint(library.bp)

# In-memory task store (the running jobs of this worker process)
tasks = {}


class TranslationTask:
    def __init__(self, task_id, input_path, output_path, file_type,
                 source_lang, target_lang, original_filename,
                 provider, model, book_id=None, library_id=None):
        self.task_id = task_id
        self.input_path = input_path
        self.output_path = output_path
        self.file_type = file_type
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.original_filename = original_filename
        self.provider = provider
        self.model = model
        self.book_id = book_id
        self.library_id = library_id
        self.status = 'pending'
        self.progress = 0.0
        self.status_text = 'In attesa...'
        self.error = None
        self.logs = []
        self.output_filename = None
        # Persisted so every gunicorn worker can answer status/download requests
        library_db.create_translation(
            task_id,
            library_id=library_id,
            book_id=book_id,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=provider,
            model=model,
            file_type=file_type,
            input_path=library_db.stored_path(input_path),
            output_path=library_db.stored_path(output_path),
            status_text=self.status_text,
        )

    def add_log(self, message, level='info'):
        self.logs.append({
            'message': message,
            'level': level,
            'time': time.time()
        })
        logger.info(f"[{self.task_id[:8]}] {message}")
        self.save()

    def save(self):
        try:
            library_db.update_translation(
                self.task_id,
                status=self.status,
                progress=self.progress,
                status_text=self.status_text,
                error=self.error,
                output_filename=self.output_filename,
                logs=self.logs,
                logs_total=len(self.logs),
            )
        except Exception as e:
            logger.warning(f"[{self.task_id[:8]}] Could not persist translation state: {e}")

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'status': self.status,
            'progress': self.progress,
            'status_text': self.status_text,
            'error': self.error,
            'logs': self.logs,
            'logs_total': len(self.logs),
            'output_filename': self.output_filename,
            'book_id': self.book_id,
        }


def _status_from_record(record):
    """Status payload for a job running in another worker (or already finished)."""
    return {
        'task_id': record['id'],
        'status': record['status'],
        'progress': record['progress'],
        'status_text': record['status_text'],
        'error': record['error'],
        'logs': record['logs'],
        'logs_total': record['logs_total'],
        'output_filename': record['output_filename'],
        'book_id': record['book_id'],
    }


def get_extension(filename):
    return filename.rsplit('.', 1)[1].lower() if '.' in filename else ''


def _check_api_keys():
    """Check which API keys are configured."""
    result = {}
    openai_key = os.getenv('OPENAI_API_KEY', '')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY', '')

    result['openai'] = bool(openai_key and not openai_key.startswith('sk-xxxx'))
    result['anthropic'] = bool(anthropic_key and not anthropic_key.startswith('sk-ant-xxxx'))

    return result


def run_translation(task):
    """Run translation in background thread."""
    try:
        task.status = 'processing'
        task.add_log(f'Inizio traduzione: {task.original_filename}')
        task.add_log(f'Da {task.source_lang} a {task.target_lang}')
        task.add_log(f'Modello: {task.model} ({task.provider})')

        def progress_callback(progress, status_text):
            task.progress = progress
            task.status_text = status_text
            task.add_log(status_text)

        if task.file_type == 'epub':
            from epub_handler import translate_epub
            task.add_log('Formato: EPUB - Analisi struttura libro...')
            translate_epub(
                task.input_path,
                task.output_path,
                source_lang=task.source_lang,
                target_lang=task.target_lang,
                provider=task.provider,
                model=task.model,
                progress_callback=progress_callback
            )
        elif task.file_type == 'pdf':
            from pdf_handler import translate_pdf
            task.add_log('Formato: PDF - Analisi pagine e layout...')
            translate_pdf(
                task.input_path,
                task.output_path,
                source_lang=task.source_lang,
                target_lang=task.target_lang,
                provider=task.provider,
                model=task.model,
                progress_callback=progress_callback
            )

        # Generate output filename
        name, ext = os.path.splitext(task.original_filename)
        task.output_filename = f"{name}_tradotto_{task.target_lang}{ext}"

        task.status = 'completed'
        task.progress = 1.0
        task.status_text = 'Completato!'
        task.add_log('Traduzione completata con successo!', 'success')

    except Exception as e:
        task.status = 'error'
        task.error = str(e)
        task.add_log(f'Errore: {str(e)}', 'error')
        logger.exception(f"Translation failed for task {task.task_id}")


# === Routes ===

@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({'error': 'File troppo grande. Massimo 100MB.'}), 413


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/models')
def api_models():
    """Return available models based on configured API keys."""
    from translator import MODEL_PRICING
    keys = _check_api_keys()

    models = []
    for model_id, info in MODEL_PRICING.items():
        provider = info['provider']
        if keys.get(provider):
            models.append({
                'id': model_id,
                'provider': provider,
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
        'default_model': 'claude-sonnet-4-20250514' if keys.get('anthropic') else (
            'gpt-4o' if keys.get('openai') else None
        ),
    })


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """
    Analyze a file and return stats + token count.
    No model parameter required - cost calculation happens client-side
    using pricing data from /api/models.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file caricato'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Nessun file selezionato'}), 400

    ext = get_extension(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Formato non supportato: .{ext}'}), 400

    # Save file temporarily
    temp_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename)
    temp_path = os.path.join(UPLOAD_DIR, f"analyze_{temp_id}_{safe_name}")

    try:
        file.save(temp_path)

        if ext == 'epub':
            from epub_handler import analyze_epub
            analysis = analyze_epub(temp_path)
            analysis['file_type'] = 'epub'
        elif ext == 'pdf':
            from pdf_handler import analyze_pdf
            analysis = analyze_pdf(temp_path)
            analysis['file_type'] = 'pdf'
        else:
            return jsonify({'error': 'Formato non supportato'}), 400

        return jsonify(analysis)

    except Exception as e:
        logger.error(f"File analysis failed: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


@app.route('/api/translate', methods=['POST'])
def api_translate():
    """Upload a file (or pick a book from the library) and start translation."""
    book_id = (request.form.get('book_id') or '').strip()
    save_to_library = request.form.get('save_to_library', '').lower() in ('1', 'true', 'on', 'yes')

    file = None
    if not book_id:
        if 'file' not in request.files:
            return jsonify({'error': 'Nessun file caricato'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'Nessun file selezionato'}), 400

        ext = get_extension(file.filename)
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({'error': f'Formato non supportato: .{ext}. Usa EPUB o PDF.'}), 400

    source_lang = request.form.get('source_lang', 'English')
    target_lang = request.form.get('target_lang', 'Italian')
    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', 'claude-sonnet-4-20250514')

    if source_lang == target_lang:
        return jsonify({'error': 'Lingua di partenza e di arrivo devono essere diverse'}), 400

    # Validate provider and model
    from translator import MODEL_PRICING
    if model not in MODEL_PRICING:
        return jsonify({'error': f'Modello non valido: {model}'}), 400

    model_info = MODEL_PRICING[model]
    if model_info['provider'] != provider:
        provider = model_info['provider']

    # Check API key
    keys = _check_api_keys()
    if not keys.get(provider):
        return jsonify({'error': f'Chiave API {provider} non configurata'}), 400

    task_id = str(uuid.uuid4())
    book = None
    library_id = None

    if book_id:
        # Translate a book that is already in the user's library
        library_id = library.current_library_id(create=False)
        book = library_db.get_book_record(library_id, book_id) if library_id else None
        if not book:
            return jsonify({'error': 'Libro non trovato nella tua libreria'}), 404
    elif save_to_library:
        # Keep the uploaded book in the library (auto-tagged); the translation is attached to it
        library_id = library.current_library_id()
        try:
            imported, _ = library.import_book(library_id, file, reuse_duplicate=True)
        except library_db.LibraryError as e:
            return jsonify({'error': e.message}), e.status
        book = library_db.get_book_record(library_id, imported['id'])

    if book:
        ext = book['file_type']
        input_path = library_db.abs_path(book['file_path'])
        output_path = library_db.abs_path(library_db.translation_rel(library_id, task_id, ext))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        original_filename = library.download_name(book['title'], ext)
        logger.info(f"Translating library book {book['id'][:8]}")
    else:
        # Save uploaded file
        safe_name = secure_filename(file.filename)
        input_path = os.path.join(UPLOAD_DIR, f"{task_id}_{safe_name}")
        output_path = os.path.join(OUTPUT_DIR, f"{task_id}_translated.{ext}")
        original_filename = file.filename

        file.save(input_path)
        logger.info(f"File saved: {input_path} ({os.path.getsize(input_path)} bytes)")

    # Create task
    task = TranslationTask(
        task_id=task_id,
        input_path=input_path,
        output_path=output_path,
        file_type=ext,
        source_lang=source_lang,
        target_lang=target_lang,
        original_filename=original_filename,
        provider=provider,
        model=model,
        book_id=book['id'] if book else None,
        library_id=library_id if book else None,
    )
    tasks[task_id] = task

    # Start translation in background
    thread = threading.Thread(target=run_translation, args=(task,), daemon=True)
    thread.start()

    return jsonify({
        'task_id': task_id,
        'status': 'started',
        'book_id': book['id'] if book else None,
    })


@app.route('/api/status/<task_id>')
def api_status(task_id):
    """Get translation status (from this worker, or from the shared job store)."""
    task = tasks.get(task_id)
    if task:
        return jsonify(task.to_dict())
    record = library_db.get_translation(task_id)
    if not record:
        return jsonify({'error': 'Task non trovato'}), 404
    return jsonify(_status_from_record(record))


@app.route('/api/download/<task_id>')
def api_download(task_id):
    """Download translated file."""
    task = tasks.get(task_id)
    if task:
        status, output_path, output_filename = task.status, task.output_path, task.output_filename
    else:
        record = library_db.get_translation(task_id)
        if not record:
            return jsonify({'error': 'Task non trovato'}), 404
        status = record['status']
        output_path = library_db.abs_path(record['output_path'])
        output_filename = record['output_filename']

    if status != 'completed':
        return jsonify({'error': 'Traduzione non ancora completata'}), 400

    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'File tradotto non trovato'}), 404

    return send_file(
        output_path,
        as_attachment=True,
        download_name=output_filename
    )


@app.route('/api/detect-language', methods=['POST'])
def api_detect_language():
    """
    Detect language of uploaded file.
    Accepts file upload, optional provider/model.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file caricato'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Nessun file selezionato'}), 400

    ext = get_extension(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Formato non supportato: .{ext}'}), 400

    # Determine which provider to use for detection
    keys = _check_api_keys()
    provider = request.form.get('provider', '')
    model = request.form.get('model', '')

    if not provider:
        if keys.get('anthropic'):
            provider = 'anthropic'
            model = model or 'claude-haiku-3-5-20241022'
        elif keys.get('openai'):
            provider = 'openai'
            model = model or 'gpt-4o-mini'
        else:
            return jsonify({'error': 'Nessuna chiave API configurata'}), 400

    if not model:
        model = 'claude-haiku-3-5-20241022' if provider == 'anthropic' else 'gpt-4o-mini'

    # Save file temporarily
    temp_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename)
    temp_path = os.path.join(UPLOAD_DIR, f"detect_{temp_id}_{safe_name}")

    try:
        file.save(temp_path)
        text_sample = ""

        if ext == 'epub':
            try:
                import ebooklib
                from ebooklib import epub as epub_lib
                from bs4 import BeautifulSoup

                book = epub_lib.read_epub(temp_path, options={'ignore_ncx': True})
                documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
                for doc_item in documents[:3]:
                    content = doc_item.get_content().decode('utf-8', errors='replace')
                    soup = BeautifulSoup(content, 'html.parser')
                    for tag in soup.find_all(['script', 'style', 'meta', 'link']):
                        tag.decompose()
                    text = soup.get_text(separator=' ', strip=True)
                    if len(text) > 50:
                        text_sample = text[:1000]
                        break
            except Exception as e:
                logger.warning(f"EPUB text extraction failed: {e}")

        elif ext == 'pdf':
            try:
                from pdf_handler import extract_text_sample
                text_sample = extract_text_sample(temp_path, max_chars=1000)
            except Exception as e:
                logger.warning(f"PDF text extraction failed: {e}")

        if not text_sample or len(text_sample.strip()) < 20:
            return jsonify({'error': 'Non e stato possibile estrarre testo sufficiente per il rilevamento'}), 400

        from translator import detect_language
        detected = detect_language(text_sample, provider=provider, model=model)

        if detected == "Unknown":
            return jsonify({'error': 'Lingua non riconosciuta'}), 400

        return jsonify({'language': detected})

    except Exception as e:
        logger.error(f"Language detection failed: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  ULTIMATE TRANSLATOR")
    print("  Il miglior traduttore AI di EPUB e PDF")
    print("  Powered by OpenAI + Anthropic")
    print("=" * 60)

    keys = _check_api_keys()
    print()
    if keys['openai']:
        print("  OpenAI:    CONNESSO")
    else:
        print("  OpenAI:    NON CONFIGURATO")

    if keys['anthropic']:
        print("  Anthropic: CONNESSO")
    else:
        print("  Anthropic: NON CONFIGURATO")

    if not keys['openai'] and not keys['anthropic']:
        print("\n  ATTENZIONE: Nessuna chiave API configurata!")
        print("  Modifica il file .env e inserisci almeno una chiave API\n")

    port = int(os.environ.get('PORT', 5001))
    print(f"\n  Apri il browser: http://localhost:{port}\n")

    app.run(host='0.0.0.0', port=port, debug=False)
