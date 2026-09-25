"""import_books.py against a real HTTP server running the app."""

import os
import subprocess
import sys
import threading

import pytest
from werkzeug.serving import make_server

from conftest import ROOT, state
from samples import make_epub, make_pdf


@pytest.fixture
def live_server(app):
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def run_import(*args):
    env = {**os.environ, 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'import_books.py'), *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, env=env,
    )
    return result.returncode, result.stdout + result.stderr


def test_import_a_mac_folder_into_a_library_folder(client, live_server, tmp_path):
    source = tmp_path / 'nuovo progetto' / 'Book down'
    (source / 'Sotto cartella').mkdir(parents=True)
    (source / '__MACOSX').mkdir()
    make_epub(str(source / 'Influence.epub'), title='Influence')
    make_pdf(str(source / 'Copy 101.PDF'), title='Copy 101')
    make_pdf(str(source / 'Sotto cartella' / 'Profumi.pdf'), title='Profumi e vendita')
    (source / '.DS_Store').write_bytes(b'\0')
    (source / '._Influence.epub').write_bytes(b'\0')
    (source / '__MACOSX' / 'hidden.epub').write_bytes(b'junk')
    (source / 'note.txt').write_text('not a book')
    (source / 'broken.epub').write_bytes(b'not an epub')

    code = client.get('/api/library/key').get_json()['code']
    status, output = run_import(str(source), '--cartella', 'Marketing, vendita e copywriting',
                                '--codice', code, '--server', live_server)
    assert status == 1, output  # one broken file
    assert 'Fatto: 3 caricati, 0 gia presenti, 1 errori.' in output
    assert 'broken.epub' in output

    data = state(client)
    folders = {f['id']: f for f in data['folders']}
    books = {b['title']: b for b in data['books']}
    assert set(books) == {'Influence', 'Copy 101', 'Profumi e vendita'}
    top = folders[books['Influence']['folder_id']]
    assert top['name'] == 'Marketing, vendita e copywriting' and top['parent_id'] is None
    assert books['Copy 101']['folder_id'] == top['id']
    sub = folders[books['Profumi e vendita']['folder_id']]
    assert sub['name'] == 'Sotto cartella' and sub['parent_id'] == top['id']

    # Running it again skips what is already there
    status, output = run_import(str(source), '--cartella', 'Marketing, vendita e copywriting',
                                '--codice', code, '--server', live_server)
    assert 'Fatto: 0 caricati, 3 gia presenti, 1 errori.' in output
    assert len(state(client)['books']) == 3


def test_import_explains_wrong_codes_and_addresses(client, live_server, tmp_path):
    make_pdf(str(tmp_path / 'one.pdf'))
    status, output = run_import(str(tmp_path), '--codice', 'ABCD-EFGH-JKMN-PQRS-TVWX', '--server', live_server)
    assert status == 1 and 'Codice non accettato' in output

    code = client.get('/api/library/key').get_json()['code']
    status, output = run_import(str(tmp_path), '--codice', code, '--server', 'http://127.0.0.1:9')
    assert status == 1 and 'Impossibile raggiungere' in output

    status, output = run_import(str(tmp_path / 'missing'), '--codice', code, '--server', live_server)
    assert status == 1 and 'Percorso non trovato' in output
