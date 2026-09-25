"""Configura la voce a consumo senza far passare il token dalla chat.

Stesso principio della password SMTP e della chiave Oracle: il token non deve
comparire in una conversazione, non deve finire in un log e non deve restare
negli appunti. Si incolla in un terminale, il programma lo scrive nel `.env`
del server con i permessi giusti e non lo stampa mai.

Uso:
    ./venv/bin/python imposta_voce_cloud.py
"""
import os
import re
import sys
import getpass
import subprocess

SERVER = os.environ.get("SERVER_PORTALE", "ubuntu@89.168.31.56")
ENV_REMOTO = "/app/.env"


def chiedi(testo, predefinito=None):
    v = input("%s%s: " % (testo, " [%s]" % predefinito if predefinito else "")).strip()
    return v or (predefinito or "")


def main():
    print(__doc__)
    print("Prima di continuare, sul sito del provider devi aver fatto TRE cose:")
    print("  1. disattivato la RICARICA AUTOMATICA")
    print("  2. impostato un LIMITE DI SPESA")
    print("  3. caricato del credito prepagato")
    print("Se non le hai fatte, interrompi con Ctrl-C e fallle prima.\n")

    token = getpass.getpass("Incolla il token (non si vedra' mentre digiti): ").strip()
    if not token or len(token) < 20:
        print("Token troppo corto o vuoto: non ho scritto niente.")
        return 1

    tetto = chiedi("Tetto di spesa MENSILE in dollari, applicato dal portale", "10")
    try:
        float(tetto)
    except ValueError:
        print("Il tetto deve essere un numero.")
        return 1

    righe = {
        "DEEPINFRA_TOKEN": token,
        "TTS_CLOUD_PROVIDER": "deepinfra",
        "TTS_CLOUD_MODEL": "hexgrad/Kokoro-82M",
        "TTS_CLOUD_VOICE": "if_sara",
        "TTS_CLOUD_CAP_USD": tetto,
    }

    # Il token NON passa sulla riga di comando: finirebbe nella lista dei
    # processi del server e nella cronologia della shell. Viaggia sullo standard
    # input.
    #
    # E l'aiutante viene COPIATO sul server invece di essere passato a
    # `python3 -c`: ssh unisce gli argomenti in una stringa sola e la shell
    # remota li rilegge, facendo a pezzi qualunque script su piu' righe. E'
    # esattamente cosi' che questa funzione ha fallito la prima volta.
    aiutante = '''import sys, os
p = sys.argv[1]
nuove = dict(l.split("=", 1) for l in sys.stdin.read().splitlines() if "=" in l)
vecchie = []
if os.path.exists(p):
    vecchie = [l for l in open(p).read().splitlines()
               if l.split("=", 1)[0] not in nuove]
with open(p, "w") as f:
    f.write("\\n".join(vecchie + ["%s=%s" % kv for kv in nuove.items()]) + "\\n")
os.chmod(p, 0o600)
print("scritte", len(nuove), "righe in", p)
'''
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(aiutante)
        locale = f.name
    try:
        c = subprocess.run(["scp", "-q", locale, SERVER + ":/tmp/aggiorna_env.py"],
                           stderr=subprocess.PIPE)
        if c.returncode != 0:
            print("Non riesco a raggiungere il server:")
            print(c.stderr.decode()[:300])
            return 1
        dati = "\n".join("%s=%s" % kv for kv in righe.items())
        p = subprocess.run(["ssh", SERVER, "python3 /tmp/aggiorna_env.py " + ENV_REMOTO],
                           input=dati.encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    finally:
        os.unlink(locale)
    if p.returncode != 0:
        print("Non sono riuscito a scrivere sul server:")
        print(p.stderr.decode()[:400])
        return 1
    print(" ", p.stdout.decode().strip())
    subprocess.run(["ssh", SERVER, "rm -f /tmp/aggiorna_env.py"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("\nRiavvio il portale perche' rilegga la configurazione...")
    subprocess.run(["ssh", SERVER, "sudo systemctl restart traduttore"],
                   stdout=subprocess.DEVNULL)
    print("Fatto. Il token sta solo sul server, con permessi 600.")
    print("Tetto applicativo: %s $ al mese. Superato quello, il portale smette" % tetto)
    print("di chiamare il provider e torna alle macchine gratuite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
