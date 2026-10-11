#!/bin/bash
# ULTIMATE TRANSLATOR - doppio clic (Mac): cerca sul Mac i libri della lista di profumeria che hai gia
# (Download, Documenti, Scrivania, iCloud Drive, Calibre...) e li carica nella libreria, cartella "Profumeria".
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    python3 trova_libri_comprati.py "$@"
else
    echo "Serve Python 3: installalo da https://www.python.org/downloads/ e riprova."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere questa finestra..."
echo
