"""Test della libreria a cartelle DENTRO il portale di produzione.

I test del branch claude/intelligent-clarke-0d4k7s girano qui contro app.py
vero, con il login: ogni "browser" e' un account diverso (e quindi una
libreria diversa). Tutto in cartelle temporanee: database del portale,
libreria, file. Nessuna chiamata vera ai modelli.
"""
import os
import sys
import time
import secrets
import tempfile

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

_TMP = tempfile.mkdtemp(prefix='ut-test-')

# Prima di importare app: load_dotenv non sovrascrive cio' che e' gia' impostato.
os.environ['OPENAI_API_KEY'] = ''
os.environ['ANTHROPIC_API_KEY'] = ''
os.environ['MODEL_CHECK'] = '0'
os.environ['TRANSLATOR_DB'] = os.path.join(_TMP, 'translator.db')
os.environ['LIBRARY_DATA_DIR'] = os.path.join(_TMP, 'libreria')
os.environ['LIBRARY_SYNC_AT_START'] = '0'
os.environ['ALLOWED_EMAILS'] = 'uno@esempio.it,due@esempio.it'
os.environ['AUDIOLIBRO_AUTO'] = '0'

from samples import make_epub, make_pdf  # noqa: E402

UNO = 'uno@esempio.it'
DUE = 'due@esempio.it'

# Test del branch che provano il suo funzionamento "da solo" e che nel portale
# non valgono per scelta: identita' dal login (non un cookie per browser) e
# traduzione lanciata dalla pagina del traduttore, non dalle cartelle.
SOLO_NEL_BRANCH = {
    'test_each_browser_gets_a_private_library_cookie':
        'nel portale la libreria e\' quella dell\'account (login), non del browser',
    'test_access_code_opens_library_on_another_device_and_can_be_rotated':
        'nel portale il codice serve solo a import_books.py (vedi test_ponte.py)',
    'test_changes_never_create_a_library_silently':
        'nel portale senza login si riceve 401 (vedi test_ponte.py)',
    'test_translating_a_library_book_attaches_the_translation':
        'nel portale si traduce dalla pagina del traduttore',
    'test_direct_upload_can_be_saved_to_the_library':
        'nel portale si traduce dalla pagina del traduttore',
    'test_translation_without_library_still_works':
        'nel portale si traduce dalla pagina del traduttore',
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        motivo = SOLO_NEL_BRANCH.get(item.originalname if hasattr(item, 'originalname') else item.name)
        if motivo:
            item.add_marker(pytest.mark.skip(reason=motivo))


def _pulisci_portale():
    import store
    conn = store.db()
    for tabella in ('jobs', 'job_logs', 'sessions', 'reading', 'annotations'):
        try:
            conn.execute(f'DELETE FROM {tabella}')
        except Exception:
            pass
    conn.commit()


@pytest.fixture
def app(tmp_path, monkeypatch):
    import covers
    import library
    import library_db
    from app import app as flask_app

    # le copertine della libreria di sempre finirebbero nella cartella vera del progetto
    monkeypatch.setattr(covers, 'genera', lambda *a, **k: None)
    _pulisci_portale()
    library_db.init(str(tmp_path / 'data'))
    library.TAGGING_INLINE = True
    library._last_maintenance.clear()
    flask_app.config['TESTING'] = True
    return flask_app


def login(app, email):
    """Un browser entrato nel portale come `email`."""
    import auth
    import store
    token = secrets.token_urlsafe(24)
    conn = store.db()
    conn.execute('INSERT INTO sessions (token, email, created_at, expires_at) VALUES (?,?,?,?)',
                 (token, email, time.time(), time.time() + 3600))
    conn.commit()
    client = app.test_client()
    client.set_cookie(auth.COOKIE_NAME, token)
    return client


def browser(app, email=UNO):
    import libreria_ponte
    client = login(app, email)
    assert client.get('/api/library').status_code == 200
    # aprire la pagina fa partire la sincronizzazione in sottofondo: i test
    # partono quando e' finita, altrimenti gareggiano con lei
    with libreria_ponte._sync_in_corso:
        pass
    return client


@pytest.fixture
def client(app):
    return browser(app, UNO)


@pytest.fixture
def other_client(app):
    """Un secondo account, quindi un'altra libreria."""
    return browser(app, DUE)


@pytest.fixture
def pdf_file(tmp_path):
    return make_pdf(str(tmp_path / 'profumi.pdf'))


@pytest.fixture
def epub_file(tmp_path):
    return make_epub(str(tmp_path / 'sales.epub'))


def upload(client, path, name=None, folder_id=None, **fields):
    with open(path, 'rb') as fh:
        data = {'file': (fh, name or os.path.basename(path)), **fields}
        if folder_id:
            data['folder_id'] = folder_id
        return client.post('/api/library/books', data=data, content_type='multipart/form-data')


def state(client):
    response = client.get('/api/library')
    assert response.status_code == 200
    return response.get_json()
