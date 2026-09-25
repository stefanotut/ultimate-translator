#!/usr/bin/env python3
"""
ULTIMATE TRANSLATOR - Import a folder of books into your library

Uploads every EPUB and PDF found in a folder on your computer into your
library, through the same API used by the /libreria page (so every book gets
its cover, metadata and automatic tags). Subfolders become folders of the
library. Books already in the library are skipped, so the import can be run
again at any time (for example after a network problem) without duplicates.

Your library code is in the folders page (/libreria/cartelle): "Codice libreria" (bottom left).

Examples:
    python3 import_books.py            # asks for everything it needs

    python3 import_books.py "/Users/me/Book down" \\
        --cartella "Marketing, vendita e copywriting" \\
        --codice ABCD-EFGH-JKMN-PQRS-TVWX

    # the app deployed on Render
    python3 import_books.py ~/Libri --server https://my-app.onrender.com --codice ABCD-...

Only the Python standard library is needed.
"""

import os
import re
import sys
import json
import time
import uuid
import argparse
import http.client
import mimetypes
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

SUPPORTED = ('.epub', '.pdf')
MAX_SIZE = 100 * 1024 * 1024
DEFAULT_SERVER = 'https://89-168-31-56.nip.io'
ATTEMPTS = 4                   # per request, for network errors and busy servers
RETRY_PAUSES = (3, 10, 30)
CHUNK = 1024 * 1024
SKIPPED_DIRS = {'__MACOSX', '$RECYCLE.BIN', 'System Volume Information'}


class ApiError(Exception):
    def __init__(self, status, payload):
        super().__init__(payload.get('error') or f'HTTP {status}')
        self.status = status
        self.payload = payload


class Unreachable(Exception):
    """The server did not answer (not started, wrong address, no network)."""


def nfc(text):
    return unicodedata.normalize('NFC', text)


class MultipartBody:
    """A multipart/form-data body that streams the file instead of loading it in memory."""

    def __init__(self, fields, file_field, file_path):
        self.boundary = uuid.uuid4().hex
        self.file_path = file_path
        head = b''
        for name, value in fields.items():
            head += (f'--{self.boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f'{value}\r\n').encode('utf-8', 'replace')
        filename = nfc(os.path.basename(file_path)).replace('"', "'").replace('\r', ' ').replace('\n', ' ')
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        # 'replace': a name that is not valid UTF-8 (possible on Linux) must not stop the import
        head += (f'--{self.boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode('utf-8', 'replace')
        self.head = head
        self.tail = f'\r\n--{self.boundary}--\r\n'.encode('utf-8')
        self.length = len(head) + os.path.getsize(file_path) + len(self.tail)

    @property
    def content_type(self):
        return f'multipart/form-data; boundary={self.boundary}'

    def __iter__(self):
        yield self.head
        with open(self.file_path, 'rb') as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        yield self.tail


class Library:
    def __init__(self, server, code):
        self.server = server.rstrip('/')
        self.cookie = f"ut_library={code}"

    def _send_once(self, method, path, body=None, content_type=None, length=None):
        try:
            request = urllib.request.Request(self.server + path, data=body, method=method)
        except ValueError:
            raise Unreachable(f'indirizzo non valido: {self.server}') from None
        request.add_header('Cookie', self.cookie)
        request.add_header('Accept', 'application/json')
        if content_type:
            request.add_header('Content-Type', content_type)
        if length is not None:
            request.add_header('Content-Length', str(length))
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raw = e.read() or b''
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if not payload.get('error'):
                # Not an answer of this app (e.g. an HTML error page of a proxy or another site)
                payload = {'error': f'il server ha risposto {e.code} {e.reason}', 'foreign': True}
            raise ApiError(e.code, payload) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise Unreachable(getattr(e, 'reason', None) or e) from None
        try:
            return json.loads(raw or b'{}')
        except ValueError:
            raise ApiError(200, {'error': "la risposta non viene da ULTIMATE TRANSLATOR: l'indirizzo e giusto?",
                                 'foreign': True}) from None

    def _send(self, method, path, body_factory=None, content_type=None, length=None, attempts=ATTEMPTS):
        """Retries network errors and busy servers (429 / 5xx) with pauses."""
        for attempt in range(attempts):
            try:
                body = body_factory() if body_factory else None
                return self._send_once(method, path, body, content_type, length)
            except (Unreachable, ApiError) as e:
                retryable = isinstance(e, Unreachable) or e.status in (429, 500, 502, 503, 504)
                if not retryable or attempt == attempts - 1:
                    raise
                pause = RETRY_PAUSES[min(attempt, len(RETRY_PAUSES) - 1)]
                print(f'    (problema temporaneo: {e}; nuovo tentativo tra {pause} s)')
                time.sleep(pause)

    def post_json(self, path, data, attempts=ATTEMPTS):
        payload = json.dumps(data).encode('utf-8')
        return self._send('POST', path, lambda: payload, 'application/json', attempts=attempts)

    def upload(self, file_path, folder_id):
        fields = {'folder_id': folder_id} if folder_id else {}
        body = MultipartBody(fields, 'file', file_path)
        return self._send('POST', '/api/library/books', lambda: iter(body), body.content_type, body.length)


def normalize_server(raw):
    server = (raw or '').strip().strip('"\'').rstrip('/')
    if server and not re.match(r'^https?://', server, re.I):
        server = 'http://' + server
    return server


def valid_server(server):
    parts = urllib.parse.urlsplit(server)
    return parts.scheme in ('http', 'https') and bool(parts.netloc) and not re.search(r'[\s<>"{}|\\^`]', server)


def normalize_code(raw):
    code = ''.join(ch for ch in (raw or '') if ch.isalnum()).upper()
    return code.translate(str.maketrans('OIL', '011'))


def clean_path(raw):
    """A path typed or dragged into the terminal ('...\\ with\\ spaces', quotes, trailing space)."""
    text = (raw or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1]
    elif '\\' in text and os.sep == '/':
        text = re.sub(r'\\(.)', r'\1', text)
    return os.path.abspath(os.path.expanduser(text))


def ask(question, default=None):
    suffix = f' [{default}]' if default else ''
    try:
        answer = input(f'{question}{suffix}: ').strip()
    except EOFError:
        answer = ''
    return answer or (default or '')


def collect_books(source):
    """Returns [(absolute path, relative directory)] for supported files, sorted."""
    books = []
    if os.path.isfile(source):
        return [(os.path.abspath(source), '')]
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIPPED_DIRS)
        for name in sorted(files):
            if name.startswith('.') or not name.lower().endswith(SUPPORTED):
                continue
            rel_dir = os.path.relpath(root, source)
            books.append((os.path.join(root, name), '' if rel_dir == '.' else nfc(rel_dir.replace(os.sep, '/'))))
    return books


def connect(server, code, interactive):
    """(Library, info) once the server answers and the code opens a library; asks again when interactive."""
    while True:
        library = Library(server, code)
        try:
            if not valid_server(server):
                raise Unreachable('indirizzo non valido')
            # One quick try: when the address is wrong the user can fix it right away
            return library, library.post_json('/api/library/key', {'code': code}, attempts=1), server
        except Unreachable as e:
            print(f"\nImpossibile raggiungere {server} ({e}).")
            print("L'app e avviata? Se usi la versione online, scrivi l'indirizzo che vedi nel browser "
                  "(per esempio https://nome-app.onrender.com).")
            if not interactive:
                sys.exit(1)
            server = normalize_server(ask("Indirizzo dell'app", server))
        except ApiError as e:
            if e.payload.get('foreign'):
                print(f"\n{server} non sembra ULTIMATE TRANSLATOR ({e}): controlla l'indirizzo.")
                if not interactive:
                    sys.exit(1)
                server = normalize_server(ask("Indirizzo dell'app", server))
                continue
            print(f'\nCodice non accettato da {server}: {e}')
            if not interactive:
                sys.exit(1)
            code = normalize_code(ask('Codice libreria (pagina Cartelle > "Codice libreria", in basso a sinistra)'))


def main():
    parser = argparse.ArgumentParser(description='Carica una cartella di libri EPUB/PDF nella tua libreria.')
    parser.add_argument('sorgente', nargs='?', help='cartella (o singolo file) da caricare')
    parser.add_argument('--cartella', help='cartella di destinazione nella libreria (creata se non esiste). '
                                           'Senza questa opzione viene usato il nome della cartella caricata.')
    parser.add_argument('--codice', default=os.getenv('LIBRARY_CODE'),
                        help='codice della libreria (pagina Cartelle > "Codice libreria")')
    parser.add_argument('--server', default=os.getenv('LIBRARY_SERVER'),
                        help=f"indirizzo dell'app (default: {DEFAULT_SERVER})")
    parser.add_argument('--piatto', action='store_true',
                        help='metti tutti i libri direttamente nella cartella di destinazione, senza sottocartelle')
    args = parser.parse_args()
    interactive = sys.stdin.isatty() and not (args.sorgente and args.codice)

    if interactive:
        print('ULTIMATE TRANSLATOR - Carica i tuoi libri nella libreria\n')

    source_arg = args.sorgente
    while True:
        if not source_arg:
            if not interactive:
                sys.exit('Indica la cartella dei libri da caricare.')
            source_arg = ask('Trascina qui la cartella dei libri (o scrivi il percorso) e premi Invio')
        source = clean_path(source_arg)
        if os.path.exists(source):
            break
        print(f'Percorso non trovato: {source}')
        if not interactive:
            sys.exit(1)
        source_arg = None

    books = collect_books(source)
    if not books:
        sys.exit(f'Nessun file EPUB o PDF trovato in {source}')
    total_size = sum(os.path.getsize(path) for path, _ in books if os.path.isfile(path))
    print(f'Trovati {len(books)} libri ({total_size / 1024 / 1024:.1f} MB) in {source}')

    server = normalize_server(args.server or (ask("Indirizzo dell'app", DEFAULT_SERVER) if interactive
                                              else DEFAULT_SERVER))
    code = normalize_code(args.codice)
    while len(code) != 20:
        if not interactive:
            sys.exit('Serve il codice della libreria (--codice): lo trovi nella pagina Cartelle (/libreria/cartelle), in basso a sinistra.')
        if code:
            print('Il codice ha 20 caratteri (per esempio ABCD-EFGH-JKMN-PQRS-TVWX).')
        code = normalize_code(ask('Codice libreria (pagina Cartelle > "Codice libreria", in basso a sinistra)'))

    library, opened, server = connect(server, code, interactive)
    print(f"Libreria {opened['code']} ({opened['books']} libri) su {server}")

    default_base = '' if os.path.isfile(source) else nfc(os.path.basename(source.rstrip(os.sep)))
    base = args.cartella
    if base is None and interactive:
        base = ask('Cartella di destinazione nella libreria', default_base)
    base = nfc((base if base is not None else default_base).strip().strip('/'))

    def destination(rel_dir):
        parts = [p for p in [base, '' if args.piatto else rel_dir] if p]
        return '/'.join(parts)

    paths = sorted({destination(rel) for _, rel in books if destination(rel)})
    folders = {}
    for start in range(0, len(paths), 400):  # big trees (e.g. a Calibre library) in several requests
        try:
            chunk = paths[start:start + 400]
            folders.update(library.post_json('/api/library/folders/tree',
                                             {'parent_id': None, 'paths': chunk})['folders'])
        except (ApiError, Unreachable) as e:
            sys.exit(f'Impossibile creare le cartelle nella libreria: {e}')

    added = duplicates = 0
    failures = []
    for index, (path, rel_dir) in enumerate(books, 1):
        try:
            size = os.path.getsize(path)
        except OSError as e:
            failures.append((path, str(e)))
            print(f'[{index}/{len(books)}] {os.path.basename(path)}  ERRORE: {e}')
            continue
        label = f'[{index}/{len(books)}] {nfc(os.path.basename(path))} ({size / 1024 / 1024:.1f} MB)'
        if size > MAX_SIZE:
            failures.append((path, 'file oltre 100 MB'))
            print(f'{label}  ERRORE: file oltre 100 MB')
            continue
        target = destination(rel_dir)
        try:
            book = library.upload(path, folders.get(target))['book']
            added += 1
            print(f"{label}  ok -> {target or 'fuori dalle cartelle'}  ({book['title']})")
        except ApiError as e:
            if e.payload.get('code') == 'duplicate':
                duplicates += 1
                print(f'{label}  gia nella libreria, saltato')
            else:
                failures.append((path, str(e)))
                print(f'{label}  ERRORE: {e}')
        except Unreachable as e:
            failures.append((path, f'connessione: {e}'))
            print(f'{label}  ERRORE di connessione: {e}')
        except Exception as e:  # one unreadable file must not stop the others
            failures.append((path, str(e)))
            print(f'{label}  ERRORE: {e}')

    print(f'\nFatto: {added} caricati, {duplicates} gia presenti, {len(failures)} errori.')
    if failures:
        print('Non caricati:')
        for path, reason in failures:
            print(f'  - {nfc(os.path.basename(path))}: {reason}')
        print('Puoi rilanciare lo script quando vuoi: i libri gia caricati vengono saltati.')
    if added:
        print('I tag automatici vengono assegnati in background: apri la pagina Cartelle (/libreria/cartelle).')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit('\nInterrotto. Rilancia lo script per continuare: i libri gia caricati vengono saltati.')
