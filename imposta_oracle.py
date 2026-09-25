"""Configura l'accesso a Oracle Cloud senza far passare segreti dalla chat.

Stesso principio della password SMTP: la chiave privata e i codici del tuo
account non devono comparire in una conversazione, non devono finire in un log
e non devono restare negli appunti. Qui si incollano in un terminale, il
programma scrive il file di configurazione con i permessi giusti e non stampa
nulla di sensibile.

Uso:
    ./venv/bin/python imposta_oracle.py
"""
import os
import re
import sys
import stat
import getpass

CARTELLA = os.path.expanduser("~/.oci")
CONFIG = os.path.join(CARTELLA, "config")
CHIAVE = os.path.join(CARTELLA, "oci_api_key.pem")


def chiedi(etichetta, esempio, obbligatorio=True):
    while True:
        v = input("%s\n  (%s)\n> " % (etichetta, esempio)).strip()
        if v or not obbligatorio:
            return v
        print("  Serve un valore.\n")


def main():
    print(__doc__)
    print("Apri la console Oracle: profilo (in alto a destra) -> "
          "«My profile» -> «API keys» -> «Add API key» -> «Generate API key "
          "pair» -> scarica la chiave privata e copia il riquadro di "
          "configurazione che appare.\n")

    os.makedirs(CARTELLA, exist_ok=True)
    os.chmod(CARTELLA, stat.S_IRWXU)

    scaricata = chiedi(
        "1) Dove hai salvato la chiave privata scaricata (file .pem)?",
        "di solito ~/Downloads/qualcosa.pem")
    scaricata = os.path.expanduser(scaricata.strip().strip("'\""))
    if not os.path.exists(scaricata):
        print("Non trovo il file: %s" % scaricata)
        return 1
    with open(scaricata, "rb") as f:
        dati = f.read()
    if b"PRIVATE KEY" not in dati:
        print("Quel file non sembra una chiave privata.")
        return 1
    with open(CHIAVE, "wb") as f:
        f.write(dati)
    os.chmod(CHIAVE, stat.S_IRUSR | stat.S_IWUSR)     # solo tu puoi leggerla
    print("   Chiave copiata in %s (permessi 600).\n" % CHIAVE)

    print("2) Ora incolla il riquadro di configurazione mostrato da Oracle.")
    print("   Finisci con una riga vuota.\n")
    righe = []
    while True:
        r = sys.stdin.readline()
        if not r.strip():
            break
        righe.append(r.rstrip("\n"))
    testo = "\n".join(righe)

    campi = {}
    for chiave in ("user", "fingerprint", "tenancy", "region"):
        m = re.search(r"^\s*%s\s*=\s*(.+)$" % chiave, testo, re.M)
        if m:
            campi[chiave] = m.group(1).strip()
    mancanti = [k for k in ("user", "fingerprint", "tenancy", "region") if k not in campi]
    if mancanti:
        print("Nel riquadro mancano: %s. Riprova incollandolo per intero."
              % ", ".join(mancanti))
        return 1

    with open(CONFIG, "w") as f:
        f.write("[DEFAULT]\n")
        for k in ("user", "fingerprint", "tenancy", "region"):
            f.write("%s=%s\n" % (k, campi[k]))
        f.write("key_file=%s\n" % CHIAVE)
    os.chmod(CONFIG, stat.S_IRUSR | stat.S_IWUSR)

    print("\nFatto. Configurazione in %s (permessi 600)." % CONFIG)
    print("Regione: %s" % campi["region"])
    print("\nOra puoi cancellare la chiave scaricata:\n  rm '%s'" % scaricata)
    print("\nProva che funzioni:\n  oci iam region list --output table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
