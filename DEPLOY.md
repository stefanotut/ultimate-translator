# Mettere il portale online, sempre acceso, gratis

Finché il portale gira sul Mac, col Mac spento non funziona nulla: né leggere,
né ascoltare. Questo documento porta tutto su una macchina che non si spegne.

## Dove, e perché

**Oracle Cloud Always Free**, macchina `VM.Standard.A1.Flex` (ARM), Ubuntu 24.04.

Non è una prova a tempo: è gratuita per sempre. Regge tutto perché ha i numeri
che servono, misurati sul Mac su questo stesso software:

| Cosa serve | Misurato | Oracle Always Free |
|---|---|---|
| RAM per Kokoro a regime | 1,9–2,8 GB | 6 GB (su 12 disponibili) |
| RAM di picco per un PDF da 216 pagine | 677 MB | idem |
| Disco per software + modello | 1,4 GB | 200 GB persistenti |
| Traffico per un'ora di audiolibro | 13,8 MB | 10 TB al mese |
| Deve restare acceso mentre guidi | — | è una VM vera, non dorme |

Gli altri sono stati scartati per un motivo solo e concreto: **Render, Railway,
Koyeb e Fly danno 256–512 MB di RAM**, cioè meno della metà di quanto serve solo
per caricare il modello vocale — e per giunta si addormentano. Hugging Face
avrebbe la RAM ma il disco si azzera a ogni riavvio: perderesti libreria,
posizioni di lettura e annotazioni.

**Piano B se Oracle non dà la macchina** (capita: "Out of host capacity"):
Hetzner CAX11, stessa architettura ARM, ~4-6 € al mese. Si installa uguale.

## Quello che devi fare tu

1. Account su `oracle.com/cloud/free`. **Serve una carta di credito vera** (non
   prepagata): non viene addebitata, serve solo a verificare l'identità.
2. **Scegli la regione con attenzione: è irreversibile.** Le regioni americane
   sono spesso esaurite per le macchine ARM; Francoforte e Zurigo sono più
   libere. Se la creazione fallisce, riprova in un'altra regione.
3. Creare la macchina: `VM.Standard.A1.Flex`, **2 OCPU / 6 GB** (non 12: vedi
   sotto), Ubuntu 24.04 ARM, disco 100 GB.
4. Dammi l'accesso SSH, oppure esegui tu i comandi qui sotto.

**Perché 6 GB e non 12**: Oracle può reclamare le macchine ferme, e per le ARM
guarda anche la memoria — una macchina con 12 GB quasi vuoti rientra nei
criteri. Con 6 GB il portale ne usa una quota sana e non sembra abbandonato.

## Quello che faccio io

```bash
# sulla VM
sudo apt update && sudo apt install -y docker.io git
git clone <repo>  &&  cd "Traduzione file epub e pdf"
# .env NON è nel repo: va ricreato qui
docker build -t traduttore .
docker run -d --restart=always -p 8000:8000 \
  -v /dati/data:/app/data -v /dati/outputs:/app/outputs -v /dati/uploads:/app/uploads \
  --env-file .env traduttore
```

Poi si porta dal Mac quello che c'è già — libreria, database, posizioni di
lettura, annotazioni, cache audio — con un `rsync` di `data/` e `outputs/`.

Infine Cloudflare Tunnel sulla VM: niente porte aperte, HTTPS gratis, e si
riusa lo stesso strumento che già gira sul Mac.

## Due trappole già disinnescate

**`TTS_ENGINE=locale`, non `auto`.** Con `auto`, se la voce locale si rompe il
sistema passa a OpenAI **senza dirlo** e comincia a fatturare. Con `locale`
l'ascolto si ferma con un errore visibile: meglio un guasto di una bolletta.

**`OMP_NUM_THREADS=2`.** Torch, lasciato libero, apre un thread per core e
arriva a 3,5 GB di picco; con due thread sta sotto 1,6 GB e non va più lento.
Su una macchina da 6 GB è la differenza fra funzionare e farsi uccidere dal
sistema.

## Quanto costa

Zero al mese. Il traffico incluso (10 TB) basta per circa 72.000 ore di
ascolto. I tetti di spazio sono già nel codice (`AUDIOLIBRI_MAX_GB=40`,
`TTS_CACHE_MAX_MB=4000`): il massimo che il portale può occupare è 44 GB sui
200 disponibili.

L'unica spesa che resta è la traduzione dei libri nuovi, sulle tue chiavi, come
adesso.

## Non spegnere subito il Mac

Il tunnel sul Mac va lasciato vivo per qualche settimana: se sul server qualcosa
non va, il portale continua a rispondere da qui mentre si sistema.
