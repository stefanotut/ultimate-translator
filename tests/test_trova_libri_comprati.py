"""trova_libri_comprati.py on a fake Mac home folder, then the real import into the app."""

import os
import threading

import pytest
from werkzeug.serving import make_server

from conftest import state
from samples import make_epub, make_pdf

import trova_libri_comprati as finder


@pytest.fixture
def mac_home(tmp_path, monkeypatch):
    home = tmp_path / 'Users' / 'stefano'
    downloads, documents, desktop = home / 'Downloads', home / 'Documents', home / 'Desktop'
    icloud = home / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs' / 'Libri'
    project = documents / 'Traduzione file epub e pdf'
    for folder in (downloads / 'libri', downloads / '.cache', icloud, project / 'data', desktop):
        folder.mkdir(parents=True)

    make_pdf(str(downloads / 'Arctander - Perfume and Flavor Materials of Natural Origin (z-library.sk, 1lib.sk).pdf'),
             title='Scansione', author='')
    make_epub(str(downloads / 'libri' / 'the_emperor_of_scent.epub'), title='The Emperor of Scent',
              author='Chandler Burr')
    make_pdf(str(documents / ('Le lacrime di Mirra -- Giuseppe Squillace -- Il Mulino, 2015 -- 9788815253613 -- '
                              '0123456789abcdef0123456789abcdef -- Anna’s Archive.pdf')), title='', author='')
    make_pdf(str(desktop / 'scan0042.pdf'), author='Barbara Herman',
             title='Scent and Subversion: Decoding a Century of Provocative Perfume')
    make_pdf(str(downloads / 'Perfume.pdf'), title='Perfume', author='Megan Volpert')
    make_epub(str(icloud / 'Essenze e alchimia.epub'), title='Essenze e alchimia', author='Mandy Aftel', language='it')
    # not books of the list, or not to be touched
    make_pdf(str(downloads / 'Il profumo.pdf'), title='Il profumo', author='')
    make_pdf(str(downloads / 'Marketing dei profumi.pdf'))
    make_pdf(str(downloads / '.cache' / 'Floating Gold - Christopher Kemp.pdf'))
    make_pdf(str(project / 'data' / 'Arctander - Perfume and Flavor Materials of Natural Origin.pdf'))
    (project / 'app.py').write_text('# the app')
    (project / 'import_books.py').write_text('# the importer')

    monkeypatch.setattr(finder, 'HOME', str(home))
    monkeypatch.setattr(finder, 'STAGING', str(downloads / 'Libri di profumeria trovati'))
    monkeypatch.setattr(finder, 'MIN_SIZE', 1000)
    return home


def found_titles(found):
    return {(finder.CATEGORIES[entry['category']], entry['titles'][0].split(':')[0], os.path.basename(path))
            for _, path, entry in found.values()}


def test_recognizes_the_books_of_the_list(mac_home):
    roots = [os.path.join(str(mac_home), r) for r in finder.ROOTS]
    found = finder.find_books(roots)
    assert found_titles(found) == {
        ('Materie prime', 'Perfume and Flavor Materials of Natural Origin',
         'Arctander - Perfume and Flavor Materials of Natural Origin (z-library.sk, 1lib.sk).pdf'),
        ('Profumieri', 'The Emperor of Scent', 'the_emperor_of_scent.epub'),
        ('Libri italiani', 'Le lacrime di Mirra. Miti e luoghi dei profumi nel mondo antico',
         'Le lacrime di Mirra -- Giuseppe Squillace -- Il Mulino, 2015 -- 9788815253613 -- '
         '0123456789abcdef0123456789abcdef -- Anna’s Archive.pdf'),
        ('Maison, vintage e flaconi', 'Scent and Subversion', 'scan0042.pdf'),
        ("Scienza e cultura dell'olfatto", 'Perfume', 'Perfume.pdf'),
        ('Tecnica e composizione', 'Essence and Alchemy', 'Essenze e alchimia.epub'),
    }


def test_finds_and_imports_into_category_folders(mac_home, app, client, monkeypatch):
    live = make_server('127.0.0.1', 0, app, threaded=True)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    monkeypatch.setenv('NO_PROXY', '127.0.0.1,localhost')
    code = client.get('/api/library/key').get_json()['code']
    monkeypatch.setattr('sys.argv', ['trova_libri_comprati.py', '--codice', code,
                                     '--server', f'http://127.0.0.1:{live.server_port}'])
    try:
        assert finder.main() == 0
        assert finder.main() == 0          # a second run: everything already there, nothing breaks
    finally:
        live.shutdown()

    data = state(client)
    folders = {f['id']: f for f in data['folders']}
    placed = {}
    for book in data['books']:
        sub = folders[book['folder_id']]
        assert folders[sub['parent_id']]['name'] == 'Profumeria'
        placed[sub['name']] = placed.get(sub['name'], 0) + 1
    assert placed == {'Materie prime': 1, 'Profumieri': 1, 'Libri italiani': 1, 'Maison, vintage e flaconi': 1,
                      "Scienza e cultura dell'olfatto": 1, 'Tecnica e composizione': 1}
    staging = finder.STAGING
    assert os.path.exists(os.path.join(staging, 'Libri della lista non trovati sul Mac.txt'))
    # the originals are untouched
    assert os.path.exists(os.path.join(str(mac_home), 'Desktop', 'scan0042.pdf'))
