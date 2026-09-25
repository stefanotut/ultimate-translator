#!/usr/bin/env python3
"""
Scrive SMTP_PASSWORD nel file .env senza mostrarla a schermo.

La password per le app di Google viene mostrata come 4 gruppi da 4 caratteri
("abcd efgh ijkl mnop"): gli spazi vengono tolti in automatico, sono solo
un aiuto alla lettura.

    ./venv/bin/python imposta_password_smtp.py
"""

import io
import os
import re
import sys
import getpass

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def main():
    if not os.path.exists(ENV):
        print("Non trovo %s" % ENV)
        return 1

    print("Incolla la password per le app di Google (non verra' mostrata).")
    print("Se il terminale non ti fa incollare, usa Cmd+V comunque: funziona.\n")

    pwd = getpass.getpass("SMTP_PASSWORD: ").strip()
    pwd = re.sub(r"\s+", "", pwd)

    if not pwd:
        print("\nNiente inserito, non ho toccato il file.")
        return 1
    if len(pwd) != 16:
        print("\nAttenzione: le app password Google sono di 16 caratteri, "
              "questa ne ha %d." % len(pwd))
        if input("Vuoi salvarla lo stesso? [s/N] ").strip().lower() != "s":
            print("Annullato, file non modificato.")
            return 1

    lines = io.open(ENV, encoding="utf-8").read().splitlines(True)
    found = False
    for i, line in enumerate(lines):
        if line.startswith("SMTP_PASSWORD="):
            lines[i] = "SMTP_PASSWORD=%s\n" % pwd
            found = True
            break
    if not found:
        lines.append("\nSMTP_PASSWORD=%s\n" % pwd)

    # Scrittura atomica + permessi stretti: il file contiene segreti.
    tmp = ENV + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.replace(tmp, ENV)
    os.chmod(ENV, 0o600)

    print("\nSalvata in .env (%d caratteri, permessi 600)." % len(pwd))
    print("Ora riavvia il server perche' la legga.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
