# Immagine per far girare il portale SEMPRE ONLINE, senza dipendere dal Mac.
#
# Contiene tutto: il portale, la voce italiana Kokoro e gli strumenti di sistema
# che le servono. Cosi' chi ascolta non ha bisogno ne' del Mac acceso ne' di
# spazio sul telefono: l'audio nasce e resta sul server.
#
# Due pacchetti di sistema non sono opzionali:
#   ffmpeg     converte l'audio prodotto dal modello in mp3
#   espeak-ng  serve a Kokoro per la fonetica. I pacchetti Python che
#              dovrebbero portarselo dietro hanno dentro il percorso della
#              macchina di build (/Users/runner/...) e non funzionano: va
#              installato dal sistema, e il programma lo cerca da solo.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg espeak-ng espeak-ng-data libespeak-ng1 \
    && rm -rf /var/lib/apt/lists/*

# I percorsi di espeak-ng NON si scrivono qui: cambiano fra x86 e ARM, e la
# macchina di destinazione e' ARM. Li cerca `voce_locale._trova_espeak()`.
#
# TTS_ENGINE=locale e non "auto": con "auto", se la voce locale si rompe il
# sistema passerebbe in silenzio a OpenAI e comincerebbe a fatturare. Meglio
# che l'ascolto si fermi con un errore visibile.
ENV FFMPEG_PATH=/usr/bin/ffmpeg \
    PYTHONUNBUFFERED=1 \
    TTS_ENGINE=locale \
    OMP_NUM_THREADS=2

WORKDIR /app

# Prima le dipendenze: cosi' un cambio di codice non ricompila torch.
COPY requirements.txt requirements-voce.txt ./
# torch va preso dall'indice "cpu": quello normale si porta dietro ~1,2 GB di
# librerie CUDA che su un server senza scheda video non servono a nulla.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r requirements-voce.txt

COPY . .

# I dati (database, libri, cache audio) stanno su un volume: devono
# sopravvivere ai riavvii e ai nuovi deploy, altrimenti si perdono libreria,
# posizioni di lettura e audio gia' pagato in tempo di calcolo.
VOLUME ["/app/data", "/app/outputs", "/app/uploads"]

EXPOSE 8000
CMD ["./avvio.sh"]
