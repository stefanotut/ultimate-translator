"""Tiene il magazzino pieno: genera l'audio del libro attivo, in ordine, sempre.

Perche' non serve andare veloci
-------------------------------
Sulla macchina gratuita una frase da 7 secondi richiede 27 secondi di calcolo.
La tentazione e' cercare macchine piu' veloci, ed e' la strada sbagliata: il
sistema non deve essere in tempo reale, deve essere un MAGAZZINO. Il conto che
conta e' giornaliero, non al minuto:

    sessione di 5 ore a 2x   consumi 10 ore di contenuto
                             ne produci 2,5 mentre ascolti
                             svuoti 7,5 ore di cuscinetto
    le altre 19 ore          produci 9,4 ore, consumi 0
                             -> il giorno dopo sei di nuovo pieno

Da qui la dimensione del cuscinetto: **8 ore di contenuto**, non 15 minuti.
Quindici minuti durerebbero dieci minuti di ascolto e sembrerebbero un guasto.

Perche' in ordine, e perche' un libro alla volta
------------------------------------------------
Si ascolta in ordine, quindi si genera in ordine: la frase che serve fra un'ora
vale piu' di quella che serve fra otto. E un libro alla volta perche' generare
in parallelo cinque libri significa non averne pronto nessuno.

Cosa NON fa
-----------
Non genera l'intera libreria: 200 libri completi sarebbero 53 GB su 46 di disco.
Degli altri libri tiene solo l'inizio, cosi' premere play e' comunque immediato.
"""
import os
import re
import sys
import time
import logging
import warnings

warnings.filterwarnings("ignore")
QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, QUI)

# .env PRIMA di importare tts: quel modulo legge l'ambiente al momento
# dell'import (URL della voce, motore, tetti). Senza questa riga il riempitore
# cercava la voce su 127.0.0.1 — dove non c'e' piu' — e ripiegava sul motore a
# pagamento, fallendo a ogni frase in silenzio.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(QUI, ".env"))
except Exception:
    pass

import tts
import store as store_mod

# Quanto contenuto tenere pronto davanti alla posizione, in ore.
CUSCINETTO_ORE = float(os.environ.get("CUSCINETTO_ORE", "8"))
# Degli altri libri basta l'inizio: serve solo a far partire subito il play.
AVVIO_MINUTI = float(os.environ.get("AVVIO_MINUTI", "10"))
# Caratteri al secondo di audio, misurato sulla voce Sara.
CAR_AL_SECONDO = 17.6
PAUSA = int(os.environ.get("RIEMPITORE_PAUSA", "20"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] riempitore: %(message)s")
logger = logging.getLogger(__name__)


CARTELLA_FRASI = os.path.join(QUI, "data", "frasi")


def frasi_del_libro_cache(job_id, percorso, tipo):
    """Come `frasi_del_libro`, ma analizza il libro UNA VOLTA SOLA.

    Non e' un'ottimizzazione cosmetica: rianalizzare l'EPUB a ogni giro (ebooklib
    piu' BeautifulSoup su un libro da 5.000 frasi) faceva un picco di memoria che
    su 954 MB spingeva il MODELLO VOCALE in swap. Risultato misurato: la resa
    scendeva da 0,249x a 0,169x, un terzo in meno, e la causa non era la sintesi
    ma il vicino di casa che gli rubava la RAM.

    La cache si invalida da sola se il file del libro cambia (dimensione o data):
    un libro ritradotto non deve ereditare le frasi vecchie.
    """
    import json, hashlib
    # La firma e' l'impronta del CONTENUTO, non dimensione+data.
    # Dimensione e data non sono un'identita': un `rsync` cambia la data, un
    # ripristino la riporta indietro, e due libri diversi possono avere la
    # stessa dimensione. Con l'impronta l'elenco calcolato sul Mac vale anche
    # sul server, che e' esattamente cio' che serve: quella macchina non deve
    # analizzare libri, non ha la memoria per farlo.
    try:
        h = hashlib.sha256()
        with open(percorso, "rb") as f:
            for blocco in iter(lambda: f.read(1 << 20), b""):
                h.update(blocco)
        firma = "sha256:" + h.hexdigest()[:32] + "|v1"     # v1 = versione del divisore
    except OSError:
        return []
    dove = os.path.join(CARTELLA_FRASI, "%s.json" % job_id)
    try:
        with open(dove, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("firma") == firma:
            return d.get("frasi") or []
    except Exception:
        pass
    frasi = frasi_del_libro(percorso, tipo)
    try:
        os.makedirs(CARTELLA_FRASI, exist_ok=True)
        tmp = dove + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"firma": firma, "frasi": frasi}, f, ensure_ascii=False)
        os.replace(tmp, dove)      # scrittura atomica: mai un file mezzo scritto
    except Exception:
        logger.debug("non riesco a salvare l'elenco frasi", exc_info=True)
    return frasi


def frasi_del_libro(percorso, tipo):
    """Le frasi del libro, in ordine di lettura."""
    if tipo == "epub":
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup
        libro = epub.read_epub(percorso, options={"ignore_ncx": True})
        pezzi = []
        for it in libro.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            s = BeautifulSoup(it.get_content(), "html.parser")
            for t in s.find_all(["script", "style"]):
                t.decompose()
            testo = s.get_text(" ", strip=True)
            if len(testo) > 120:
                pezzi.append(testo)
        grezzo = "\n".join(pezzi)
    elif tipo == "pdf":
        import fitz
        d = fitz.open(percorso)
        grezzo = "\n".join(d[i].get_text() for i in range(d.page_count))
        d.close()
    else:
        return []
    return tts.dividi_in_frasi(grezzo)


def gia_pronta(testo, voce):
    ch = tts.chiave(tts.normalizza(testo), voce, tts.profilo_locale())
    p = tts.percorso(ch)
    return os.path.exists(p) and os.path.getsize(p) > 0


def riempi(job, voce, quante_ore, store, fermati=None):
    """Genera in ordine finche' non ci sono `quante_ore` di contenuto pronte.

    Conta solo il contenuto NUOVO: le frasi gia' in cache non si rigenerano e
    non consumano niente, ma contano nel cuscinetto — e' proprio quello lo
    scopo di averle.
    """
    percorso = job.get("output_path")
    if not percorso or not os.path.exists(percorso):
        return 0, "il libro non ha piu' un file"

    frasi = frasi_del_libro_cache(job["id"], percorso, job.get("file_type"))
    if not frasi:
        return 0, "nessun testo leggibile"

    bersaglio = quante_ore * 3600
    pronte_s = fatte = falliti = 0
    for f in frasi:
        if fermati and fermati():
            break
        durata = len(f) / CAR_AL_SECONDO
        if gia_pronta(f, voce):
            pronte_s += durata
            if pronte_s >= bersaglio:
                break
            continue
        try:
            tts.sintetizza(f, voce, store=store)
            fatte += 1
            falliti = 0
            pronte_s += durata
        except tts.TettoRaggiunto:
            return fatte, "tetto giornaliero raggiunto"
        except tts.TroppoLungo:
            continue                      # frase anomala: si salta, non si muore
        except Exception as e:
            # Un errore isolato si salta. Ma se falliscono TUTTE di fila non e'
            # una frase difficile: e' il sistema che non funziona (voce
            # irraggiungibile, motore sbagliato, disco pieno). Prima lo
            # registravo a `debug` e il servizio restava "attivo" macinando
            # errori per ore senza che nessuno se ne accorgesse. Un guasto
            # invisibile e' peggio di un guasto rumoroso: adesso si arrende e
            # lo dice.
            falliti += 1
            if falliti >= 5:
                logger.error("Mi fermo su questo libro: %d frasi di fila fallite. "
                             "Ultimo errore: %s", falliti, str(e)[:160])
                return fatte, "guasto: %s" % str(e)[:80]
            logger.warning("frase fallita (%d di fila): %s", falliti, str(e)[:120])
            time.sleep(2)
        if pronte_s >= bersaglio:
            break
    return fatte, "pronte %.1f ore" % (pronte_s / 3600)


def libro_attivo(owner):
    """Il libro letto o ascoltato piu' di recente.

    E' la priorita' giusta: quello che stai leggendo e' quello che
    probabilmente ascolterai stasera. Si ricava da `reading`, che e' la stessa
    tabella dove vivono la posizione di lettura e quella di ascolto — una sola,
    condivisa.
    """
    try:
        mappa = store_mod.reading_map(owner)
        if not mappa:
            return None
        return max(mappa.items(), key=lambda kv: kv[1].get("updated_at") or 0)[0]
    except Exception:
        logger.debug("libro attivo non determinabile", exc_info=True)
        return None


def main():
    # store e' un modulo di funzioni, non una classe: si usa direttamente.
    store = store_mod
    owner = os.environ.get("RIEMPITORE_OWNER") or (
        (os.environ.get("ALLOWED_EMAILS") or "").split(",")[0].strip())
    if not owner:
        logger.error("Non so per chi lavorare: manca ALLOWED_EMAILS o RIEMPITORE_OWNER")
        sys.exit(1)
    voce = tts.voce_predefinita()
    logger.info("avviato per %s: cuscinetto %.0f ore sul libro attivo, "
                "%.0f minuti sugli altri", owner, CUSCINETTO_ORE, AVVIO_MINUTI)
    while True:
        try:
            jobs = [j for j in store.list_jobs(owner, limit=500)
                    if j.get("status") == "completed"]
            attivo = libro_attivo(owner)
            # prima il libro attivo, poi gli altri: l'ordine e' la priorita'
            jobs.sort(key=lambda j: 0 if j["id"] == attivo else 1)
            for j in jobs:
                ore = CUSCINETTO_ORE if j["id"] == attivo else AVVIO_MINUTI / 60.0
                fatte, come = riempi(j, voce, ore, store)
                if fatte:
                    logger.info("%s: +%d frasi (%s)",
                                (j.get("original_name") or j["id"])[:40], fatte, come)
                if come == "tetto giornaliero raggiunto":
                    time.sleep(600)
                    break
        except Exception:
            logger.exception("giro fallito")
        time.sleep(PAUSA)


if __name__ == "__main__":
    main()
