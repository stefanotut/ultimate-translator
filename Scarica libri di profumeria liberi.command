#!/bin/bash
# ULTIMATE TRANSLATOR - doppio clic (Mac): scarica i libri di profumeria liberi (pubblico dominio e
# open access) e li carica nella tua libreria, nella cartella "Profumeria/Materie prime".
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    python3 scarica_libri_liberi.py
else
    echo "Serve Python 3: installalo da https://www.python.org/downloads/ e riprova."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere questa finestra..."
echo
