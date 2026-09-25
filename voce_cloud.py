"""Voce a consumo: la garanzia che premendo Play si parta, e il freno di spesa.

Perche' esiste
--------------
Le macchine gratuite tengono la libreria e macinano quando c'e' tempo, ma non
garantiscono NIENTE sui tempi: misurato, una frase costa 20-60 secondi e la resa
oscilla. Se carichi un libro dal telefono col Mac spento e premi Play, quel
percorso non ti fa ascoltare. Questo modulo e' l'unica parte che promette
"parte adesso", e la paga a consumo.

Le regole, che valgono piu' del codice
--------------------------------------
1. NESSUNA chiamata parte se il tetto non la copre. Il costo si stima PRIMA,
   si prenota in modo atomico, e solo dopo si chiama. Chi controlla il budget
   dopo aver speso non ha un budget: ha un rendiconto.
2. Nessuna ricarica automatica, mai. Finito il credito, si torna alle macchine
   gratuite e alla cache: l'ascolto rallenta, la carta no.
3. Il profilo del provider entra nella chiave di cache. Audio locale e audio
   cloud non devono MAI finire sotto la stessa chiave: sono due voci diverse
   dello stesso modello, e mescolarle a meta' capitolo si sente.
4. Il token sta solo sul server, in `.env`, mai nel browser e mai nei log.
"""
import os
import json
import time
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# --- configurazione ----------------------------------------------------------
PROVIDER = os.environ.get("TTS_CLOUD_PROVIDER", "deepinfra")
TOKEN = os.environ.get("DEEPINFRA_TOKEN", "")
MODELLO = os.environ.get("TTS_CLOUD_MODEL", "hexgrad/Kokoro-82M")
VOCE = os.environ.get("TTS_CLOUD_VOICE", "if_sara")
VELOCITA = float(os.environ.get("TTS_SYNTH_SPEED", "1.25"))
URL = os.environ.get("TTS_CLOUD_URL",
                     "https://api.deepinfra.com/v1/inference/" + MODELLO)

# Prezzo dichiarato dal provider, per carattere. Serve a STIMARE prima di
# chiamare: il costo vero, quando l'API lo restituisce, viene registrato a parte.
PREZZO_PER_CARATTERE = float(os.environ.get("TTS_CLOUD_PRICE_PER_M", "0.62")) / 1e6

# Tetto applicativo, in dollari. E' il secondo freno: il primo e' il tetto
# impostato nel pannello del provider. Servono entrambi, perche' proteggono da
# guasti diversi (un bug qui, un abuso la').
TETTO_MENSILE = float(os.environ.get("TTS_CLOUD_CAP_USD", "10"))

PROFILO = os.environ.get(
    "TTS_CLOUD_PROFILE",
    "%s:%s@%.2f" % (PROVIDER, MODELLO.split("/")[-1], VELOCITA))


# Il portale chiama le voci con nomi brevi (`sara`), il provider vuole gli
# identificativi Kokoro (`if_sara`). Passare il nome breve tale e quale fa
# rispondere 422, e l'errore non dice quale campo sia sbagliato: e' successo.
NOMI_VOCE = {
    "sara": "if_sara",
    "nicola": "im_nicola",
}


def voce_provider(nome):
    """Da `sara` a `if_sara`. Se e' gia' un id Kokoro lo lascia stare."""
    if not nome:
        return VOCE
    return NOMI_VOCE.get(nome, nome)


class NonConfigurato(RuntimeError):
    pass


class TettoSuperato(RuntimeError):
    pass


def disponibile():
    """Vero solo se c'e' un token. Non indovina, non ripiega: o c'e' o non c'e'."""
    return bool(TOKEN)


def costo_stimato(testo):
    return len(testo) * PREZZO_PER_CARATTERE


def _mese():
    return time.strftime("%Y-%m")


def speso_questo_mese(store):
    try:
        return float(store.cloud_speso(_mese()) or 0.0)
    except Exception:
        logger.exception("non riesco a leggere la spesa: mi comporto come se fosse esaurita")
        return TETTO_MENSILE          # in dubbio non si spende


def residuo(store):
    return max(0.0, TETTO_MENSILE - speso_questo_mese(store))


def sintetizza(testo, store, voce=None, velocita=None, formato="mp3"):
    """Genera UNA richiesta, dopo aver prenotato il costo. Restituisce i byte.

    Solleva `TettoSuperato` PRIMA di chiamare se il credito non basta: e'
    l'unico ordine accettabile, perche' l'errore dopo la spesa non serve a
    niente.
    """
    if not disponibile():
        raise NonConfigurato(
            "Manca DEEPINFRA_TOKEN: la voce a consumo non e' configurata.")
    testo = (testo or "").strip()
    if not testo:
        raise ValueError("Niente da leggere.")

    stima = costo_stimato(testo)
    # prenotazione atomica: se due richieste partono insieme, una sola passa
    if not store.cloud_prenota(_mese(), stima, TETTO_MENSILE):
        raise TettoSuperato(
            "Tetto di spesa raggiunto (%.2f $ al mese): l'audio a pagamento e' "
            "sospeso. Restano le macchine gratuite e la cache." % TETTO_MENSILE)

    corpo = json.dumps({
        "text": testo,
        "preset_voice": voce_provider(voce),
        "speed": float(velocita or VELOCITA),
        "output_format": formato,
    }).encode("utf-8")
    req = urllib.request.Request(
        URL, data=corpo, method="POST",
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            tipo = r.headers.get("Content-Type", "")
            dati = r.read()
    except Exception as e:
        # la richiesta non e' andata: si restituisce la prenotazione, altrimenti
        # una rete ballerina esaurirebbe il budget senza produrre un secondo
        # di audio
        store.cloud_rilascia(_mese(), stima)
        raise RuntimeError("voce a consumo non disponibile: %s" % str(e)[:200])

    # Alcune API restituiscono JSON con l'audio in base64 e il costo: si accetta
    # entrambe le forme invece di dare per scontata quella che ci si aspetta.
    if tipo.startswith("application/json"):
        d = json.loads(dati)
        b64 = d.get("audio") or ""
        if b64.startswith("data:"):
            b64 = b64.split(",", 1)[-1]
        import base64
        audio = base64.b64decode(b64) if b64 else b""
        reale = None
        for k in ("inference_status", "usage"):
            v = d.get(k) or {}
            if isinstance(v, dict) and v.get("cost") is not None:
                reale = float(v["cost"])
        if reale is not None:
            store.cloud_correggi(_mese(), stima, reale)
    else:
        audio = dati

    if not audio:
        store.cloud_rilascia(_mese(), stima)
        raise RuntimeError("il provider non ha restituito audio")

    store.cloud_registra(_mese(), len(testo), stima, PROVIDER)
    logger.info("cloud: %d caratteri, %d byte, %.1fs, ~%.4f $ (residuo %.2f $)",
                len(testo), len(audio), time.time() - t0, stima, residuo(store))
    return audio
