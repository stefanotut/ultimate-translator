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

# In-memory task store
tasks = {}


class TranslationTask:
    def __init__(self, task_id, input_path, output_path, file_type,
                 source_lang, target_lang, original_filename,
                 provider, model):
        self.task_id = task_id
        self.input_path = input_path
        self.output_path = output_path
        self.file_type = file_type
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.original_filename = original_filename
        self.provider = provider
        self.model = model
        self.status = 'pending'
        self.progress = 0.0
        self.status_text = 'In attesa...'
        self.error = None
        self.logs = []
        self.output_filename = None

    def add_log(self, message, level='info'):
        self.logs.append({
            'message': message,
            'level': level,
            'time': time.time()
        })
        logger.info(f"[{self.task_id[:8]}] {message}")

    def to_dict(self):
        return {
            'task_id': self.task_id,
            'status': self.status,
            'progress': self.progress,
            'status_text': self.status_text,
            'error': self.error,
            'logs': self.logs,
            'output_filename': self.output_filename,
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
    """Upload a file and start translation."""
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

    # Save uploaded file
    task_id = str(uuid.uuid4())
    safe_name = secure_filename(file.filename)
    input_path = os.path.join(UPLOAD_DIR, f"{task_id}_{safe_name}")
    output_path = os.path.join(OUTPUT_DIR, f"{task_id}_translated.{ext}")

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
        original_filename=file.filename,
        provider=provider,
        model=model,
    )
    tasks[task_id] = task

    # Start translation in background
    thread = threading.Thread(target=run_translation, args=(task,), daemon=True)
    thread.start()

    return jsonify({'task_id': task_id, 'status': 'started'})


@app.route('/api/status/<task_id>')
def api_status(task_id):
    """Get translation status."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task non trovato'}), 404
    return jsonify(task.to_dict())


@app.route('/api/download/<task_id>')
def api_download(task_id):
    """Download translated file."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task non trovato'}), 404

    if task.status != 'completed':
        return jsonify({'error': 'Traduzione non ancora completata'}), 400

    if not os.path.exists(task.output_path):
        return jsonify({'error': 'File tradotto non trovato'}), 404

    return send_file(
        task.output_path,
        as_attachment=True,
        download_name=task.output_filename
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
