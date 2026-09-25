#!/bin/bash
# Porta il portale sulla macchina Oracle gratuita: codice, libreria, HTTPS.
#
# Si lancia dal Mac. E' fatto per essere rilanciato: ogni passo controlla se e'
# gia' stato fatto, e i dati (libreria, posizioni di lettura, annotazioni) non
# vengono mai cancellati.
#
# NIENTE DOCKER, ed e' una scelta, non una pigrizia: la macchina gratuita ha
# 1 GB di RAM e 1/8 di CPU. Costruire un'immagine che si porta dietro torch
# (1,2 GB) su quel ferro vorrebbe ore e non ci starebbe. Il portale da solo e'
# leggero: flask, ebooklib, PyMuPDF. Gira nudo, con un venv e systemd.
#
# LA VOCE NON STA QUI. Misurato su questa stessa macchina: una frase da 7,4
# secondi richiede 34 secondi di calcolo (0,2x il tempo reale), cioe' cinque
# volte piu' lenta di quanto ci metti ad ascoltarla. La memoria basterebbe
# (535 MB su 954), e' la CPU che non c'e'. Percio' qui si mette il portale — che
# ti fa leggere col Mac spento — e la voce trova casa altrove.
#
# Tre trappole gia' disinnescate:
#   1. L'immagine Ubuntu di Oracle ha un firewall LOCALE che blocca tutto tranne
#      la 22: aprire le porte nella console non basta.
#   2. Con 1 GB di RAM serve lo swap, altrimenti il primo PDF grosso fa uccidere
#      il processo dal sistema e il portale sparisce senza spiegazioni.
#   3. I dati stanno in /dati e sono collegati dentro /app: cosi' un rsync del
#      codice non puo' portarseli via nemmeno per sbaglio.
set -euo pipefail

cd "$(dirname "$0")"
IP=$(cat .vm_ip 2>/dev/null || true)
[ -n "$IP" ] || { echo "Non so l'indirizzo della macchina."; exit 1; }
[ -f .env ] || { echo "Manca .env: senza, il portale non ha ne' chiavi ne' email."; exit 1; }

SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 ubuntu@$IP"
dimmi() { printf '\n\033[1m%s\033[0m\n' "$*"; }

dimmi "Macchina: $IP"
$SSH true || { echo "Non risponde via SSH."; exit 1; }

# --- 1. codice ---------------------------------------------------------------
# rsync e non git: il .env non e' nel repo (e non deve esserci), ma qui serve.
dimmi "Copio il codice"
rsync -az --delete \
  --exclude venv --exclude venv-voce --exclude vv --exclude .git \
  --exclude __pycache__ --exclude '*.pyc' --exclude .DS_Store \
  --exclude data --exclude outputs --exclude uploads \
  --exclude '*.onnx' --exclude 'voices.bin' \
  ./ ubuntu@"$IP":/app/

# --- 2. dati -----------------------------------------------------------------
# Libreria, posizioni di lettura, annotazioni, cache audio gia' pagata: e' la
# roba che non si puo' rifare. Va prima di tutto il resto.
dimmi "Copio libreria e dati (769 MB: ci vuole qualche minuto)"
[ -d data ]    && rsync -az data/    ubuntu@"$IP":/dati/data/
[ -d outputs ] && rsync -az outputs/ ubuntu@"$IP":/dati/outputs/

# --- 3. ambiente e servizio --------------------------------------------------
dimmi "Preparo l'ambiente Python e il servizio"
$SSH 'bash -s' <<'REMOTO'
set -e
cd /app
# i dati vivono fuori dal codice: un rsync sbagliato non puo' toccarli
for d in data outputs uploads; do
    [ -L "$d" ] || { rm -rf "$d"; ln -s "/dati/$d" "$d"; }
done
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

# TTS_ENGINE=locale e non "auto": con "auto", non trovando la voce locale il
# sistema passerebbe a OpenAI SENZA DIRLO e comincerebbe a fatturare. Cosi'
# invece l'ascolto si ferma con un errore visibile. Meglio un guasto di una
# bolletta a sorpresa.
grep -q '^TTS_ENGINE=' .env || echo 'TTS_ENGINE=locale' >> .env

sudo tee /etc/systemd/system/traduttore.service >/dev/null <<'UNIT'
[Unit]
Description=Ultimate Translator
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/app
Environment=PYTHONUNBUFFERED=1
# --timeout 600: tradurre un libro e' lungo, il worker non va ucciso a meta'.
# 1 worker e 8 thread: i thread condividono la memoria, i worker no, e qui di
# memoria ce n'e' 1 GB in tutto.
ExecStart=/app/venv/bin/gunicorn app:app --bind 127.0.0.1:8000 \
          --timeout 600 --workers 1 --threads 8
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable -q traduttore
sudo systemctl restart traduttore
sleep 6
systemctl is-active traduttore
REMOTO

# --- 4. HTTPS ----------------------------------------------------------------
# Caddy prende da solo un certificato valido per <ip>.nip.io: nessun dominio da
# comprare, nessun account, e l'indirizzo non cambia piu' (a differenza dei
# tunnel usa-e-getta, che cambiano URL a ogni riavvio).
DOMINIO="${IP//./-}.nip.io"
dimmi "Metto l'HTTPS su https://$DOMINIO"
$SSH "bash -s $DOMINIO" <<'REMOTO'
set -e
DOMINIO=$1
if ! command -v caddy >/dev/null; then
    sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update -qq && sudo apt-get install -y -qq caddy >/dev/null
fi
# request_body: un EPUB da 40 MB deve poter passare
printf '%s {\n    request_body {\n        max_size 60MB\n    }\n    reverse_proxy 127.0.0.1:8000\n}\n' "$DOMINIO" \
  | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy
sleep 10
systemctl is-active caddy
REMOTO

dimmi "Controllo che risponda davvero"
sleep 5
curl -sS --max-time 25 "https://$DOMINIO/healthz" && echo || echo "  (non ha risposto: guarda i log con: ssh ubuntu@$IP 'journalctl -u traduttore -n 50')"

dimmi "Il portale e' su:  https://$DOMINIO"
