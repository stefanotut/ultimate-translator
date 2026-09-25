#!/bin/bash
# ULTIMATE TRANSLATOR - Script di avvio
# Powered by OpenAI + Anthropic

cd "$(dirname "$0")"

echo ""
echo "=============================================="
echo "  ULTIMATE TRANSLATOR"
echo "  Powered by OpenAI + Anthropic"
echo "=============================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  ERRORE: Python 3 non trovato."
    echo "  Installa Python 3 prima di procedere."
    exit 1
fi

echo "  Python: $(python3 --version)"

# Create venv if needed
if [ ! -d "venv" ]; then
    echo ""
    echo "  Creazione ambiente virtuale..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "  Installazione dipendenze..."
pip install -r requirements.txt --quiet 2>/dev/null

# Check .env
if [ ! -f ".env" ]; then
    echo ""
    echo "  ATTENZIONE: File .env non trovato!"
    echo "  Crea un file .env con le tue chiavi API:"
    echo "  OPENAI_API_KEY=sk-..."
    echo "  ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
    exit 1
fi

echo ""
echo "  Stato chiavi API:"

# Check OpenAI key
if grep -q "OPENAI_API_KEY=" .env 2>/dev/null; then
    OPENAI_KEY=$(grep "OPENAI_API_KEY=" .env | head -1 | cut -d'=' -f2)
    if [ -n "$OPENAI_KEY" ] && ! echo "$OPENAI_KEY" | grep -q "sk-xxxx"; then
        echo "  OpenAI:    CONFIGURATO"
    else
        echo "  OpenAI:    NON CONFIGURATO"
        echo "               Modifica .env e inserisci la tua OPENAI_API_KEY"
        echo "               Ottienila da: https://platform.openai.com/api-keys"
    fi
else
    echo "  OpenAI:    NON CONFIGURATO"
fi

# Check Anthropic key
if grep -q "ANTHROPIC_API_KEY=" .env 2>/dev/null; then
    ANTHROPIC_KEY=$(grep "ANTHROPIC_API_KEY=" .env | head -1 | cut -d'=' -f2)
    if [ -n "$ANTHROPIC_KEY" ] && ! echo "$ANTHROPIC_KEY" | grep -q "sk-ant-xxxx"; then
        echo "  Anthropic: CONFIGURATO"
    else
        echo "  Anthropic: NON CONFIGURATO"
        echo "               Modifica .env e inserisci la tua ANTHROPIC_API_KEY"
        echo "               Ottienila da: https://console.anthropic.com/settings/keys"
    fi
else
    echo "  Anthropic: NON CONFIGURATO"
fi

echo ""

# Check at least one key is present
HAS_KEY=false
if grep -q "OPENAI_API_KEY=" .env 2>/dev/null; then
    KEY_VAL=$(grep "OPENAI_API_KEY=" .env | head -1 | cut -d'=' -f2)
    if [ -n "$KEY_VAL" ] && ! echo "$KEY_VAL" | grep -q "sk-xxxx"; then
        HAS_KEY=true
    fi
fi
if grep -q "ANTHROPIC_API_KEY=" .env 2>/dev/null; then
    KEY_VAL=$(grep "ANTHROPIC_API_KEY=" .env | head -1 | cut -d'=' -f2)
    if [ -n "$KEY_VAL" ] && ! echo "$KEY_VAL" | grep -q "sk-ant-xxxx"; then
        HAS_KEY=true
    fi
fi

if [ "$HAS_KEY" = false ]; then
    echo "  ATTENZIONE: Nessuna chiave API configurata!"
    echo "  Devi configurare almeno una chiave API (OpenAI o Anthropic)"
    echo "  nel file .env per poter utilizzare il traduttore."
    echo ""
fi

# Start server
echo "  Avvio ULTIMATE TRANSLATOR..."
echo "  Apri il browser: http://localhost:${PORT:-5001}"
echo ""
python3 app.py
