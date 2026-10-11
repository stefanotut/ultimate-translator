"""scarica_libri_liberi.py against a fake internet (Internet Archive, Wellcome, publishers)
and, at the end, the real import_books.py against the app."""

import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from werkzeug.serving import make_server

from conftest import state
from samples import make_pdf

import scarica_libri_liberi as dl


@pytest.fixture
def internet(tmp_path, monkeypatch):
    """A local server playing archive.org, the Wellcome Collection and two publishers."""
    pdf = open(make_pdf(str(tmp_path / 'scan.pdf'), title='Odorographia', pages=12), 'rb').read()
    report = open(make_pdf(str(tmp_path / 'fao.pdf'), title='Flavours and Fragrances of Plant Origin',
                           pages=12), 'rb').read()    # a different book: the library rejects duplicates
    hits = []
    flaky = {'/editore/lento.pdf': 1}           # answers 503 once, then the file

    archive_items = {
        'odorographia01': {'metadata': {'year': '1892', 'possible-copyright-status': 'NOT_IN_COPYRIGHT'},
                           'files': [{'name': 'odo_bw.pdf', 'size': '900'},
                                     {'name': 'odo_huge.pdf', 'format': 'Text PDF', 'size': str(200 * 2 ** 20)},
                                     {'name': 'odo_text.pdf', 'format': 'Text PDF', 'size': '1000'},
                                     {'name': 'odo.epub'}]},
        'riechstoffe1950': {'metadata': {'year': '1950'}, 'files': [{'name': 'r.pdf'}]},
        'riechstoffe1904': {'metadata': {'year': '1904', 'possible-copyright-status': 'IN_COPYRIGHT'},
                            'files': [{'name': 'r.pdf'}]},
        'riechstoffe1904b': {'metadata': {'year': '1904'}, 'files': [{'name': 'cohn.pdf'}]},
    }
    search_docs = [
        {'identifier': 'riechstoffe1950', 'title': 'Die Riechstoffe', 'year': '1950'},     # still in copyright
        {'identifier': 'kochbuch1900', 'title': 'Kochbuch', 'year': '1900'},              # another book
        {'identifier': 'riechstoffe1904', 'title': 'Die Riechstoffe', 'year': '1904'},   # flagged in copyright
        {'identifier': 'riechstoffe1904b', 'title': 'Die Riechstoffe', 'year': 1904},
    ]
    works = {
        'chiuso': {'id': 'chiuso', 'title': 'Les parfums artificiels', 'items': [{'locations': [
            {'locationType': {'id': 'iiif-presentation'}, 'url': 'http://x/presentation/v2/b111',
             'license': {'id': 'inc'}, 'accessConditions': [{'status': {'id': 'open'}}]}]}]},
        'libero': {'id': 'libero', 'title': 'Les parfums artificiels / par Eugène Charabot',
                   'production': [{'dates': [{'label': '1900'}]}], 'items': [{'locations': [
                       {'locationType': {'id': 'iiif-presentation'}, 'url': 'http://x/presentation/v2/b222',
                        'license': {'id': 'pdm'}, 'accessConditions': [{'status': {'id': 'open'}}]}]}]},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, kind='application/json'):
            body = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(url.query)
            hits.append(url.path)
            if flaky.get(url.path):
                flaky[url.path] -= 1
                return self.send(503, b'busy', 'text/plain')
            if url.path.startswith('/archive/metadata/'):
                return self.send(200, archive_items.get(url.path.rsplit('/', 1)[1], {}))
            if url.path == '/archive/advancedsearch.php':
                assert 'riechstoffe' in query['q'][0]
                return self.send(200, {'response': {'docs': search_docs}})
            if url.path.startswith('/archive/download/'):
                return self.send(200, pdf, 'application/pdf')
            if url.path.startswith('/wellcome/works/'):
                work = works.get(url.path.rsplit('/', 1)[1])
                return self.send(200, work) if work else self.send(404, {'errorType': 'http'})
            if url.path == '/wellcome/works':
                assert query['availabilities'] == ['online']
                return self.send(200, {'results': [works['chiuso'], works['libero']]})
            if url.path in ('/iiif/pdf/b222', '/editore/lento.pdf'):
                return self.send(200, pdf, 'application/pdf')
            if url.path == '/editore/libro.pdf':
                return self.send(200, report, 'application/pdf')
            if url.path == '/editore/pagina.html':
                return self.send(200, b'<html>' + b'x' * 100000, 'text/html')
            return self.send(404, b'not found', 'text/plain')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy', '127.0.0.1,localhost')
    monkeypatch.setattr(dl, 'ARCHIVE', base + '/archive')
    monkeypatch.setattr(dl, 'WELLCOME_API', base + '/wellcome')
    monkeypatch.setattr(dl, 'WELLCOME_IIIF', base + '/iiif')
    monkeypatch.setattr(dl, 'RETRY_PAUSES', (0, 0, 0))
    monkeypatch.setattr(dl, 'PAUSE', 0)
    monkeypatch.setattr(dl, 'MIN_SIZE', 1000)
    yield base, hits
    server.shutdown()


def test_downloads_only_free_copies_with_fallbacks(internet, tmp_path):
    base, hits = internet
    books = [
        {'file': 'Editore', 'fonti': [('url', base + '/editore/libro.pdf')]},
        {'file': 'Fallback', 'fonti': [('url', base + '/editore/pagina.html'), ('archive', 'odorographia01')]},
        {'file': 'Cohn', 'fonti': [('archive_cerca', {'q': 'title:(riechstoffe)', 'parole': ['riechstoffe'],
                                                      'anno_max': 1925})]},
        {'file': 'Charabot', 'fonti': [('wellcome', 'chiuso'),
                                       ('wellcome_cerca', {'q': 'Charabot', 'parole': ['parfums', 'artificiels'],
                                                           'anno_max': 1925})]},
        {'file': 'Lento', 'fonti': [('url', base + '/editore/lento.pdf')]},
        {'file': 'Introvabile', 'fonti': [('url', base + '/editore/manca.pdf'), ('archive', 'nessuno')]},
    ]
    folder = str(tmp_path / 'scaricati')
    got, missing = dl.fetch_all(folder, books)

    assert sorted(os.path.basename(p) for p in got) == ['Charabot.pdf', 'Cohn.pdf', 'Editore.pdf',
                                                        'Fallback.pdf', 'Lento.pdf']
    assert [name for name, _ in missing] == ['Introvabile']
    assert all(open(p, 'rb').read(5) == b'%PDF-' for p in got)
    assert not [f for f in os.listdir(folder) if f.endswith('.part')]
    # the best PDF under the size limit, never the 200 MB one; public domain only
    assert '/archive/download/odorographia01/odo_text.pdf' in hits
    assert '/archive/download/odorographia01/odo_huge.pdf' not in hits
    assert '/archive/download/riechstoffe1904b/cohn.pdf' in hits
    assert not [h for h in hits if '/download/riechstoffe1950' in h or '/download/riechstoffe1904/' in h]
    assert '/iiif/pdf/b222' in hits and '/iiif/pdf/b111' not in hits

    # a second run downloads nothing again
    hits.clear()
    got_again, missing_again = dl.fetch_all(folder, books[:5])
    assert len(got_again) == 5 and not missing_again and hits == []


def test_downloads_then_imports_into_the_library(internet, app, client, tmp_path, monkeypatch):
    base, _ = internet
    live = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    monkeypatch.setattr(dl, 'BOOKS', [
        {'file': 'Sawer - Odorographia, prima serie (1892)', 'fonti': [('archive', 'odorographia01')]},
        {'file': 'Coppen - Flavours and Fragrances of Plant Origin, FAO (1995)',
         'fonti': [('url', base + '/editore/libro.pdf')]},
    ])
    code = client.get('/api/library/key').get_json()['code']
    monkeypatch.setattr('sys.argv', ['scarica_libri_liberi.py', '--dove', str(tmp_path / 'liberi'),
                                     '--codice', code, '--server', f'http://127.0.0.1:{live.server_port}'])
    try:
        assert dl.main() == 0
    finally:
        live.shutdown()

    data = state(client)
    folders = {f['id']: f for f in data['folders']}
    assert sorted(b['title'] for b in data['books']) == ['Flavours and Fragrances of Plant Origin', 'Odorographia']
    for book in data['books']:
        sub = folders[book['folder_id']]
        assert sub['name'] == 'Materie prime'
        assert folders[sub['parent_id']]['name'] == 'Profumeria'
