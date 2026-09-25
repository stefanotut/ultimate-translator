"""Sintesi vocale: trasforma il testo di un libro in audio italiano.

Perche' e' fatto cosi'
----------------------
L'audio costa. Misurato sul posto con `gpt-4o-mini-tts`: 430 caratteri di testo
diventano ~30 secondi di parlato, generati in 4-5 secondi. Quindi la generazione
va circa dieci volte piu' veloce dell'ascolto: si puo' produrre l'audio mentre
l'utente ascolta, senza fargli attendere un libro intero prima di partire.

Da qui tre scelte:

1. **Si genera una frase alla volta, su richiesta**, non il libro intero. Chi
   ascolta mezzo capitolo e smette non ha pagato le altre nove ore.
2. **Tutto viene messo in cache su disco**, con chiave sul testo esatto: un libro
   riascoltato, o una frase riletta, non si ripaga mai. La cache NON vive in
   `uploads/` o `outputs/`, che la pulizia periodica svuota dopo 48 ore.
3. **C'e' un tetto di caratteri al giorno.** Un ciclo impazzito nel lettore
   potrebbe altrimenti macinare l'intera libreria in pochi minuti.

La voce
-------
Tutte le voci OpenAI sono state provate sullo stesso brano italiano preso da un
libro vero, e le trascrizioni riportate al testo di partenza per misurare quanto
l'italiano venga davvero pronunciato (una voce con accento straniero si
ritrascrive peggio). Fedelta' misurata fra 94% e 100%: nessuna sbaglia lingua.
La differenza rimasta e' di timbro e di ritmo, e quella la sceglie l'orecchio di
chi ascolta — per questo la voce si cambia dal lettore, con prova immediata.
"""
import os
import json
import re
import time
import hashlib
import logging
import threading

logger = logging.getLogger(__name__)

CARTELLA_AUDIO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "audio")

MODELLO = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts")
VOCE_PREDEFINITA = os.environ.get("TTS_VOICE", "marin")

# ---------------------------------------------------------------------------
# Due motori
# ---------------------------------------------------------------------------
# "locale"  Kokoro, gira sul Mac dell'utente (voce_locale.py, porta 5055).
#           Costo zero, nessuna rete, e sul suo Mac produce audio circa cinque
#           volte piu' in fretta di quanto si ascolti.
# "openai"  il servizio a pagamento. Serve dove il locale non c'e' (per esempio
#           su Render, dove non gira alcun modello).
#
# "auto" sceglie il locale se il servizio risponde, altrimenti OpenAI. La scelta
# viene DICHIARATA all'interfaccia: l'utente deve sapere quale voce sta usando e
# se sta spendendo, non indovinarlo.
MOTORE = os.environ.get("TTS_ENGINE", "auto")
URL_LOCALE = os.environ.get("VOCE_LOCALE_URL", "http://127.0.0.1:5055")

# Il servizio locale puo' essere spento o riacceso mentre l'app gira: si
# ricontrolla ogni tanto, ma non a ogni frase (sarebbe una richiesta in piu' per
# ogni riga letta).
_INTERVALLO_CONTROLLO = 30.0
# profilo e fattore hanno un valore di ripiego, ma quello buono lo dice il
# servizio vocale in /salute: e' lui che sa con cosa sta generando.
_stato_locale = {"vivo": False, "quando": 0.0,
                 "profilo": "kokoro-onnx-int8@1.25", "fattore": 1.171}

# Tetto di spesa: caratteri sintetizzabili in un giorno. Un libro intero sta
# intorno ai 500.000 caratteri, quindi il valore predefinito lascia ascoltare
# un libro e mezzo al giorno e ferma qualunque ciclo impazzito.
MAX_CARATTERI_GIORNO = int(os.environ.get("TTS_MAX_CHARS_DAY", "800000"))

# Oltre questa lunghezza una singola richiesta viene rifiutata: le frasi vere
# non superano mai questa misura, quindi un valore piu' alto significherebbe
# soltanto che qualcosa sta mandando un capitolo intero per sbaglio.
MAX_CARATTERI_RICHIESTA = int(os.environ.get("TTS_MAX_CHARS_REQUEST", "1600"))

ISTRUZIONI = (
    "Stai leggendo ad alta voce un audiolibro in italiano. "
    "Voce calda e naturale, ritmo tranquillo da narratore professionista, "
    "pause vere alla punteggiatura, nessuna enfasi da pubblicita'. "
    "Pronuncia italiana corretta, senza accento straniero."
)

# Fedelta' = quanto il parlato, ritrascritto e forzato sull'italiano, coincide
# col testo di partenza. Parole/min: il passo di un audiolibro sta fra 140 e 160.
VOCI = [
    {"id": "alloy",   "nome": "Alloy",   "genere": "neutra",  "fedelta": 98.6, "ppm": 145,
     "nota": "Passo da audiolibro, timbro neutro."},
    {"id": "fable",   "nome": "Fable",   "genere": "neutra",  "fedelta": 98.6, "ppm": 148,
     "nota": "La piu' spedita, buona per saggi."},
    {"id": "onyx",    "nome": "Onyx",    "genere": "maschile", "fedelta": 100.0, "ppm": 129,
     "nota": "Pronuncia perfetta, voce profonda e lenta."},
    {"id": "marin",   "nome": "Marin",   "genere": "femminile", "fedelta": 98.6, "ppm": 134,
     "nota": "Fra le piu' recenti, molto naturale. Predefinita."},
    {"id": "cedar",   "nome": "Cedar",   "genere": "maschile", "fedelta": 95.8, "ppm": 142,
     "nota": "Fra le piu' recenti, calda."},
    {"id": "coral",   "nome": "Coral",   "genere": "femminile", "fedelta": 98.6, "ppm": 127,
     "nota": "Morbida e posata."},
    {"id": "ballad",  "nome": "Ballad",  "genere": "neutra",  "fedelta": 98.6, "ppm": 136,
     "nota": "Espressiva, adatta alla narrativa."},
    {"id": "shimmer", "nome": "Shimmer", "genere": "femminile", "fedelta": 98.6, "ppm": 132,
     "nota": "Chiara e brillante."},
    {"id": "verse",   "nome": "Verse",   "genere": "neutra",  "fedelta": 98.6, "ppm": 118,
     "nota": "Molto lenta: da alzare a 1,2x."},
    {"id": "sage",    "nome": "Sage",    "genere": "femminile", "fedelta": 97.8, "ppm": 118,
     "nota": "Pacata, da alzare a 1,2x."},
    {"id": "nova",    "nome": "Nova",    "genere": "femminile", "fedelta": 97.0, "ppm": 142,
     "nota": "Vivace."},
    {"id": "ash",     "nome": "Ash",     "genere": "maschile", "fedelta": 97.0, "ppm": 132,
     "nota": "Asciutta."},
    {"id": "echo",    "nome": "Echo",    "genere": "maschile", "fedelta": 94.4, "ppm": 146,
     "nota": "La meno fedele delle tredici: inciampa su qualche parola."},
]
VOCI_VALIDE = {v["id"] for v in VOCI}

# Le voci del motore locale. Misurate con lo stesso metro delle altre: stesso
# brano, ri-trascrizione forzata sull'italiano, confronto col testo di partenza.
VOCI_LOCALI = [
    {"id": "sara",   "nome": "Sara",   "genere": "femminile", "fedelta": 97.1, "ppm": 166,
     "nota": "Gratis, gira sul tuo Mac, funziona senza rete. Predefinita."},
    {"id": "nicola", "nome": "Nicola", "genere": "maschile", "fedelta": 97.1, "ppm": 149,
     "nota": "Gratis, gira sul tuo Mac, funziona senza rete."},
]
VOCI_LOCALI_VALIDE = {v["id"] for v in VOCI_LOCALI}
VOCE_LOCALE_PREDEFINITA = os.environ.get("TTS_VOICE_LOCALE", "sara")


def _locale_risponde():
    ora = time.time()
    if ora - _stato_locale["quando"] < _INTERVALLO_CONTROLLO:
        return _stato_locale["vivo"]
    vivo = False
    try:
        import urllib.request
        with urllib.request.urlopen(URL_LOCALE + "/salute", timeout=2) as r:
            vivo = r.status == 200
            if vivo:
                # Profilo e fattore arrivano dal servizio, non sono scritti qui:
                # e' il servizio che sa con quale modello e a quale velocita'
                # sta generando, e sbagliarli significa riusare audio vecchio.
                d = json.loads(r.read() or b"{}")
                _stato_locale["profilo"] = d.get("profilo") or _stato_locale["profilo"]
                _stato_locale["fattore"] = float(d.get("fattore") or
                                                 _stato_locale["fattore"])
    except Exception:
        vivo = False
    _stato_locale.update(vivo=vivo, quando=ora)
    return vivo


def profilo_locale():
    """Identifica motore + precisione + velocita' di sintesi.

    Entra nella chiave di cache. Senza, cambiando modello o velocita' si
    riascolterebbe l'audio vecchio credendolo nuovo — e il guasto sarebbe
    silenzioso, che e' il peggiore.
    """
    _locale_risponde()
    return _stato_locale["profilo"]


def fattore_locale():
    """Quanto contenuto sta in un secondo di file audio.

    Serve al cursore: `playbackRate = velocita_scelta / fattore`. Non e' uguale
    alla velocita' di sintesi — il file non si accorcia in proporzione al
    parametro — e va misurato, non dedotto.
    """
    _locale_risponde()
    return _stato_locale["fattore"]


def motore_attivo():
    """'locale' o 'openai'. Non mente mai: se dice locale, e' locale."""
    if MOTORE == "locale":
        return "locale"
    if MOTORE == "openai":
        return "openai"
    return "locale" if _locale_risponde() else "openai"


def voci_disponibili(motore=None):
    return VOCI_LOCALI if (motore or motore_attivo()) == "locale" else VOCI


def voce_predefinita(motore=None):
    return (VOCE_LOCALE_PREDEFINITA if (motore or motore_attivo()) == "locale"
            else VOCE_PREDEFINITA)

# Frase di prova del selettore voce: corta, con accenti e punteggiatura vera,
# cosi' si sente subito il ritmo e non solo il timbro.
FRASE_CAMPIONE = ("Questa e' la mia voce. Posso leggerti un libro intero, "
                  "con calma, mentre guidi o mentre segui il testo.")

_lucchetti = {}
_lucchetto_globale = threading.Lock()


def _lucchetto(chiave):
    """Un lucchetto per chiave: due richieste della stessa frase non la pagano due volte."""
    with _lucchetto_globale:
        l = _lucchetti.get(chiave)
        if l is None:
            l = _lucchetti[chiave] = threading.Lock()
        return l


def voce_valida(voce, motore=None):
    motore = motore or motore_attivo()
    if motore == "locale":
        return voce if voce in VOCI_LOCALI_VALIDE else VOCE_LOCALE_PREDEFINITA
    return voce if voce in VOCI_VALIDE else VOCE_PREDEFINITA


def normalizza(testo):
    """Ripulisce il testo prima di leggerlo.

    Gli spazi doppi e gli a capo dentro un paragrafo non cambiano il senso ma
    cambiano la chiave della cache: normalizzando, la stessa frase incontrata
    due volte riusa lo stesso file.
    """
    testo = re.sub(r"\s+", " ", (testo or "")).strip()
    # I trattini di sillabazione a fine riga arrivano dentro il testo estratto
    # dal PDF e verrebbero letti come pause.
    testo = re.sub(r"(\w)-\s(\w)", r"\1\2", testo)
    return testo


def chiave(testo, voce, modello=None):
    # Il modello entra nella chiave: cosi' cambiando motore o voce non si
    # riascolta per sbaglio l'audio dell'altro.
    grezzo = "\x00".join([normalizza(testo), voce, modello or MODELLO, ISTRUZIONI])
    return hashlib.sha256(grezzo.encode("utf-8")).hexdigest()


def percorso(ch):
    # Sottocartelle a due lettere: una sola cartella con decine di migliaia di
    # file rallenta ogni accesso al disco.
    return os.path.join(CARTELLA_AUDIO, ch[:2], ch + ".mp3")


class TroppoLungo(ValueError):
    pass


class TettoRaggiunto(RuntimeError):
    pass


def _client():
    from openai import OpenAI
    chiave_api = os.environ.get("OPENAI_API_KEY")
    if not chiave_api:
        raise RuntimeError("Manca OPENAI_API_KEY: la lettura ad alta voce non e' disponibile.")
    return OpenAI(api_key=chiave_api, timeout=120)


def _sintetizza_locale(testo, voce):
    import urllib.request
    req = urllib.request.Request(
        URL_LOCALE + "/sintetizza", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"testo": testo, "voce": voce}).encode("utf-8"))
    # Generoso: la prima frase dopo l'avvio aspetta il caricamento del modello.
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.headers.get("Content-Type", "").startswith("application/json"):
            raise RuntimeError(json.loads(r.read()).get("error", "voce locale non disponibile"))
        return r.read()



def _sintetizza_cloud(testo, voce, store, vc):
    """Genera col provider a consumo e mette in cache sotto il SUO profilo.

    Il profilo del provider entra nella chiave: audio locale e audio cloud non
    devono mai finire sotto la stessa chiave, altrimenti si riascolta l'uno
    credendo di avere l'altro. Ogni segmento pagato resta sul server per sempre:
    si paga una volta sola.
    """
    ch = chiave(testo, voce or vc.VOCE, vc.PROFILO)
    dest = percorso(ch)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        if store is not None:
            store.audio_usato(ch)
        return dest, True

    with _lucchetto(ch):
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            if store is not None:
                store.audio_usato(ch)
            return dest, True
        dati = vc.sintetizza(testo, store, voce=voce or vc.VOCE)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".parziale"
        with open(tmp, "wb") as f:
            f.write(dati)
        os.replace(tmp, dest)          # atomica: mai un mp3 troncato in cache
        if store is not None:
            store.audio_registrato(ch, dest, len(dati), len(testo), voce or vc.VOCE)
    return dest, False


def sintetizza(testo, voce=None, store=None, cloud=False):
    """Restituisce (percorso_del_file, era_gia_in_cache).

    Non solleva per un semplice errore di rete: riprova, e solo dopo si arrende.
    Chi chiama deve poter distinguere "non disponibile ora" da "non disponibile mai".
    """
    testo = normalizza(testo)
    if not testo:
        raise ValueError("Niente da leggere.")
    if len(testo) > MAX_CARATTERI_RICHIESTA:
        raise TroppoLungo("Frase troppo lunga: %d caratteri." % len(testo))

    motore = motore_attivo()
    # Il cloud e' l'unico che garantisce i TEMPI: misurato, 159 caratteri al
    # secondo (9,0x il tempo reale) contro lo 0,25x delle macchine gratuite.
    # Si usa quando il libro non e' gia' pronto e l'utente sta aspettando.
    #
    # Un libro NON cambia motore a meta': il profilo si sceglie al primo ascolto
    # e resta. Le due implementazioni di Kokoro (int8 locale e modello pieno del
    # provider) sono simili ma non identiche — misurato: 6% di durata in piu' —
    # e alternarle capitolo per capitolo si sentirebbe.
    if cloud:
        import voce_cloud
        if voce_cloud.disponibile():
            return _sintetizza_cloud(testo, voce, store, voce_cloud)
        logger.warning("Richiesto il cloud ma manca il token: uso il motore locale")

    voce = voce_valida(voce, motore)
    # Il profilo, non un nome generico: contiene motore, precisione e velocita'
    # di sintesi. Cambiarne uno deve produrre chiavi nuove, altrimenti si
    # riascolta l'audio vecchio senza accorgersene.
    modello = profilo_locale() if motore == "locale" else MODELLO
    ch = chiave(testo, voce, modello)
    dest = percorso(ch)

    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        if store is not None:
            store.audio_usato(ch)
        return dest, True

    with _lucchetto(ch):
        # Un'altra richiesta identica potrebbe averlo appena generato.
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            if store is not None:
                store.audio_usato(ch)
            return dest, True

        # Il tetto di spesa vale solo per il motore a pagamento: il locale non
        # costa nulla, e fermarlo a meta' libro sarebbe solo un fastidio.
        if (motore == "openai" and store is not None
                and not store.tts_entro_il_tetto(len(testo), MAX_CARATTERI_GIORNO)):
            raise TettoRaggiunto(
                "Tetto giornaliero raggiunto (%d caratteri): la lettura ad alta voce "
                "riprende domani." % MAX_CARATTERI_GIORNO)

        ultimo = None
        for tentativo in range(3):
            try:
                if motore == "locale":
                    dati = _sintetizza_locale(testo, voce)
                else:
                    r = _client().audio.speech.create(
                        model=MODELLO, voice=voce, input=testo,
                        instructions=ISTRUZIONI, response_format="mp3")
                    dati = r.read()
                break
            except Exception as e:
                ultimo = e
                if tentativo == 2:
                    raise
                time.sleep(1.5 * (tentativo + 1))
        else:                                        # pragma: no cover
            raise ultimo

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Scrittura atomica: un'interruzione a meta' lascerebbe in cache un mp3
        # troncato che verrebbe poi servito per sempre come se fosse buono.
        temporaneo = dest + ".parziale"
        with open(temporaneo, "wb") as f:
            f.write(dati)
        os.replace(temporaneo, dest)

        if store is not None:
            store.audio_registrato(ch, dest, len(dati), len(testo), voce)
            store.tts_addebita(len(testo))
        logger.info("Voce generata: %d caratteri, voce %s, %d byte", len(testo), voce, len(dati))
        return dest, False


def campione(voce, store=None):
    """Audio della frase di prova per il selettore della voce."""
    return sintetizza(FRASE_CAMPIONE, voce, store=store)[0]


# ---------------------------------------------------------------------------
# Divisione in frasi
# ---------------------------------------------------------------------------
# Il lettore EPUB non ha bisogno di questa funzione: le frasi gliele segna gia'
# foliate dentro l'SSML. Serve al PDF, dove c'e' soltanto lo strato di testo.

_ABBREVIAZIONI = (
    "sig", "sigg", "dott", "dr", "prof", "avv", "ing", "arch", "on", "rev",
    "ecc", "es", "pag", "pagg", "cfr", "vol", "cap", "art", "fig", "tab",
    "n", "nn", "num", "sec", "a.C", "d.C", "ca", "ss", "op", "cit", "ibid",
)


def frasi_con_posizioni(testo, minimo=40):
    """Come `dividi_in_frasi`, ma dice anche DOVE comincia e finisce ogni frase.

    Serve al PDF: li' il testo e' uno strato di riquadri sopra un'immagine, e per
    evidenziare la frase che si sta ascoltando bisogna sapere quali riquadri
    copre. Gli indici sono riferiti alla stringa ricevuta, non a una sua
    versione ripulita, altrimenti non combacerebbero piu' con nulla.

    Restituisce [{"inizio": i, "fine": j, "testo": <ripulito, da leggere>}].
    """
    if not testo:
        return []

    tagli = [0]
    for m in re.finditer(r"(?<=[.!?…])(?=\s)", testo):
        tagli.append(m.start())
    tagli.append(len(testo))

    pezzi = []
    for k in range(len(tagli) - 1):
        inizio, fine = tagli[k], tagli[k + 1]
        if not testo[inizio:fine].strip():
            continue
        if pezzi:
            prima = testo[pezzi[-1][0]:pezzi[-1][1]].rstrip()
            ultima = re.search(r"([\w\.]+)\.$", prima)
            abbreviazione = (ultima and
                             ultima.group(1).rstrip(".").lower() in _ABBREVIAZIONI)
            numero = re.search(r"\d\.$", prima)
            iniziale = re.search(r"(?:^|\s)[A-Z]\.$", prima)
            if abbreviazione or numero or iniziale or len(prima.strip()) < minimo:
                pezzi[-1][1] = fine
                continue
        pezzi.append([inizio, fine])

    fuori = []
    for inizio, fine in pezzi:
        ripulito = normalizza(testo[inizio:fine])
        if not ripulito:
            continue
        # Una frase oltre il tetto va spezzata: se il server la rifiutasse, quel
        # pezzo di libro resterebbe muto e la lettura si fermerebbe li'.
        while len(ripulito) > MAX_CARATTERI_RICHIESTA:
            quota = MAX_CARATTERI_RICHIESTA / float(len(ripulito))
            meta = inizio + max(1, int((fine - inizio) * quota))
            spazio = testo.rfind(" ", inizio, meta)
            meta = spazio if spazio > inizio else meta
            parte = normalizza(testo[inizio:meta])
            if parte:
                fuori.append({"inizio": inizio, "fine": meta, "testo": parte})
            inizio = meta
            ripulito = normalizza(testo[inizio:fine])
        if ripulito:
            fuori.append({"inizio": inizio, "fine": fine, "testo": ripulito})
    return fuori


def dividi_in_frasi(testo, minimo=40):
    """Divide un testo italiano in frasi da leggere.

    Non usa una libreria apposta perche' ne servirebbe una pesante per un
    guadagno minimo: le insidie vere dell'italiano sono poche e note, cioe' le
    abbreviazioni ("dott.", "ecc.", "pag."), i numeri col punto e le iniziali
    puntate. Le frasi troppo corte vengono unite alla successiva: una voce che
    si ferma ogni tre parole non sembra un narratore.
    """
    testo = normalizza(testo)
    if not testo:
        return []

    pezzi = re.split(r"(?<=[.!?…])(?=[\s ])", testo)
    frasi = []
    for p in pezzi:
        p = p.strip()
        if not p:
            continue
        if frasi:
            precedente = frasi[-1]
            ultima_parola = re.search(r"([\w\.]+)\.$", precedente)
            abbreviazione = (ultima_parola and
                             ultima_parola.group(1).rstrip(".").lower() in _ABBREVIAZIONI)
            numero = re.search(r"\d\.$", precedente)
            iniziale = re.search(r"(?:^|\s)[A-Z]\.$", precedente)
            if abbreviazione or numero or iniziale or len(precedente) < minimo:
                frasi[-1] = precedente + " " + p
                continue
        frasi.append(p)

    # Una frase piu' lunga del tetto va spezzata, altrimenti la richiesta viene
    # rifiutata e quel pezzo di libro resterebbe muto.
    finali = []
    for f in frasi:
        while len(f) > MAX_CARATTERI_RICHIESTA:
            taglio = f.rfind(" ", 0, MAX_CARATTERI_RICHIESTA)
            if taglio <= 0:
                taglio = MAX_CARATTERI_RICHIESTA
            finali.append(f[:taglio].strip())
            f = f[taglio:].strip()
        if f:
            finali.append(f)
    return finali
