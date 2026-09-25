"""Audiolibri pre-generati: un file mp3 per capitolo, pronti da ascoltare.

Perche' esiste, e perche' e' fatto a coda
-----------------------------------------
Ascoltare frase per frase, generando al momento, funziona bene davanti al libro
ma non in macchina: serve rete a ogni frase, e ogni pausa si sente. Un audio
gia' pronto invece si scarica, si mette in coda e non chiede piu' niente.

Ma "creare subito l'audio di ogni libro" non e' possibile, e conviene saperlo:
Kokoro produce audio circa 5,6 volte piu' in fretta di quanto lo si ascolti.
Quindi

    un libro da 12 ore di ascolto  ->  circa 2 ore di Mac
    200 libri come quelli in libreria -> circa 416 ore, cioe' 17 giorni pieni

Non e' un difetto da correggere: e' quanto ci vuole. Da qui la scelta di una
coda con priorita': il libro che stai per ascoltare passa avanti, gli altri si
fanno di notte, uno alla volta, senza scaldare il Mac.

Lo spazio invece non e' un problema: a 32 kbps un'ora di parlato pesa 14 MB,
quindi 200 libri stanno in ~31 GB. C'e' comunque un tetto, e quando si supera si
buttano gli audiolibri che non si ascoltano da piu' tempo: rigenerarli non costa
denaro (la voce e' sul Mac), costa solo tempo.
"""
import os
import re
import time
import logging
import subprocess

import tts

logger = logging.getLogger(__name__)

CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "audiolibri")
FFMPEG = os.environ.get("FFMPEG_PATH", "/opt/homebrew/bin/ffmpeg")

# Spazio massimo per gli audiolibri gia' pronti.
MAX_GB = float(os.environ.get("AUDIOLIBRI_MAX_GB", "40"))

# Qualita' dell'mp3. 32 kbps mono e' piu' che sufficiente per una voce e pesa
# la meta' di 64: su un libro da 12 ore sono 170 MB invece di 340.
BITRATE = os.environ.get("AUDIOLIBRO_BITRATE", "32k")

# Un capitolo troppo lungo diventa un file scomodo (e una ripresa imprecisa):
# oltre questa soglia si spezza in piu' parti.
MAX_CARATTERI_CAPITOLO = int(os.environ.get("AUDIOLIBRO_MAX_CHARS", "24000"))

# ...e uno troppo corto e' altrettanto sbagliato. Molti EPUB scaricati sono
# spezzati in centinaia di file da poche righe: preso alla lettera, un libro
# diventava 334 tracce da un minuto l'una, impossibili da gestire in macchina.
# I pezzi consecutivi si uniscono fino a fare una traccia di durata umana
# (12.000 caratteri ~ 15 minuti di ascolto).
MIN_CARATTERI_CAPITOLO = int(os.environ.get("AUDIOLIBRO_MIN_CHARS", "12000"))


def cartella_di(job_id):
    return os.path.join(CARTELLA, job_id)


def _pulisci_titolo(t):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    return t[:80]


def capitoli_epub(percorso):
    """Restituisce [(titolo, testo)] rispettando l'ordine di lettura."""
    import warnings
    warnings.filterwarnings("ignore")
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    libro = epub.read_epub(percorso, options={"ignore_ncx": True})
    fuori = []
    for it in libro.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        s = BeautifulSoup(it.get_content(), "html.parser")
        for t in s.find_all(["script", "style"]):
            t.decompose()
        testo = s.get_text(" ", strip=True)
        if len(testo) < 200:            # frontespizi, pagine di stacco
            continue
        titolo = None
        h = s.find(["h1", "h2", "h3"])
        if h:
            titolo = _pulisci_titolo(h.get_text(" ", strip=True))
        fuori.append((titolo or _pulisci_titolo(testo[:60]), testo))
    return fuori


def capitoli_pdf(percorso, pagine_per_pezzo=10):
    """Il PDF non ha capitoli dichiarati: si raggruppa a blocchi di pagine."""
    import fitz
    d = fitz.open(percorso)
    fuori = []
    blocco, prima = [], 1
    for i in range(d.page_count):
        blocco.append(d[i].get_text())
        if len(blocco) >= pagine_per_pezzo or i == d.page_count - 1:
            testo = "\n".join(blocco).strip()
            if len(testo) > 200:
                fuori.append(("Pagine %d-%d" % (prima, i + 1), testo))
            blocco, prima = [], i + 2
    d.close()
    return fuori


def capitoli(percorso, tipo):
    if tipo == "epub":
        grezzi = capitoli_epub(percorso)
    elif tipo == "pdf":
        grezzi = capitoli_pdf(percorso)
    else:
        return []
    # Prima si UNISCONO i pezzi troppo corti, poi si spezzano quelli troppo
    # lunghi: l'ordine conta, altrimenti si unirebbero pezzi appena divisi.
    uniti = []
    for titolo, testo in grezzi:
        if uniti and len(uniti[-1][1]) < MIN_CARATTERI_CAPITOLO:
            # si tiene il titolo del primo pezzo: e' quello del capitolo vero
            uniti[-1] = (uniti[-1][0], uniti[-1][1] + "\n" + testo)
        else:
            uniti.append((titolo, testo))
    grezzi = uniti

    # I capitoli lunghissimi si spezzano: un file da un'ora e' scomodo da
    # riprendere e lento da scaricare in mobilita'.
    fuori = []
    for titolo, testo in grezzi:
        if len(testo) <= MAX_CARATTERI_CAPITOLO:
            fuori.append((titolo, testo))
            continue
        frasi = tts.dividi_in_frasi(testo)
        parte, lung, n = [], 0, 1
        for f in frasi:
            if lung + len(f) > MAX_CARATTERI_CAPITOLO and parte:
                fuori.append(("%s (%d)" % (titolo, n), " ".join(parte)))
                parte, lung, n = [], 0, n + 1
            parte.append(f)
            lung += len(f)
        if parte:
            fuori.append(("%s (%d)" % (titolo, n), " ".join(parte)))
    return fuori


def _unisci(pezzi, destinazione):
    """Concatena gli mp3 delle frasi in un solo file di capitolo."""
    elenco = destinazione + ".elenco"
    with open(elenco, "w", encoding="utf-8") as f:
        for p in pezzi:
            f.write("file '%s'\n" % p.replace("'", "'\\''"))
    p = subprocess.run(
        [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", elenco,
         "-c:a", "libmp3lame", "-b:a", BITRATE, "-ac", "1", "-ar", "24000",
         "-y", destinazione],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        os.remove(elenco)
    except OSError:
        pass
    if p.returncode != 0 or not os.path.exists(destinazione):
        raise RuntimeError("unione mp3 fallita: " +
                           p.stderr.decode("utf-8", "ignore")[:200])
    return destinazione


def durata(percorso):
    try:
        from mutagen.mp3 import MP3
        return MP3(percorso).info.length
    except Exception:
        return 0.0


def genera(job, voce, store, fermati=None, avanzamento=None):
    """Crea l'audiolibro di un libro gia' tradotto o importato.

    Riprende da dove era: i capitoli gia' fatti non si rifanno. Se il Mac si
    riavvia a meta' libro, alla ripresa si riparte dal primo capitolo mancante.
    """
    percorso = job["output_path"]
    if not percorso or not os.path.exists(percorso):
        raise RuntimeError("il libro non ha piu' un file")

    elenco = capitoli(percorso, job["file_type"])
    if not elenco:
        raise RuntimeError("nessun capitolo leggibile in questo formato")

    dove = cartella_di(job["id"])
    os.makedirs(dove, exist_ok=True)

    fatti = byte = 0
    secondi = 0.0
    for n, (titolo, testo) in enumerate(elenco, 1):
        if fermati and fermati():
            raise InterruptedError("interrotto")
        dest = os.path.join(dove, "cap-%03d.mp3" % n)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            fatti += 1
            byte += os.path.getsize(dest)
            secondi += durata(dest)
            if avanzamento:
                avanzamento(fatti, len(elenco))
            continue

        frasi = tts.dividi_in_frasi(testo)
        pezzi = []
        for f in frasi:
            if fermati and fermati():
                raise InterruptedError("interrotto")
            try:
                p, _ = tts.sintetizza(f, voce, store=store)
                pezzi.append(p)
            except tts.TroppoLungo:
                continue                     # frase anomala: si salta, non si muore
        if not pezzi:
            continue
        _unisci(pezzi, dest)
        fatti += 1
        byte += os.path.getsize(dest)
        secondi += durata(dest)
        if avanzamento:
            avanzamento(fatti, len(elenco))

    return {"capitoli": len(elenco), "capitoli_fatti": fatti,
            "byte": byte, "secondi": secondi}


def indice(job_id):
    """I capitoli gia' pronti, in ordine, con durata e peso."""
    dove = cartella_di(job_id)
    if not os.path.isdir(dove):
        return []
    fuori = []
    for nome in sorted(os.listdir(dove)):
        if not nome.endswith(".mp3"):
            continue
        p = os.path.join(dove, nome)
        fuori.append({"file": nome,
                      "numero": int(re.sub(r"\D", "", nome) or 0),
                      "byte": os.path.getsize(p),
                      "secondi": round(durata(p), 1)})
    return fuori


def elimina(job_id):
    """Toglie l'audiolibro dal disco. Si puo' sempre rifare: costa solo tempo."""
    import shutil
    dove = cartella_di(job_id)
    if os.path.isdir(dove):
        shutil.rmtree(dove, ignore_errors=True)
        return True
    return False


def pota(store):
    """Rientra nel tetto di spazio buttando gli audiolibri piu' trascurati."""
    limite = int(MAX_GB * 1024 ** 3)
    da_buttare = store.audiolibri_da_liberare(limite)
    for r in da_buttare:
        elimina(r["job_id"])
        store.audiolibro_dimentica(r["job_id"], r["owner"])
    if da_buttare:
        logger.info("Audiolibri: liberati %d libri (%.1f GB) per rientrare nel tetto",
                    len(da_buttare), sum(r["byte"] for r in da_buttare) / 1024 ** 3)
    return len(da_buttare)
