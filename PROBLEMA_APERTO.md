# Ultimate Translator — stato del progetto e problema aperto

Documento per una seconda opinione. Contiene misure reali, non stime: dove c'e'
un numero, l'ho misurato io oggi sulle macchine descritte.

---

## 1. Cos'e' il progetto

Portale privato personale, un solo utente. Fa tre cose:

1. **Traduce libri interi** (EPUB, PDF, DOCX, TXT) con AI, restituendo il file
   nello stesso formato e con la stessa impaginazione dell'originale.
2. **Li fa leggere** in un lettore web (EPUB via foliate-js, PDF via pdf.js),
   con posizione, annotazioni, ricerca.
3. **Li fa ascoltare** come audiolibri, con il testo evidenziato frase per frase
   mentre la voce avanza, controlli di velocita', e Media Session per l'uso in
   automobile.

Lettura e ascolto **condividono una sola posizione**: si legge un po' e si
ascolta un po', riprendendo sempre da dove si era rimasti.

## 2. Come e' fatto

| pezzo | tecnologia |
|---|---|
| Backend | Flask + gunicorn (1 worker, 8 thread) |
| Stato | SQLite in WAL: job, cache traduzioni, sessioni, posizioni, annotazioni, cache audio |
| Accesso | OTP via email, allowlist di **un solo indirizzo** |
| Traduzione | Claude / OpenAI, blocchi raggruppati in lotti e mandati in parallelo (misurato: 4,8 s/blocco → 0,44 s/blocco) |
| Formati | ebooklib+bs4, PyMuPDF, python-docx |
| Frontend | HTML/JS vanilla, nessuna build. foliate-js e pdf.js vendorizzati |
| Voce | **Kokoro** (82M parametri), voce italiana `if_sara` |

**Come funziona l'ascolto oggi**: si sintetizza **una frase alla volta**, su
richiesta, mai il libro intero. Ogni mp3 finisce in una cache su disco con chiave
`sha256(testo + voce + modello + istruzioni)`. Riascoltare non ricalcola nulla.
La regola "una frase alla volta" nacque quando la voce era a pagamento (OpenAI):
chi ascolta mezzo capitolo paga mezzo capitolo.

## 3. Dov'e' adesso

**Online e funzionante**: `https://<ip>.nip.io` su **Oracle Cloud Always Free**,
macchina `VM.Standard.E2.1.Micro` — **1 GB di RAM, 1/8 di OCPU**, 48 GB di disco,
regione Milano. Portale, libreria, lettura, posizioni e annotazioni funzionano
col Mac spento. HTTPS vero (Caddy + Let's Encrypt su nip.io), indirizzo stabile,
costo zero.

**Non funziona l'ascolto** su quella macchina. E' il problema di questo documento.

## 4. Le misure (tutte fatte oggi, tutte mie)

Motore vocale: **kokoro-onnx**, che e' Kokoro senza PyTorch.

| modello | file | picco di memoria | note |
|---|---|---|---|
| int8 quantizzato | 96 MB | **577 MB** | |
| pieno | 320 MB | **889 MB** | qualita' leggermente superiore |

Velocita', espressa come **fattore rispetto al tempo reale** (1,0x = genera un
secondo di audio in un secondo):

| macchina | modello | velocita' |
|---|---|---|
| Mac M-series, 1 processo, 2 thread | int8 | **1,2-1,5x** |
| Mac M-series, 1 processo, 2 thread | pieno | **2,0-3,3x** |
| Mac M-series, 8 processi x 1 thread | pieno | **2,3x** (il parallelismo **non** scala: +15% con 8 volte i processi) |
| Oracle E2.1.Micro (1/8 OCPU), sostenuta su 6 minuti | int8 | **0,21x** |

La misura sostenuta sul micro e' stabile: 0,21x dopo 5, 10 e 15 frasi, nessun
crollo dopo l'esaurimento del credito di burst.

**Il fatto che il parallelismo sul Mac non scali e' importante e non me lo
spiego**: 1 processo fa 2,0x, 8 processi fanno 2,3x, su una macchina con 8 core
veloci e 64 GB di RAM. Sospetto un lucchetto globale nella fonemizzazione
(espeak-ng) o dentro kokoro-onnx, ma non l'ho dimostrato. **Se questa cosa si
sbloccasse, il Mac da solo risolverebbe il problema.**

La libreria attuale, misurata sui file veri: **6 libri, 55,2 ore di audio**,
media 9,2 ore a libro.

## 5. Cosa vuole l'utente

- Ascoltare **dal cellulare, online**, con il Mac spento o lontano.
- **Non deve bloccarsi mai**, nemmeno in automobile o in galleria.
- **Tutti i libri che vuole**, senza tetti mensili.
- Testo evidenziato che avanza con la voce, controlli di velocita', posizione
  condivisa con la lettura.
- **Gratis**, o il piu' vicino possibile. Non vuole mettere carte di credito ne'
  scoprire addebiti a sorpresa.
- La voce deve restare **Kokoro Sara**: l'ha scelta lui confrontando i campioni.

### Il vincolo che rompe tutto

**Ascolta circa 5 ore al giorno, a velocita' 2x o piu'.**

L'audio viene generato a velocita' normale e accelerato in riproduzione, quindi:

```
5 ore di ascolto  x  2  =  10 ore di audio consumate al giorno
                           = circa un libro intero al giorno
                           = ~300 ore di audio nuovo al mese
```

## 6. Il conto che non torna

| macchina | produzione al giorno (24h) | copre le 10 ore? |
|---|---|---|
| Oracle E2.1.Micro (gratis, gia' online) | 0,21 × 24 = **5,0 ore** | **NO, meta'** |
| Due E2.1.Micro (Oracle ne regala 2) | **10,1 ore** | al pelo, margine zero |
| Oracle A1 ARM, 2 OCPU (gratis) | stimata 1,0-1,5x → **24-36 ore** | si', con margine |
| Mac (2,3x, ma solo da acceso) | 55 ore se acceso 24h | si', ma il Mac deve stare acceso |

## 7. Cosa e' gia' stato escluso, e perche'

- **Oracle A1 ARM gratuita** (2 OCPU / 12 GB, nessun tetto mensile): sarebbe la
  soluzione. **Non si riesce a creare**: "Out of host capacity" a Milano su tutte
  e tre le taglie provate (2/12, 2/6, 1/6), con richieste vere, non col report.
  Uno script ritenta in continuazione. Le risorse Always Free esistono **solo
  nella regione di casa**, e la regione di casa non si cambia se non distruggendo
  l'account: quindi non si puo' cacciare in altre regioni.
- **Hugging Face Spaces**: non piu' gratuito. Testuale dai loro documenti oggi:
  *"Gradio and Docker Spaces run on compute and require a paid plan to create"*.
  9 $/mese. L'unica alternativa gratis e' ZeroGPU con **5 minuti di GPU al
  giorno**, inutile per questo pattern.
- **Google Cloud Run**: quota gratuita 180.000 vCPU-secondi/mese. Un libro da 11
  ore ne consuma 80-134.000. Copre **1,5 libri al mese**, contro i ~30 richiesti.
  Oltre quota si paga (~0,20-0,25 $ per ora di audio → **~60-75 $/mese** a questo
  volume) e serve la carta.
- **Scaleway Containers**: si paga il container acceso, non la richiesta; con
  sessioni sparse l'80% della quota se ne va in attesa.
- **Render / Railway / Koyeb / Fly**: 256-512 MB di RAM, meno di quanto serve
  (577 MB) e si addormentano.
- **TTS a pagamento** (OpenAI): a 300 ore di audio al mese sono ~19 milioni di
  caratteri, cioe' **centinaia di dollari al mese**. Fuori discussione.
- **Voce del browser** (`speechSynthesis`): gratis e istantanea, ma su iPhone
  **l'audio muore quando si blocca lo schermo**, il che uccide l'uso in auto.

## 8. La mia proposta, e il suo punto debole

Un **operaio che sta sempre avanti al punto di ascolto**: gira di continuo sul
server, genera in avanti a partire dalla posizione corrente nel libro in corso,
e scrive nella stessa cache che il lettore consulta. L'utente preme play e trova
roba gia' pronta: nessuna attesa, nessun buco.

Regge se la produzione giornaliera supera il consumo giornaliero. **Con una sola
E2.1.Micro non regge**: 5 ore prodotte contro 10 consumate, e il ritardo si
accumula ogni giorno finche' l'ascolto si ferma.

Quindi la proposta ha senso solo abbinata a piu' potenza di calcolo:
la seconda macchina gratuita, la A1 ARM se si libera, o il Mac come
acceleratore quando e' acceso.

## 9. Le domande aperte

1. **Esiste un modo gratuito e sostenibile di produrre ~10 ore di audio Kokoro
   al giorno?** Se esiste, e' la risposta. Se non esiste, qual e' la soluzione
   piu' economica in assoluto?
2. **Perche' kokoro-onnx non scala con i processi paralleli** su un Mac con 8
   core veloci (1 proc = 2,0x, 8 proc = 2,3x)? Se e' un lucchetto in espeak-ng o
   in onnxruntime, aggirarlo moltiplicherebbe la resa di ogni macchina, incluse
   quelle gratuite.
3. **Esiste un modello TTS italiano di qualita' paragonabile a Kokoro ma
   sensibilmente piu' veloce su CPU?** (Piper e' molto piu' veloce: quanto e'
   peggiore la voce italiana rispetto a Kokoro Sara?)
4. **Problema tecnico separato**: il lettore divide il testo in frasi **in
   JavaScript** (foliate-js, granularita' `sentence`), mentre un generatore
   lato server le dividerebbe **in Python**. La chiave della cache e' l'hash del
   testo della frase: se le due divisioni non coincidono carattere per carattere,
   il generatore lavora per ore e il lettore non trova nulla. Qual e' il modo
   piu' robusto di garantire che le due divisioni coincidano?
