#!/bin/bash
# ULTIMATE TRANSLATOR - doppio clic (Mac) per caricare una cartella di libri nella tua libreria.
# Ti chiede la cartella (trascinala nella finestra), l'indirizzo dell'app e il codice libreria.
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    python3 import_books.py
else
    echo "Serve Python 3: installalo da https://www.python.org/downloads/ e riprova."
fi
echo
read -n 1 -s -r -p "Premi un tasto per chiudere questa finestra..."
echo
