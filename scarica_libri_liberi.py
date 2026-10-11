#!/usr/bin/env python3
"""
ULTIMATE TRANSLATOR - Scarica i libri di profumeria liberi e li carica nella libreria

Scarica soltanto copie legali: scansioni di libri di pubblico dominio (Internet
Archive, Wellcome Collection) e pubblicazioni che l'editore stesso distribuisce
gratis (FAO, UNIDO, Wageningen University). I libri ancora protetti da
copyright non vengono scaricati: per quelli serve una copia acquistata, che si
carica con import_books.py come ogni altro libro.

Poi carica i PDF nella libreria con import_books.py (che deve stare nella
stessa cartella di questo script), nella cartella "Profumeria/Materie prime".
Si puo' rilanciare quando si vuole: i file gia scaricati non si riscaricano e
i libri gia nella libreria vengono saltati.

Esempi:
    python3 scarica_libri_liberi.py                  # scarica e carica (chiede il codice libreria)
    python3 scarica_libri_liberi.py --solo-scarica   # scarica soltanto
    python3 scarica_libri_liberi.py --codice ABCD-EFGH-JKMN-PQRS-TVWX

Serve solo la libreria standard di Python.
"""

import os
import re
import ssl
import sys
import json
import time
import argparse
import tempfile
import subprocess
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ARCHIVE = 'https://archive.org'
WELLCOME_API = 'https://api.wellcomecollection.org/catalogue/v2'
WELLCOME_IIIF = 'https://iiif.wellcomecollection.org'
DEFAULT_SERVER = 'https://89-168-31-56.nip.io'
DEFAULT_DEST = os.path.join(os.path.expanduser('~'), 'Downloads', 'Libri di profumeria liberi')
SUBFOLDER = 'Materie prime'
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UltimateTranslator/1.0 (libri di pubblico dominio)'
MAX_SIZE = 95 * 1024 * 1024        # la libreria accetta file fino a 100 MB
MIN_SIZE = 30 * 1024               # sotto questa soglia non e' un libro (pagina d'errore, anteprima)
RETRY_PAUSES = (5, 20, 60)         # errori di rete e server occupati (429 / 5xx)
PAUSE = 1.0                        # fra una richiesta e l'altra, per cortesia verso i siti
CHUNK = 1024 * 1024

# Licenze della Wellcome Collection che permettono di scaricare e conservare il libro.
OPEN_LICENSES = {'pdm', 'cc-0', 'cc-by', 'cc-by-nc', 'cc-by-nd', 'cc-by-nc-nd', 'cc-by-sa', 'cc-by-nc-sa', 'ogl', 'opl'}

# Solo i titoli della lista che si possono avere gratis e legalmente. Fonti in ordine di preferenza:
#   ('url', indirizzo)               PDF distribuito dall'editore o dalla biblioteca
#   ('archive', identificativo)      un documento preciso di Internet Archive
#   ('archive_cerca', {...})         ricerca su Internet Archive, solo edizioni fino a 'anno_max'
#   ('wellcome', id_opera)           un'opera della Wellcome Collection, solo con licenza libera
#   ('wellcome_cerca', {...})        ricerca nella Wellcome Collection, solo licenze libere
# 'anno_max' tiene la ricerca sulle edizioni di pubblico dominio (autori morti da oltre 70 anni).
BOOKS = [
    {'file': 'Sawer - Odorographia, prima serie (1892)',
     'fonti': [('archive', 'odorographianatu01sawe'),
               ('archive_cerca', {'q': 'title:(odorographia) AND mediatype:(texts)',
                                  'parole': ['odorographia'], 'anno_max': 1892})]},
    {'file': 'Sawer - Odorographia, seconda serie (1894)',
     'fonti': [('archive', 'odorographianatu02sawe'),
               ('archive_cerca', {'q': 'title:(odorographia) AND mediatype:(texts)',
                                  'parole': ['odorographia'], 'anno_min': 1893, 'anno_max': 1894})]},
    {'file': 'Gildemeister, Hoffmann - Die aetherischen Oele',
     'fonti': [('archive_cerca', {'q': 'creator:(gildemeister) AND mediatype:(texts)',
                                  'parole': ['therisch'], 'anno_max': 1916}),
               ('archive', 'DieAetherischenOeleBand11910-TheVolatileOils')]},
    {'file': 'Gildemeister, Hoffmann - The Volatile Oils, trad. inglese di Kremers',
     'fonti': [('archive_cerca', {'q': 'title:(volatile oils) AND creator:(gildemeister) AND mediatype:(texts)',
                                  'parole': ['volatile', 'oils'], 'anno_max': 1922})]},
    {'file': 'Parry - The Chemistry of Essential Oils and Artificial Perfumes',
     'fonti': [('url', 'https://iiif.wellcomecollection.org/pdf/b28059979'),
               ('archive_cerca', {'q': 'title:(chemistry essential oils artificial perfumes) AND creator:(parry)',
                                  'parole': ['essential', 'oils', 'perfumes'], 'anno_max': 1922})]},
    {'file': 'Charabot, Dupont, Pillet - Les huiles essentielles et leurs principaux constituants (1899)',
     'fonti': [('archive_cerca', {'q': 'title:(huiles essentielles) AND creator:(charabot)',
                                  'parole': ['huiles', 'essentielles'], 'anno_max': 1925}),
               ('wellcome_cerca', {'q': 'Charabot huiles essentielles constituants',
                                   'parole': ['huiles', 'essentielles'], 'anno_max': 1925})]},
    {'file': 'Charabot - Les parfums artificiels (1900)',
     'fonti': [('wellcome', 'fbd4qurm'),
               ('archive_cerca', {'q': 'title:(parfums artificiels) AND creator:(charabot)',
                                  'parole': ['parfums', 'artificiels'], 'anno_max': 1925})]},
    {'file': 'Cohn - Die Riechstoffe (1904)',
     'fonti': [('archive_cerca', {'q': 'title:(riechstoffe) AND creator:(cohn)',
                                  'parole': ['riechstoffe'], 'anno_max': 1925}),
               ('wellcome_cerca', {'q': 'Cohn Riechstoffe', 'parole': ['riechstoffe'], 'anno_max': 1925})]},
    {'file': "Parry - Parry's Cyclopaedia of Perfumery (1925)",
     'fonti': [('archive_cerca', {'q': 'title:(perfumery) AND creator:(parry) AND mediatype:(texts)',
                                  'parole': ['cyclop', 'perfumery'], 'anno_max': 1925}),
               ('wellcome_cerca', {'q': 'Parry cyclopaedia of perfumery',
                                   'parole': ['cyclop', 'perfumery'], 'anno_max': 1925})]},
    {'file': 'Coppen - Flavours and Fragrances of Plant Origin, FAO (1995)',
     'fonti': [('url', 'https://www.fao.org/docrep/018/v5350e/v5350e.pdf'),
               ('url', 'https://www.fao.org/3/v5350e/v5350e.pdf')]},
    {'file': 'Handa, Khanuja, Longo, Rakesh - Extraction Technologies for Medicinal and Aromatic Plants, UNIDO (2008)',
     'fonti': [('url', 'https://downloads.unido.org/ot/47/88/4788854/20001-_23676.pdf')]},
    {'file': 'Oyen, Nguyen Xuan Dung - PROSEA 19, Essential-oil Plants (1999)',
     'fonti': [('url', 'https://edepot.wur.nl/411171')]},
]

SOURCE_NAMES = {'url': 'sito dell\'editore', 'archive': 'Internet Archive', 'archive_cerca': 'Internet Archive',
                'wellcome': 'Wellcome Collection', 'wellcome_cerca': 'Wellcome Collection'}


class NotFound(Exception):
    """Questa fonte non ha il libro (o non in una copia libera): si passa alla successiva."""


# ---------------------------------------------------------------------------
# Rete
# ---------------------------------------------------------------------------

_ssl_context = None


def _mac_certificates():
    """Il Python di python.org sul Mac non vede i certificati del sistema finche' non si lancia
    'Install Certificates.command': li prendiamo dal portachiavi, senza chiedere nulla."""
    if sys.platform != 'darwin':
        return None
    pem = ''
    for keychain in ('/System/Library/Keychains/SystemRootCertificates.keychain',
                     '/Library/Keychains/System.keychain'):
        try:
            pem += subprocess.run(['security', 'find-certificate', '-a', '-p', keychain],
                                  capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    if 'BEGIN CERTIFICATE' not in pem:
        return None
    path = os.path.join(tempfile.gettempdir(), 'ultimate_translator_certificati.pem')
    with open(path, 'w') as fh:
        fh.write(pem)
    return path


def _certificate_problem(error):
    reason = getattr(error, 'reason', error)
    return isinstance(reason, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(reason)


def open_url(url, timeout=120):
    """urlopen con ritentativi per gli errori passeggeri. Solleva NotFound per le risposte definitive."""
    global _ssl_context
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    last = None
    for attempt in range(len(RETRY_PAUSES) + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context)
        except urllib.error.HTTPError as e:
            if e.code not in (408, 425, 429, 500, 502, 503, 504):
                raise NotFound(f'il sito ha risposto {e.code}') from None
            last = f'il sito ha risposto {e.code}'
        except (urllib.error.URLError, OSError) as e:
            if _ssl_context is None and _certificate_problem(e):
                bundle = _mac_certificates()
                if bundle:
                    _ssl_context = ssl.create_default_context(cafile=bundle)
                    os.environ['SSL_CERT_FILE'] = bundle       # vale anche per import_books.py
                    continue
            last = str(getattr(e, 'reason', None) or e)
        if attempt < len(RETRY_PAUSES):
            time.sleep(RETRY_PAUSES[attempt])
    raise NotFound(f'connessione non riuscita: {last}')


def get_json(url):
    with open_url(url, timeout=60) as response:
        try:
            return json.loads(response.read().decode('utf-8', 'replace'))
        except ValueError:
            raise NotFound('risposta non valida') from None


def download_pdf(url, target, attempts=2):
    """Scarica in target.part e rinomina solo se e' davvero un PDF completo."""
    for attempt in range(attempts):
        try:
            return _download_once(url, target)
        except NotFound as e:
            if attempt + 1 == attempts or not str(e).startswith('download interrotto'):
                raise


def _download_once(url, target):
    partial = target + '.part'
    try:
        with open_url(url, timeout=900) as response, open(partial, 'wb') as out:
            first = response.read(CHUNK)
            if not first.startswith(b'%PDF-'):
                raise NotFound('il file non e\' un PDF')
            out.write(first)
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        size = os.path.getsize(partial)
        if size < MIN_SIZE:
            raise NotFound('file troppo piccolo per essere il libro')
        os.replace(partial, target)
        return size
    except (OSError, urllib.error.URLError) as e:
        raise NotFound(f'download interrotto: {e}') from None
    finally:
        if os.path.exists(partial):
            os.remove(partial)


# ---------------------------------------------------------------------------
# Fonti
# ---------------------------------------------------------------------------

def plain(text):
    """'Ätherischen Öle' e 'Aetherischen Oele' diventano la stessa cosa."""
    text = str(text or '').casefold()
    for umlaut, letter in (('ae', 'a'), ('oe', 'o'), ('ue', 'u')):
        text = text.replace(umlaut, letter)
    text = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in text if not unicodedata.combining(c))


def first_year(value):
    if isinstance(value, list):
        value = ' '.join(str(v) for v in value)
    match = re.search(r'1[5-9]\d\d|20\d\d', str(value or ''))
    return int(match.group()) if match else None


def year_ok(year, spec):
    return year is not None and spec.get('anno_min', 0) <= year <= spec['anno_max']


def words_ok(title, spec):
    title = plain(title)
    return all(plain(word) in title for word in spec['parole'])


def archive_pdf(identifier):
    """Il PDF migliore di un documento di Internet Archive: con testo, sotto i 95 MB se possibile."""
    meta = get_json(f'{ARCHIVE}/metadata/{urllib.parse.quote(identifier)}')
    if not meta.get('files') or meta.get('is_dark'):
        raise NotFound(f'{identifier} non e\' disponibile')
    status = str((meta.get('metadata') or {}).get('possible-copyright-status', '')).upper()
    if re.search(r'(?<!NOT_)(?<!NOT )IN[_ ]COPYRIGHT', status):
        raise NotFound(f'{identifier} e\' ancora protetto da copyright')

    def rank(f):
        name, fmt = f.get('name', ''), f.get('format', '')
        size = int(f.get('size') or 0)
        quality = 0 if fmt == 'Text PDF' or name.endswith('_text.pdf') else 1 if not name.endswith('_bw.pdf') else 2
        return (size > MAX_SIZE, quality, size)

    pdfs = [f for f in meta['files'] if f.get('name', '').lower().endswith('.pdf')]
    if not pdfs:
        raise NotFound(f'{identifier} non ha un PDF')
    best = min(pdfs, key=rank)
    return [f"{ARCHIVE}/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(best['name'])}"], identifier


def archive_search(spec, used):
    params = [('q', spec['q']), ('fl[]', 'identifier'), ('fl[]', 'title'), ('fl[]', 'year'),
              ('fl[]', 'date'), ('rows', '25'), ('output', 'json'), ('sort[]', 'downloads desc')]
    docs = get_json(f'{ARCHIVE}/advancedsearch.php?{urllib.parse.urlencode(params)}').get('response', {}).get('docs', [])
    for doc in docs:
        identifier = doc.get('identifier')
        year = first_year(doc.get('year')) or first_year(doc.get('date'))
        if not identifier or identifier in used or not year_ok(year, spec) or not words_ok(doc.get('title'), spec):
            continue
        try:
            return archive_pdf(identifier)
        except NotFound:
            continue
    raise NotFound('nessuna edizione di pubblico dominio trovata')


def wellcome_pdfs(work):
    """I PDF di un'opera Wellcome, solo se digitalizzata con licenza libera e consultabile."""
    for item in work.get('items') or []:
        for location in item.get('locations') or []:
            url = location.get('url') or ''
            kind = (location.get('locationType') or {}).get('id', '')
            if kind != 'iiif-presentation' and '/presentation/' not in url:
                continue
            license_id = (location.get('license') or {}).get('id', '')
            statuses = {(c.get('status') or {}).get('id', '') for c in location.get('accessConditions') or []}
            if license_id not in OPEN_LICENSES or statuses & {'restricted', 'closed', 'licensed-resources', 'permission-required'}:
                raise NotFound(f'licenza non libera ({license_id or "sconosciuta"})')
            manifest = url.rstrip('/').rsplit('/', 1)[-1]
            return [f'{WELLCOME_IIIF}/pdf/{manifest}'], f"wellcome:{work.get('id')}"
    raise NotFound('opera non digitalizzata')


def wellcome_work(work_id, used):
    work = get_json(f'{WELLCOME_API}/works/{urllib.parse.quote(work_id)}?include=items,production')
    return wellcome_pdfs(work)


def wellcome_search(spec, used):
    params = urllib.parse.urlencode({'query': spec['q'], 'availabilities': 'online',
                                     'include': 'items,production', 'pageSize': '20'})
    for work in get_json(f'{WELLCOME_API}/works?{params}').get('results', []):
        dates = [d.get('label') for event in work.get('production') or [] for d in event.get('dates') or []]
        if f"wellcome:{work.get('id')}" in used or not year_ok(first_year(dates), spec) \
                or not words_ok(work.get('title'), spec):
            continue
        try:
            return wellcome_pdfs(work)
        except NotFound:
            continue
    raise NotFound('nessuna copia libera trovata')


def resolve(kind, value, used):
    """(lista di URL di PDF, chiave della copia) per una fonte."""
    if kind == 'url':
        return [value], value
    if kind == 'archive':
        return archive_pdf(value)
    if kind == 'archive_cerca':
        return archive_search(value, used)
    if kind == 'wellcome':
        return wellcome_work(value, used)
    if kind == 'wellcome_cerca':
        return wellcome_search(value, used)
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# Programma
# ---------------------------------------------------------------------------

def fetch_all(folder, books=None, pause=None):
    books = BOOKS if books is None else books
    pause = PAUSE if pause is None else pause
    os.makedirs(folder, exist_ok=True)
    used = set()
    got, missing = [], []
    for index, book in enumerate(books, 1):
        label = f"[{index}/{len(books)}] {book['file']}"
        target = os.path.join(folder, book['file'] + '.pdf')
        if os.path.exists(target) and os.path.getsize(target) >= MIN_SIZE:
            print(f'{label}  gia scaricato')
            got.append(target)
            continue
        reasons = []
        for kind, value in book['fonti']:
            try:
                urls, key = resolve(kind, value, used)
                if key in used:
                    raise NotFound('copia gia usata per un altro titolo')
                size = download_pdf(urls[0], target)
                used.add(key)
                print(f'{label}  scaricato da {SOURCE_NAMES[kind]} ({size / 1024 / 1024:.1f} MB)')
                got.append(target)
                break
            except NotFound as e:
                reasons.append(f'{SOURCE_NAMES[kind]}: {e}')
            finally:
                time.sleep(pause)
        else:
            print(f'{label}  NON TROVATO gratis ({"; ".join(reasons)})')
            missing.append((book['file'], reasons))
    return got, missing


def find_importer():
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.getcwd()):
        path = os.path.join(folder, 'import_books.py')
        if os.path.isfile(path):
            return path
    return None


def main():
    parser = argparse.ArgumentParser(description='Scarica i libri di profumeria liberi e li carica nella libreria.')
    parser.add_argument('--dove', default=DEFAULT_DEST, help=f'cartella dei download (default: {DEFAULT_DEST})')
    parser.add_argument('--cartella', default='Profumeria', help='cartella della libreria (default: Profumeria)')
    parser.add_argument('--codice', default=os.getenv('LIBRARY_CODE'), help='codice della libreria')
    parser.add_argument('--server', default=os.getenv('LIBRARY_SERVER') or DEFAULT_SERVER)
    parser.add_argument('--solo-scarica', action='store_true', help='scarica senza caricare nella libreria')
    args = parser.parse_args()

    root = os.path.abspath(os.path.expanduser(args.dove))
    print('ULTIMATE TRANSLATOR - Libri di profumeria liberi (pubblico dominio e open access)\n')
    got, missing = fetch_all(os.path.join(root, SUBFOLDER))
    print(f'\nScaricati: {len(got)} di {len(BOOKS)}, nella cartella {root}')
    if missing:
        print('Senza una copia gratuita e legale online (restano da comprare o da cercare in biblioteca):')
        for name, _ in missing:
            print(f'  - {name}')
    if args.solo_scarica or not got:
        return 0 if got else 1

    importer = find_importer()
    if not importer:
        print('\nimport_books.py non e\' in questa cartella: trascina la cartella scaricata '
              'nella pagina Libreria del sito per caricarla.')
        return 0
    print(f"\nCarico i libri nella libreria, cartella \"{args.cartella}/{SUBFOLDER}\"...\n")
    command = [sys.executable, importer, root, '--cartella', args.cartella, '--server', args.server]
    if args.codice:
        command += ['--codice', args.codice]
    return subprocess.call(command, env=os.environ.copy())


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit('\nInterrotto. Rilancia lo script per continuare: i file gia scaricati non si riscaricano.')
