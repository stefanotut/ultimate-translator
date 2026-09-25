#!/bin/sh
# Avvia la voce e il portale nello stesso contenitore.
#
# La voce sta in un processo a parte apposta: se il modello si pianta o mangia
# memoria muore lui, e il portale resta in piedi dicendo che la voce non c'e',
# invece di trascinare giu' tutto.
set -e

# Torch, lasciato libero, apre un thread per core e arriva a 3,5 GB di picco.
# Con due thread sta sotto 1,6 GB e non va piu' lento: oltre due core Kokoro
# non guadagna nulla. Su una macchina da 6 GB e' la differenza fra funzionare
# e farsi uccidere dal sistema.
OMP_NUM_THREADS=${OMP_NUM_THREADS:-2} MKL_NUM_THREADS=${MKL_NUM_THREADS:-2} \
  python voce_locale.py &
VOCE=$!

# Se la voce muore, il contenitore non deve fingere che vada tutto bene.
trap "kill $VOCE 2>/dev/null || true" TERM INT

exec gunicorn app:app \
    --bind 0.0.0.0:${PORT:-8000} \
    --timeout 900 \
    --workers 1 \
    --threads 12 \
    --log-level info
