import os
import json
import time
import shutil

import pytest

import library_db
import tagger
from conftest import state, upload
from samples import make_pdf, make_epub, LOREM_EN


def create_folder(client, name, parent_id=None, **extra):
    response = client.post('/api/library/folders', json={'name': name, 'parent_id': parent_id, **extra})
    assert response.status_code == 201, response.get_json()
    return response.get_json()['folder']


def tag_names(data, book):
    names = {t['id']: t['name'] for t in data['tags']}
    return [names[t['id']] for t in book['tags']]


# ---------------------------------------------------------------------------
# Private libraries
# ---------------------------------------------------------------------------

def test_each_browser_gets_a_private_library_cookie(client):
    response = client.get('/api/library')
    cookie = response.headers.get('Set-Cookie', '')
    assert response.status_code == 200
    assert cookie.startswith('ut_library=')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    # The same browser keeps its library: no new cookie
    assert 'Set-Cookie' not in client.get('/api/library').headers


def test_libraries_are_isolated(client, other_client, pdf_file):
    book = upload(client, pdf_file).get_json()['book']
    assert state(other_client)['books'] == []
    assert other_client.get(f"/api/library/books/{book['id']}").status_code == 404
    assert other_client.get(f"/api/library/books/{book['id']}/download").status_code == 404
    assert other_client.get(book['cover_url']).status_code == 404
    assert other_client.post('/api/library/trash', json={'book_ids': [book['id']]}).get_json()['books'] == 0
    assert other_client.patch(f"/api/library/books/{book['id']}", json={'title': 'x'}).status_code == 404
    assert len(state(client)['books']) == 1


def test_access_code_opens_library_on_another_device_and_can_be_rotated(client, other_client, pdf_file):
    upload(client, pdf_file)
    code = client.get('/api/library/key').get_json()['code']
    assert len(code) == 24 and code.count('-') == 4

    # Codes are forgiving: lowercase, no dashes, O instead of 0 ...
    typed = code.replace('-', '').lower().replace('0', 'o')
    response = other_client.post('/api/library/key', json={'code': typed})
    assert response.status_code == 200 and response.get_json()['books'] == 1
    assert len(state(other_client)['books']) == 1

    assert other_client.post('/api/library/key', json={'code': 'nope'}).status_code == 400
    assert other_client.post('/api/library/key', json={'code': 'A' * 20}).status_code == 404

    # Rotating the code disconnects the other device
    new_code = client.post('/api/library/key/rotate').get_json()['code']
    assert new_code != code
    assert len(state(client)['books']) == 1
    assert state(other_client)['books'] == []


def test_cross_site_writes_are_rejected(client):
    response = client.post('/api/library/folders', json={'name': 'X'},
                           headers={'Origin': 'https://evil.example'})
    assert response.status_code == 403
    same_site = client.post('/api/library/folders', json={'name': 'X'},
                            headers={'Origin': 'http://localhost'})
    assert same_site.status_code == 201


# ---------------------------------------------------------------------------
# Uploads, metadata, automatic tags
# ---------------------------------------------------------------------------

def test_pdf_upload_reads_metadata_cover_language_and_tags(client, pdf_file):
    response = upload(client, pdf_file, name='Marketing_Profumi.pdf')
    assert response.status_code == 201
    book = response.get_json()['book']
    assert book['title'] == 'Marketing dei profumi'
    assert book['author'] == 'Giulia Rossi'
    assert book['file_type'] == 'pdf' and book['num_pages'] == 3
    assert book['total_words'] > 100 and book['estimated_tokens'] > 100
    assert book['language'] == 'Italian'
    assert book['cover_url'] and client.get(book['cover_url']).mimetype == 'image/jpeg'

    data = state(client)
    stored = data['books'][0]
    assert stored['tag_status'] == 'done' and stored['tag_method'] == 'keywords'
    assert {'profumi', 'marketing'} <= set(tag_names(data, stored))
    assert data['ai'] == {'enabled': False, 'provider': None, 'label': None}


def test_epub_upload_reads_metadata(client, epub_file):
    book = upload(client, epub_file).get_json()['book']
    assert book['title'] == 'The Sales Playbook'
    assert book['author'] == 'John Smith'
    assert book['file_type'] == 'epub' and book['num_chapters'] >= 3
    assert book['language'] == 'English'
    assert book['has_cover']
    assert 'vendita' in tag_names(state(client), state(client)['books'][0])


def test_title_falls_back_to_file_name(client, tmp_path):
    path = make_pdf(str(tmp_path / 'x.pdf'), title='Microsoft Word - Documento1.docx', author='Administrator')
    book = upload(client, path, name='Guida_vendita_2024.pdf').get_json()['book']
    assert book['title'] == 'Guida vendita 2024'
    assert book['author'] is None


def test_duplicates_are_detected(client, pdf_file):
    first = upload(client, pdf_file).get_json()['book']
    response = upload(client, pdf_file, name='copia.pdf')
    assert response.status_code == 409
    assert response.get_json()['code'] == 'duplicate'
    assert response.get_json()['existing']['id'] == first['id']
    assert upload(client, pdf_file, name='copia.pdf', allow_duplicate='1').status_code == 201
    assert len(state(client)['books']) == 2


def test_bad_files_are_rejected(client, tmp_path):
    text = tmp_path / 'notes.txt'
    text.write_text('hello')
    assert upload(client, str(text)).get_json()['code'] == 'unsupported'

    broken = tmp_path / 'broken.pdf'
    broken.write_bytes(b'this is not a pdf at all')
    response = upload(client, str(broken))
    assert response.status_code == 400 and response.get_json()['code'] == 'unreadable'

    empty = tmp_path / 'empty.epub'
    empty.write_bytes(b'')
    assert upload(client, str(empty)).get_json()['code'] == 'empty'
    assert state(client)['books'] == []
    assert os.listdir(os.path.join(library_db.data_dir(), 'tmp')) == []


def test_upload_into_folder_and_new_books_go_on_top(client, pdf_file, epub_file):
    folder = create_folder(client, 'Marketing')
    first = upload(client, pdf_file, folder_id=folder['id']).get_json()['book']
    second = upload(client, epub_file, folder_id=folder['id']).get_json()['book']
    assert first['folder_id'] == folder['id'] == second['folder_id']
    assert second['position'] < first['position']


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def test_nested_folders_rename_color_and_cycles(client):
    parent = create_folder(client, 'Marketing')
    child = create_folder(client, 'Social', parent_id=parent['id'])
    assert child['parent_id'] == parent['id']

    renamed = client.patch(f"/api/library/folders/{parent['id']}", json={'name': '  Marketing   digitale '})
    assert renamed.get_json()['folder']['name'] == 'Marketing digitale'
    colored = client.patch(f"/api/library/folders/{parent['id']}", json={'color': 'red'})
    assert colored.get_json()['folder']['color'] == 'red'
    assert client.patch(f"/api/library/folders/{parent['id']}", json={'color': 'fuchsia'}).status_code == 400
    assert client.patch(f"/api/library/folders/{parent['id']}", json={'name': '   '}).status_code == 400

    cycle = client.post('/api/library/move', json={'folder_ids': [parent['id']], 'target_id': child['id']})
    assert cycle.status_code == 400 and cycle.get_json()['code'] == 'cycle'
    into_itself = client.post('/api/library/move', json={'folder_ids': [parent['id']], 'target_id': parent['id']})
    assert into_itself.status_code == 400

    assert client.post('/api/library/move', json={'folder_ids': [child['id']], 'target_id': None}).status_code == 200
    assert {f['id']: f['parent_id'] for f in state(client)['folders']}[child['id']] is None


def test_folder_upload_creates_tree_and_merges_on_reupload(client):
    base = create_folder(client, 'Libri')
    paths = ['Marketing', 'Marketing/Social Media', 'Profumi']
    first = client.post('/api/library/folders/tree', json={'parent_id': base['id'], 'paths': paths}).get_json()
    assert set(first['folders']) == set(paths) and len(first['created']) == 3

    folders = {f['id']: f for f in state(client)['folders']}
    social = folders[first['folders']['Marketing/Social Media']]
    assert social['parent_id'] == first['folders']['Marketing']
    assert folders[first['folders']['Marketing']]['parent_id'] == base['id']

    # New sibling folders keep the natural alphabetical order of the uploaded folder
    tree = client.post('/api/library/folders/tree', json={'paths': ['Corso/Lezione 10', 'Corso/Lezione 2', 'Corso/Appendice']}).get_json()
    siblings = sorted((f for f in state(client)['folders'] if f['parent_id'] == tree['folders']['Corso']), key=lambda f: f['position'])
    assert [f['name'] for f in siblings] == ['Appendice', 'Lezione 2', 'Lezione 10']

    # Uploading the same folder again reuses it (names match case-insensitively)
    again = client.post('/api/library/folders/tree', json={'parent_id': base['id'], 'paths': ['marketing/social media']}).get_json()
    assert again['created'] == []
    assert again['folders']['marketing/social media'] == first['folders']['Marketing/Social Media']


def test_move_and_reorder(client, tmp_path):
    ids = []
    for i in range(3):
        path = make_pdf(str(tmp_path / f'b{i}.pdf'), title=f'Libro {i}', text=f'testo {i} ' * 50)
        ids.append(upload(client, path).get_json()['book']['id'])
    folder = create_folder(client, 'Vendita')

    moved = client.post('/api/library/move', json={'book_ids': ids[:2], 'target_id': folder['id']}).get_json()
    assert moved['books'] == 2
    books = {b['id']: b for b in state(client)['books']}
    assert books[ids[0]]['folder_id'] == folder['id'] and books[ids[2]]['folder_id'] is None

    wanted = [ids[1], ids[0]]
    assert client.post('/api/library/reorder', json={'kind': 'book', 'parent_id': folder['id'], 'ids': wanted}).status_code == 200
    in_folder = sorted((b for b in state(client)['books'] if b['folder_id'] == folder['id']), key=lambda b: b['position'])
    assert [b['id'] for b in in_folder] == wanted

    # Ids from another container are ignored, not moved
    client.post('/api/library/reorder', json={'kind': 'book', 'parent_id': folder['id'], 'ids': [ids[2]]})
    assert {b['id']: b for b in state(client)['books']}[ids[2]]['folder_id'] is None
    assert client.post('/api/library/reorder', json={'kind': 'nope', 'ids': []}).status_code == 400


def test_rename_color_and_favorite_books(client, pdf_file, epub_file):
    a = upload(client, pdf_file).get_json()['book']
    b = upload(client, epub_file).get_json()['book']
    renamed = client.patch(f"/api/library/books/{a['id']}", json={'title': 'Profumi & Marketing', 'author': ''})
    assert renamed.get_json()['book']['title'] == 'Profumi & Marketing'
    assert renamed.get_json()['book']['author'] is None
    assert client.patch(f"/api/library/books/{a['id']}", json={'title': ''}).status_code == 400

    bulk = client.post('/api/library/books/update', json={'book_ids': [a['id'], b['id']], 'color': 'teal', 'favorite': True})
    assert bulk.get_json()['updated'] == 2
    for book in state(client)['books']:
        assert book['color'] == 'teal' and book['favorite'] is True


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def test_manual_tags_are_case_and_accent_insensitive(client, pdf_file, epub_file):
    a = upload(client, pdf_file).get_json()['book']
    b = upload(client, epub_file).get_json()['book']
    client.post('/api/library/books/tags', json={'book_ids': [a['id']], 'add': ['Città']})
    client.post('/api/library/books/tags', json={'book_ids': [b['id']], 'add': ['citta', '#VENDITA']})
    data = state(client)
    citta = [t for t in data['tags'] if t['name'] == 'Città']
    assert len(citta) == 1 and citta[0]['count'] == 2
    assert [t['name'] for t in data['tags']].count('vendita') == 1

    # Adding a tag the AI suggested marks it as confirmed by the user
    book_b = {x['id']: x for x in data['books']}[b['id']]
    vendita = next(t for t in data['tags'] if t['name'] == 'vendita')
    assert {'id': vendita['id'], 'source': 'manual'} in book_b['tags']


def test_rename_merge_recolor_and_delete_tags(client, pdf_file, epub_file):
    a = upload(client, pdf_file).get_json()['book']
    b = upload(client, epub_file).get_json()['book']
    client.post('/api/library/books/tags', json={'book_ids': [a['id']], 'add': ['sales']})
    client.post('/api/library/books/tags', json={'book_ids': [b['id']], 'add': ['vendite']})
    tags = {t['name']: t for t in state(client)['tags']}

    merged = client.patch(f"/api/library/tags/{tags['sales']['id']}", json={'name': 'Vendite'}).get_json()['tag']
    assert merged['merged'] and merged['id'] == tags['vendite']['id']
    tags = {t['name']: t for t in state(client)['tags']}
    assert 'sales' not in tags and tags['vendite']['count'] == 2

    recolored = client.patch(f"/api/library/tags/{tags['vendite']['id']}", json={'color': 'pink'}).get_json()['tag']
    assert recolored['color'] == 'pink'

    assert client.delete(f"/api/library/tags/{tags['vendite']['id']}").status_code == 200
    assert 'vendite' not in {t['name'] for t in state(client)['tags']}


def test_removing_last_use_deletes_the_tag(client, pdf_file):
    book = upload(client, pdf_file).get_json()['book']
    client.post('/api/library/books/tags', json={'book_ids': [book['id']], 'add': ['unico']})
    tag = next(t for t in state(client)['tags'] if t['name'] == 'unico')
    client.post('/api/library/books/tags', json={'book_ids': [book['id']], 'remove': [tag['id']]})
    assert 'unico' not in {t['name'] for t in state(client)['tags']}


def test_retag_keeps_manual_tags(client, pdf_file):
    book = upload(client, pdf_file).get_json()['book']
    client.post('/api/library/books/tags', json={'book_ids': [book['id']], 'add': ['da leggere']})
    assert client.post(f"/api/library/books/{book['id']}/retag").status_code == 200
    data = state(client)
    names = tag_names(data, data['books'][0])
    assert 'da leggere' in names and 'profumi' in names


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------

def test_trash_undo_restore_and_purge(client, pdf_file, epub_file):
    folder = create_folder(client, 'Profumi')
    sub = create_folder(client, 'Nicchia', parent_id=folder['id'])
    inside = upload(client, pdf_file, folder_id=sub['id']).get_json()['book']
    loose = upload(client, epub_file).get_json()['book']

    trashed = client.post('/api/library/trash', json={'folder_ids': [folder['id']]}).get_json()
    assert trashed['folders'] == 2 and trashed['books'] == 1
    data = state(client)
    assert data['folders'] == [] and [b['id'] for b in data['books']] == [loose['id']]
    assert data['trash_count'] == 1
    trash = client.get('/api/library/trash').get_json()
    assert [(i['kind'], i['name'], i['book_count']) for i in trash['items']] == [('folder', 'Profumi', 1)]
    assert trash['retention_days'] == 30

    # Undo
    client.post('/api/library/trash/restore', json={'trash_id': trashed['trash_id']})
    data = state(client)
    assert len(data['folders']) == 2 and len(data['books']) == 2 and data['trash_count'] == 0

    # Restore from the trash view, by item
    client.post('/api/library/trash', json={'folder_ids': [folder['id']]})
    client.post('/api/library/trash/restore', json={'folder_ids': [folder['id']]})
    assert {b['id']: b['folder_id'] for b in state(client)['books']}[inside['id']] == sub['id']

    # Purge deletes the files too
    library_id = library_db.get_library_by_code(client.get('/api/library/key').get_json()['code'])['id']
    record = library_db.get_book_record(library_id, loose['id'])
    book_file = library_db.abs_path(record['file_path'])
    assert os.path.exists(book_file)
    client.post('/api/library/trash', json={'book_ids': [loose['id']]})
    assert client.post('/api/library/trash/purge', json={'book_ids': [loose['id']]}).get_json()['books'] == 1
    assert not os.path.exists(book_file)
    assert client.get(f"/api/library/books/{loose['id']}/download").status_code == 404

    client.post('/api/library/trash', json={'folder_ids': [folder['id']]})
    emptied = client.post('/api/library/trash/empty').get_json()
    assert emptied == {'books': 1, 'folders': 2}
    assert state(client)['trash_count'] == 0


def test_active_items_cannot_be_purged(client, pdf_file):
    book = upload(client, pdf_file).get_json()['book']
    assert client.post('/api/library/trash/purge', json={'book_ids': [book['id']]}).get_json()['books'] == 0
    assert len(state(client)['books']) == 1


def test_restoring_a_book_from_a_deleted_folder_puts_it_in_the_root(client, pdf_file):
    folder = create_folder(client, 'Temporanea')
    book = upload(client, pdf_file, folder_id=folder['id']).get_json()['book']
    client.post('/api/library/trash', json={'book_ids': [book['id']]})
    client.post('/api/library/trash', json={'folder_ids': [folder['id']]})
    client.post('/api/library/trash/restore', json={'book_ids': [book['id']]})
    assert state(client)['books'][0]['folder_id'] is None


def test_cannot_upload_into_a_trashed_folder(client, pdf_file):
    folder = create_folder(client, 'Vecchia')
    client.post('/api/library/trash', json={'folder_ids': [folder['id']]})
    assert upload(client, pdf_file, folder_id=folder['id']).status_code == 404


# ---------------------------------------------------------------------------
# Translation integration
# ---------------------------------------------------------------------------

def _fake_translation(monkeypatch):
    import epub_handler
    import pdf_handler

    def fake(input_path, output_path, progress_callback=None, **_kwargs):
        progress_callback(0.5, 'Capitolo 1/2 - Blocco 1/1')
        shutil.copy(input_path, output_path)

    monkeypatch.setattr(epub_handler, 'translate_epub', fake)
    monkeypatch.setattr(pdf_handler, 'translate_pdf', fake)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    monkeypatch.setenv('LIBRARY_AI_TAGS', '0')  # the fake key must not reach the tagger


def _wait_for(client, task_id):
    # Wait on the shared job store: it is written right after the in-memory status changes
    for _ in range(250):
        record = library_db.get_translation(task_id)
        if record and record['status'] in ('completed', 'error'):
            return client.get(f'/api/status/{task_id}').get_json()
        time.sleep(0.02)
    raise AssertionError('translation did not finish')


def test_translating_a_library_book_attaches_the_translation(client, other_client, epub_file, monkeypatch):
    import app as app_module

    book = upload(client, epub_file).get_json()['book']
    _fake_translation(monkeypatch)
    response = client.post('/api/translate', data={
        'book_id': book['id'], 'source_lang': 'English', 'target_lang': 'Italian',
        'provider': 'anthropic', 'model': 'claude-sonnet-4-20250514',
    })
    assert response.status_code == 200, response.get_json()
    task_id = response.get_json()['task_id']
    assert response.get_json()['book_id'] == book['id']
    assert _wait_for(client, task_id)['status'] == 'completed'

    # Another gunicorn worker only has the shared job store
    app_module.tasks.clear()
    status = client.get(f'/api/status/{task_id}').get_json()
    assert status['status'] == 'completed' and status['logs_total'] >= 4
    assert status['output_filename'] == 'The Sales Playbook_tradotto_Italian.epub'
    assert client.get(f'/api/download/{task_id}').status_code == 200

    translations = state(client)['books'][0]['translations']
    assert [(t['id'], t['target_lang'], t['status']) for t in translations] == [(task_id, 'Italian', 'completed')]
    download = client.get(f'/api/library/translations/{task_id}/download')
    assert download.status_code == 200
    assert other_client.get(f'/api/library/translations/{task_id}/download').status_code == 404

    # Books of other libraries cannot be translated
    assert other_client.post('/api/translate', data={
        'book_id': book['id'], 'source_lang': 'English', 'target_lang': 'Italian',
        'provider': 'anthropic', 'model': 'claude-sonnet-4-20250514',
    }).status_code == 404

    assert client.delete(f'/api/library/translations/{task_id}').status_code == 200
    assert state(client)['books'][0]['translations'] == []


def test_direct_upload_can_be_saved_to_the_library(client, pdf_file, monkeypatch):
    _fake_translation(monkeypatch)
    form = {'source_lang': 'Italian', 'target_lang': 'English', 'provider': 'anthropic',
            'model': 'claude-sonnet-4-20250514', 'save_to_library': '1'}
    with open(pdf_file, 'rb') as fh:
        first = client.post('/api/translate', data={**form, 'file': (fh, 'profumi.pdf')}, content_type='multipart/form-data')
    assert first.status_code == 200 and first.get_json()['book_id']
    _wait_for(client, first.get_json()['task_id'])

    # Same file again: the existing library book is reused
    with open(pdf_file, 'rb') as fh:
        second = client.post('/api/translate', data={**form, 'file': (fh, 'profumi.pdf')}, content_type='multipart/form-data')
    _wait_for(client, second.get_json()['task_id'])
    assert second.get_json()['book_id'] == first.get_json()['book_id']
    books = state(client)['books']
    assert len(books) == 1 and len(books[0]['translations']) == 2


def test_translation_without_library_still_works(client, pdf_file, monkeypatch, tmp_path):
    import app as app_module

    monkeypatch.setattr(app_module, 'UPLOAD_DIR', str(tmp_path))
    monkeypatch.setattr(app_module, 'OUTPUT_DIR', str(tmp_path))
    _fake_translation(monkeypatch)
    with open(pdf_file, 'rb') as fh:
        response = client.post('/api/translate', data={
            'file': (fh, 'profumi.pdf'), 'source_lang': 'Italian', 'target_lang': 'English',
            'provider': 'anthropic', 'model': 'claude-sonnet-4-20250514',
        }, content_type='multipart/form-data')
    assert response.status_code == 200 and response.get_json()['book_id'] is None
    assert _wait_for(client, response.get_json()['task_id'])['status'] == 'completed'
    assert state(client)['books'] == []


# ---------------------------------------------------------------------------
# Tagger
# ---------------------------------------------------------------------------

def test_tag_normalization_snaps_to_existing_tags():
    tags = tagger.normalize_tags(['Profumo', 'vendite', '#SEO', 'seo', 'x', 'Crescita  personale'],
                                 ['profumi', 'vendita'])
    assert tags == ['profumi', 'vendita', 'SEO', 'Crescita personale']


@pytest.mark.parametrize('value,expected', [
    ('it', 'Italian'), ('en-US', 'English'), ('Inglese', 'English'), ('Français', 'French'),
    ('Portuguese (Brazil)', 'Portuguese'), ('unknown', None), (None, None),
])
def test_language_names_are_normalized(value, expected):
    assert tagger.normalize_language(value) == expected


def test_offline_language_detection():
    assert tagger.detect_language_offline('Il libro che hai scritto è molto bello e non vedo l\'ora di leggerlo con gli amici per la prima volta. ' * 3) == 'Italian'
    assert tagger.detect_language_offline(LOREM_EN * 2) == 'English'
    assert tagger.detect_language_offline('Привет, как дела? Это книга о маркетинге и продажах для всех.') == 'Russian'
    assert tagger.detect_language_offline('short') is None


class _FakeBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class _FakeAnthropic:
    def __init__(self, payload, stop_reason='end_turn'):
        self.calls = []
        self.payload = payload
        self.stop_reason = stop_reason
        self.messages = self

    def with_options(self, **_kwargs):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = type('Response', (), {})()
        response.content = [_FakeBlock(json.dumps(self.payload))]
        response.stop_reason = self.stop_reason
        return response


def test_ai_tagging_uses_claude_structured_output(client, epub_file, monkeypatch):
    import translator

    fake = _FakeAnthropic({'tags': ['Vendite', 'negoziazione', 'business'], 'language': 'English',
                           'summary': 'Un manuale pratico per chiudere più trattative.'})
    monkeypatch.setattr(translator, 'get_anthropic_client', lambda: fake)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')

    folder = create_folder(client, 'Vendita')
    upload(client, epub_file, folder_id=folder['id'])
    data = state(client)
    book = data['books'][0]
    assert data['ai']['provider'] == 'anthropic'
    assert book['tag_method'] == 'ai'
    assert tag_names(data, book) == ['Vendite', 'negoziazione', 'business']
    assert book['summary'] == 'Un manuale pratico per chiudere più trattative.'

    call = fake.calls[0]
    assert call['model'] == 'claude-haiku-4-5'
    assert call['output_config']['format']['type'] == 'json_schema'
    assert "Folder in the user's library: Vendita" in call['messages'][0]['content']
    assert '<book_excerpt>' in call['messages'][0]['content']

    # The next book sees the existing vocabulary, so variants snap onto it
    fake.payload = {'tags': ['vendita', 'Business'], 'language': 'English', 'summary': 'Altro libro.'}
    upload(client, make_epub(epub_file.replace('.epub', '2.epub'), title='More Sales', text=LOREM_EN + ' extra'))
    vocabulary_line = fake.calls[1]['messages'][0]['content'].splitlines()[-1]
    assert vocabulary_line.startswith('Existing tags in this library:')
    assert all(name in vocabulary_line for name in ('Vendite', 'negoziazione', 'business'))
    data = state(client)
    second = next(b for b in data['books'] if b['title'] == 'More Sales')
    assert tag_names(data, second) == ['Vendite', 'business']


def test_ai_failure_falls_back_to_keywords(client, pdf_file, monkeypatch):
    import translator

    monkeypatch.setattr(translator, 'get_anthropic_client', lambda: _FakeAnthropic({}, stop_reason='refusal'))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    upload(client, pdf_file)
    data = state(client)
    assert data['books'][0]['tag_method'] == 'keywords'
    assert 'profumi' in tag_names(data, data['books'][0])


def test_ai_tagging_can_be_disabled(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test-key')
    monkeypatch.setenv('LIBRARY_AI_TAGS', '0')
    assert tagger.ai_status()['enabled'] is False
