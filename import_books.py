#!/usr/bin/env python3
"""
ULTIMATE TRANSLATOR - Import a folder of books into your library

Uploads every EPUB and PDF found in a folder on your computer into your
library, through the same API used by the /libreria page (so every book gets
its cover, metadata and automatic tags).

Your library code is in the library page: "Codice libreria" (bottom left).

Examples:
    python3 import_books.py "/Users/me/Book down" \\
        --cartella "Marketing, vendita e copywriting" \\
        --codice ABCD-EFGH-JKMN-PQRS-TVWX

    # the app deployed on Render
    python3 import_books.py ~/Libri --server https://my-app.onrender.com --codice ABCD-...

Only the Python standard library is needed.
"""

import os
import sys
import json
import uuid
import argparse
import mimetypes
import urllib.error
import urllib.request

SUPPORTED = ('.epub', '.pdf')
MAX_SIZE = 100 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, payload):
        super().__init__(payload.get('error') or f'HTTP {status}')
        self.status = status
        self.payload = payload


class Library:
    def __init__(self, server, code):
        self.server = server.rstrip('/')
        self.cookie = f"ut_library={code}"

    def _send(self, method, path, body=None, content_type=None):
        request = urllib.request.Request(self.server + path, data=body, method=method)
        request.add_header('Cookie', self.cookie)
        request.add_header('Accept', 'application/json')
        if content_type:
            request.add_header('Content-Type', content_type)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b'{}')
            except ValueError:
                payload = {}
            raise ApiError(e.code, payload) from None

    def post_json(self, path, data):
        return self._send('POST', path, json.dumps(data).encode('utf-8'), 'application/json')

    def upload(self, file_path, folder_id):
        boundary = uuid.uuid4().hex
        filename = os.path.basename(file_path)
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        with open(file_path, 'rb') as fh:
            content = fh.read()
        parts = []
        if folder_id:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="folder_id"\r\n\r\n{folder_id}\r\n'.encode()
            )
        safe_name = filename.replace('"', "'")
        parts.append(
            (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
             f'Content-Type: {mime}\r\n\r\n').encode('utf-8') + content + b'\r\n'
        )
        parts.append(f'--{boundary}--\r\n'.encode())
        return self._send('POST', '/api/library/books', b''.join(parts), f'multipart/form-data; boundary={boundary}')


def normalize_code(raw):
    code = ''.join(ch for ch in (raw or '') if ch.isalnum()).upper()
    return code.translate(str.maketrans('OIL', '011'))


def collect_books(source):
    """Returns [(absolute path, relative directory)] for supported files, sorted."""
    books = []
    if os.path.isfile(source):
        return [(os.path.abspath(source), '')]
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if name.startswith('.') or not name.lower().endswith(SUPPORTED):
                continue
            rel_dir = os.path.relpath(root, source)
            books.append((os.path.join(root, name), '' if rel_dir == '.' else rel_dir.replace(os.sep, '/')))
    return books


def main():
    parser = argparse.ArgumentParser(description='Carica una cartella di libri EPUB/PDF nella tua libreria.')
    parser.add_argument('sorgente', help='cartella (o singolo file) da caricare')
    parser.add_argument('--cartella', help='cartella di destinazione nella libreria (creata se non esiste). '
                                           'Senza questa opzione viene usato il nome della cartella caricata.')
    parser.add_argument('--codice', default=os.getenv('LIBRARY_CODE'),
                        help='codice della libreria (pagina Libreria > "Codice libreria")')
    parser.add_argument('--server', default=os.getenv('LIBRARY_SERVER', 'http://localhost:5001'),
                        help="indirizzo dell'app (default: http://localhost:5001)")
    parser.add_argument('--piatto', action='store_true',
                        help='metti tutti i libri direttamente nella cartella di destinazione, senza sottocartelle')
    args = parser.parse_args()

    source = os.path.abspath(os.path.expanduser(args.sorgente))
    if not os.path.exists(source):
        sys.exit(f'Percorso non trovato: {source}')
    code = normalize_code(args.codice)
    if len(code) != 20:
        sys.exit('Serve il codice della libreria (--codice): lo trovi nella pagina Libreria, in basso a sinistra.')

    library = Library(args.server, code)
    try:
        opened = library.post_json('/api/library/key', {'code': code})
    except ApiError as e:
        sys.exit(f'Codice non valido per {args.server}: {e}')
    except urllib.error.URLError as e:
        sys.exit(f"Impossibile raggiungere {args.server}: {e.reason}. L'app è avviata?")
    print(f"Libreria {opened['code']} ({opened['books']} libri) su {args.server}")

    books = collect_books(source)
    if not books:
        sys.exit('Nessun file EPUB o PDF trovato.')

    base = (args.cartella or ('' if os.path.isfile(source) else os.path.basename(source.rstrip(os.sep)))).strip('/')

    def destination(rel_dir):
        parts = [p for p in [base, '' if args.piatto else rel_dir] if p]
        return '/'.join(parts)

    paths = sorted({destination(rel) for _, rel in books if destination(rel)})
    folders = {}
    if paths:
        folders = library.post_json('/api/library/folders/tree', {'parent_id': None, 'paths': paths})['folders']

    added = duplicates = failed = 0
    for index, (path, rel_dir) in enumerate(books, 1):
        label = f'[{index}/{len(books)}] {os.path.basename(path)}'
        if os.path.getsize(path) > MAX_SIZE:
            failed += 1
            print(f'{label}  ERRORE: file oltre 100 MB')
            continue
        target = destination(rel_dir)
        try:
            book = library.upload(path, folders.get(target))['book']
            added += 1
            print(f"{label}  ok -> {target or 'Cartelle'}  ({book['title']})")
        except ApiError as e:
            if e.payload.get('code') == 'duplicate':
                duplicates += 1
                print(f'{label}  già nella libreria, saltato')
            else:
                failed += 1
                print(f'{label}  ERRORE: {e}')
        except urllib.error.URLError as e:
            failed += 1
            print(f'{label}  ERRORE di connessione: {e.reason}')

    print(f'\nFatto: {added} caricati, {duplicates} già presenti, {failed} errori.')
    if added:
        print('I tag automatici vengono assegnati in background: aprili nella pagina Libreria.')


if __name__ == '__main__':
    main()
