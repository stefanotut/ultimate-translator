"""Servizio di voce: Kokoro via ONNX, a costo zero, sul server o sul Mac.

Perche' e' un processo a parte
------------------------------
Isola il guasto: se il modello si pianta o mangia memoria muore questo processo
e il portale resta in piedi (senza voce, e lo dice), invece di trascinare giu'
anche la lettura. Sulla macchina gratuita, con 1 GB di RAM, non e' un lusso.

Perche' ONNX e non PyTorch
--------------------------
Misurato: PyTorch arriva a 2,8 GB di picco, ONNX int8 sta in 577 MB. Sulla
macchina che tiene il portale (954 MB in tutto) PyTorch non ci sta e basta.
Ma la ragione vera e' un'altra, ed e' di correttezza: il Mac e il server devono
produrre la STESSA voce, perche' la cache e' condivisa e indicizzata sul testo.
Due motori diversi dietro la stessa chiave significa sentire due voci diverse
nello stesso capitolo, a seconda di chi ha generato quale frase.

Perche' si sintetizza a 1,25x
-----------------------------
Il calcolo e' proporzionale ai campioni prodotti, non al testo: generare piu'
veloce costa meno a parita' di contenuto. Misurato sul Mac: +23-45% di resa. La
velocita' scelta dall'utente NON e' questa: il lettore corregge con
`playbackRate = velocita_scelta / FATTORE`, cosi' il cursore resta libero e
cambiare velocita' non invalida un solo file di cache.

Il FATTORE non e' 1,25: il file non si accorcia in proporzione al parametro.
Misurato su due brani reali: 139,9s -> 119,5s e 128,7s -> 109,4s, cioe' 1,171.
Va rimisurato se si cambia modello o voce, e chi lo cambia deve cambiare anche
il PROFILO, altrimenti la cache vecchia viene riutilizzata per audio nuovo.

Si avvia con l'ambiente dedicato:
    ./vv/bin/python voce_locale.py
"""
import io
import os
import sys
import json
import time
import logging
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORTA = int(os.environ.get("VOCE_LOCALE_PORT", "5055"))
QUI = os.path.dirname(os.path.abspath(__file__))
MODELLO = os.environ.get("KOKORO_MODEL", os.path.join(QUI, "kokoro-int8.onnx"))
VOCI_FILE = os.environ.get("KOKORO_VOICES", os.path.join(QUI, "voices.bin"))
FFMPEG = os.environ.get("FFMPEG_PATH", "/usr/bin/ffmpeg")

# Velocita' a cui il modello genera. Entra nel PROFILO, quindi nella chiave di
# cache: cambiarla senza cambiare il profilo farebbe riusare l'audio vecchio.
VELOCITA = float(os.environ.get("TTS_SYNTH_SPEED", "1.25"))
# Quanto contenuto sta in un secondo di file, a quella velocita'. Misurato.
FATTORE = float(os.environ.get("TTS_SYNTH_FACTOR", "1.171"))
PROFILO = os.environ.get("TTS_PROFILE", "kokoro-onnx-int8@%.2f" % VELOCITA)

VOCI = {
    "sara":   {"id": "if_sara",   "nome": "Sara",   "genere": "femminile"},
    "nicola": {"id": "im_nicola", "nome": "Nicola", "genere": "maschile"},
}
VOCE_PREDEFINITA = "sara"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] voce: %(message)s")
logger = logging.getLogger(__name__)

_kokoro = None
_lucchetto = threading.Lock()


def _trova_espeak():
    """Cerca espeak-ng dove puo' stare, invece di darlo per scontato.

    I percorsi cambiano fra Mac (Homebrew) e Linux, e su Linux perfino fra x86 e
    ARM. Scriverli fissi significava un'immagine che funziona sul portatile e
    muore sul server.
    """
    dati = [os.environ.get("ESPEAK_DATA_PATH"),
            "/opt/homebrew/share/espeak-ng-data",
            "/usr/share/espeak-ng-data",
            "/usr/lib/x86_64-linux-gnu/espeak-ng-data",
            "/usr/lib/aarch64-linux-gnu/espeak-ng-data",
            "/usr/local/share/espeak-ng-data"]
    librerie = [os.environ.get("PHONEMIZER_ESPEAK_LIBRARY"),
                "/opt/homebrew/lib/libespeak-ng.dylib",
                "/usr/lib/x86_64-linux-gnu/libespeak-ng.so.1",
                "/usr/lib/aarch64-linux-gnu/libespeak-ng.so.1",
                "/usr/lib/libespeak-ng.so.1",
                "/usr/local/lib/libespeak-ng.so.1"]
    d = next((x for x in dati if x and os.path.exists(x)), None)
    l = next((x for x in librerie if x and os.path.exists(x)), None)
    return d, l


def kokoro():
    """Carica il modello una volta sola: ricrearlo a ogni frase costa piu' della
    frase stessa."""
    global _kokoro
    if _kokoro is None:
        with _lucchetto:
            if _kokoro is None:
                dati, libreria = _trova_espeak()
                if not dati:
                    raise RuntimeError(
                        "Manca espeak-ng. Mac: `brew install espeak-ng`. "
                        "Linux: `apt install espeak-ng`.")
                os.environ["ESPEAK_DATA_PATH"] = dati
                if libreria:
                    os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = libreria
                for f in (MODELLO, VOCI_FILE):
                    if not os.path.exists(f):
                        raise RuntimeError("Manca il file del modello: %s" % f)
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
                # Opzioni esplicite: lasciate al caso, ONNX Runtime apre un
                # thread per core. Su una macchina con due CPU condivise, e con
                # il portale che gira accanto, conviene tenerlo corto.
                o = ort.SessionOptions()
                o.intra_op_num_threads = int(os.environ.get("ONNX_THREADS", "2"))
                o.inter_op_num_threads = 1
                o.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                # L'arena di memoria di ONNX Runtime cresce per adattarsi alla
                # frase piu' lunga vista finora e non restituisce mai niente.
                # Misurato su questo server dopo qualche ora di lavoro: da 577 MB
                # a 2,1 GB (489 residenti + 1.657 in swap) su una macchina che ne
                # ha 954. Il risultato non e' un errore ma qualcosa di peggio: il
                # servizio continua a rispondere e diventa cento volte piu' lento,
                # perche' rilegge il modello dal disco a ogni frase.
                # Senza arena si alloca e si libera a ogni chiamata: qualche
                # punto percentuale piu' lento, memoria costante.
                o.enable_cpu_mem_arena = False
                t0 = time.time()
                sess = ort.InferenceSession(MODELLO, sess_options=o,
                                            providers=["CPUExecutionProvider"])
                _kokoro = Kokoro.from_session(sess, VOCI_FILE)
                logger.info("modello %s caricato in %.1fs (profilo %s)",
                            os.path.basename(MODELLO), time.time() - t0, PROFILO)
    return _kokoro


def in_mp3(audio, frequenza=24000):
    """Da campioni float32 a mp3, senza passare dal disco."""
    p = subprocess.run(
        [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "f32le", "-ar", str(frequenza), "-ac", "1", "-i", "pipe:0",
         "-b:a", "64k", "-f", "mp3", "pipe:1"],
        input=audio.astype("float32").tobytes(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + p.stderr.decode("utf-8", "ignore")[:200])
    return p.stdout


def sintetizza(testo, voce=None, velocita=None):
    scelta = VOCI.get(voce or VOCE_PREDEFINITA, VOCI[VOCE_PREDEFINITA])
    v = float(velocita or VELOCITA)
    t0 = time.time()
    audio, sr = kokoro().create(testo, voice=scelta["id"], speed=v, lang="it")
    dati = in_mp3(audio, sr)
    durata = len(audio) / sr
    calcolo = time.time() - t0
    # Si registra la resa in CONTENUTO, non in durata del file: a 1,25x il file
    # e' piu' corto del testo che contiene, e confondere le due cose fa credere
    # di essere piu' lenti di quanto si e'.
    logger.info("%d caratteri -> file %.1fs (contenuto %.1fs) in %.1fs = %.2fx",
                len(testo), durata, durata * FATTORE, calcolo,
                durata * FATTORE / max(calcolo, 1e-6))
    return dati


class Gestore(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _rispondi(self, codice, corpo, tipo="application/json"):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path.startswith("/salute"):
            self._rispondi(200, json.dumps({
                "stato": "vivo",
                "modello_caricato": _kokoro is not None,
                "voci": list(VOCI.keys()),
                "predefinita": VOCE_PREDEFINITA,
                # Il portale usa questi due per la chiave di cache e per il
                # cursore della velocita': non sono decorativi.
                "profilo": PROFILO,
                "velocita_sintesi": VELOCITA,
                "fattore": FATTORE,
            }).encode())
        else:
            self._rispondi(404, b'{"error":"non trovato"}')

    def do_POST(self):
        if not self.path.startswith("/sintetizza"):
            self._rispondi(404, b'{"error":"non trovato"}')
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            dati = json.loads(self.rfile.read(n) or b"{}")
            testo = (dati.get("testo") or "").strip()
            if not testo:
                self._rispondi(400, b'{"error":"niente da leggere"}')
                return
            mp3 = sintetizza(testo, dati.get("voce"), dati.get("velocita"))
            self._rispondi(200, mp3, "audio/mpeg")
        except Exception as e:
            logger.exception("sintesi fallita")
            self._rispondi(500, json.dumps({"error": str(e)[:300]}).encode())


def main():
    # Il modello si carica all'avvio, non alla prima frase: chi preme play non
    # deve aspettare dieci secondi senza capire perche'.
    try:
        kokoro()
    except Exception as e:
        logger.error("modello non caricato: %s", e)
        sys.exit(1)
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore)
    logger.info("in ascolto su http://127.0.0.1:%d — profilo %s, fattore %.3f",
                PORTA, PROFILO, FATTORE)
    srv.serve_forever()


if __name__ == "__main__":
    main()
