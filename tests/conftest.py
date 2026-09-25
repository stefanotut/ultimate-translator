import os
import sys
import tempfile

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS_DIR)

# Never call real AI APIs from tests (load_dotenv does not override these)
os.environ['OPENAI_API_KEY'] = ''
os.environ['ANTHROPIC_API_KEY'] = ''
os.environ['MODEL_CHECK'] = '0'  # never ask the providers which models a key can use
os.environ.setdefault('LIBRARY_DATA_DIR', tempfile.mkdtemp(prefix='ut-library-'))

from samples import make_epub, make_pdf  # noqa: E402


@pytest.fixture
def app(tmp_path):
    import library
    import library_db
    from app import app as flask_app, tasks

    library_db.init(str(tmp_path / 'data'))
    library.TAGGING_INLINE = True
    library._last_maintenance.clear()
    tasks.clear()
    flask_app.config['TESTING'] = True
    return flask_app


def browser(app):
    """A browser that opened the library page (which creates its library)."""
    test_client = app.test_client()
    assert test_client.get('/api/library').status_code == 200
    return test_client


@pytest.fixture
def client(app):
    return browser(app)


@pytest.fixture
def other_client(app):
    """A second browser, with its own cookie jar (and therefore its own library)."""
    return browser(app)


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
