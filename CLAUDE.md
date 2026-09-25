# ULTIMATE TRANSLATOR

## Scopo
Portale privato che traduce libri interi (EPUB, PDF, DOCX, TXT/MD) con AI,
restituendo il file **nello stesso formato e con la stessa impaginazione**.
Lingua di arrivo di default: italiano.

## Stack
- **Backend**: Flask (`app.py`) — API REST, coda di job, worker in thread
- **Stato**: SQLite (`store.py`) in `data/translator.db`, modalita' WAL
- **Accesso**: OTP via email (`auth.py`), solo indirizzi in `ALLOWED_EMAILS`
- **Traduzione**: `translator.py` — `translate_blocks()` raggruppa i blocchi in
  lotti (`⟦N⟧`) e li manda in parallelo. Misurato su un libro vero:
  **4,8 s/blocco → 0,44 s/blocco, ~11x**. Retry con backoff, cache per blocco,
  recupero automatico dei tag di enfasi persi dal modello
- **Formati**: `epub_handler.py` (ebooklib+bs4), `pdf_handler.py` (PyMuPDF),
  `docx_handler.py` (python-docx), `txt_handler.py`
- **Frontend**: `templates/index.html` + `login.html` (vanilla, no build)
- **Repo**: https://github.com/stefanotut/ultimate-translator (PUBBLICO)

## Comandi
```bash
./venv/bin/python app.py     # dev  -> http://localhost:5001
gunicorn app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1 --threads 8
```

## Come regge i guasti
| Guasto | Comportamento |
|---|---|
| Crash / deploy / OOM a meta' traduzione | Il job torna in coda e **riprende**, riusando i blocchi gia' tradotti dalla cache: non si ripaga l'AI |
| Errore transitorio (rete, rate limit) | Fino a 3 tentativi, poi si ferma con errore |
| Piu' worker gunicorn | `claim_next_job()` e' atomico: un job va a un solo worker |
| Job apparentemente morto | Ripreso solo se l'heartbeat manca da `STALE_JOB_SECONDS` (default 120s) |
| Riavvio / deploy (SIGTERM) | I job del processo tornano in coda **subito**, senza attendere la scadenza |
| File che fa crashare il worker | Dopo `MAX_JOB_ATTEMPTS` (5) il job si ferma: niente crash-loop |
| Doppio click / script impazzito | Max `MAX_ACTIVE_JOBS` (5) job per utente: argine alla spesa API |
| Disco pieno | Upload rifiutato sotto `MIN_FREE_DISK_MB` (500) con 507 |
| File vecchi | Cancellati dopo `RETENTION_HOURS` (default 48) da un thread di pulizia |
| Brute force sul codice | 5 tentativi per OTP + 20 verifiche/10min **per email e per IP** |

## Variabili d'ambiente (`.env`)
`ALLOWED_EMAILS`, `SECRET_KEY`, `SMTP_HOST/PORT/USER/PASSWORD/FROM`,
`DEFAULT_TARGET_LANG`, `MAX_UPLOAD_MB` (40), `WORKER_THREADS` (2),
`RETENTION_HOURS` (48), `STALE_JOB_SECONDS` (120), `MAX_JOB_ATTEMPTS` (5),
`MAX_ACTIVE_JOBS` (5), `MIN_FREE_DISK_MB` (500), `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`.

## API
| Endpoint | Auth | Note |
|---|---|---|
| `/login`, `/api/auth/*` | no | richiesta OTP, verifica, logout |
| `/healthz` | no | per Render/uptime |
| `/api/models` | si | modelli + prezzi + formati supportati |
| `/api/analyze` | si | stima costo, **nessuna chiamata AI** |
| `/api/detect-language` | si | rileva la lingua sorgente |
| `/api/translate` | si | accoda un job, ritorna `task_id` |
| `/api/importa` | si | mette in libreria un libro GIA' pronto, senza tradurlo |
| `/api/libro/<id>` | si | DELETE: toglie il libro, i file, la copertina, note e posizione |
| `/api/status/<id>` | si | progresso + log (`?after_log=N`) |
| `/libreria` | si | pagina con tutti i libri, ricerca e filtri |
| `/api/jobs` | si | elenco job; `?q=` ricerca, `?status=active\|done\|error`, `?limit/offset` |
| `/api/cancel/<id>` | si | annulla un job in corso |
| `/api/download/<id>` | si | scarica il file tradotto |
| `/api/voci` | si | voci disponibili, scelta del libro, tetto giornaliero |
| `/api/voce` | si | sintetizza UNA frase, mp3; cache su disco per chiave di testo |
| `/api/voce/campione/<voce>` | si | frase di prova per scegliere la voce |
| `/api/frasi` | si | divide in frasi (regole italiane) con le posizioni, per il PDF |
| `/api/book/<id>/ascolto` | si | voce e velocita' del libro (la POSIZIONE sta in `/progress`) |

## Regole
- **Il portale e' una libreria, non solo un traduttore**: `/api/importa` accetta
  libri gia' pronti (finiscono in `outputs/`, mai in `uploads/`, perche' non sono
  file di passaggio) e `DELETE /api/libro/<id>` e' l'unica cancellazione voluta
  del sistema — porta via file, copertina, cache, posizione e annotazioni
- **MAI committare `.env`** né `data/` (gia' in `.gitignore`) — il repo e' pubblico
- I model ID vivono in `MODEL_PRICING` (`translator.py`): **verificarli contro le
  API vive prima di fidarsi**, i modelli vengono ritirati e l'app fa 404
- Anthropic Claude 5: **niente `temperature`** (400) e la risposta va letta
  cercando il blocco `type == "text"`, non `content[0]`
- `store.HEARTBEAT_INTERVAL` e' derivato da `STALE_JOB_SECONDS`: non fissarlo a mano
- Ogni job appartiene a un `owner` (email): status e download sono filtrati per owner
- **Il rate limit non puo' basarsi solo sull'IP**: dietro il tunnel Cloudflare (e
  dietro qualunque proxy) l'IP visto dal server cambia a ogni richiesta. Va sempre
  usato anche `subject=` (l'email presa di mira), che e' stabile
- **Ascolto (`tts.py`)**: si sintetizza UNA frase alla volta, su richiesta, mai il
  libro intero: chi ascolta mezzo capitolo paga mezzo capitolo. Tutto in cache in
  `data/audio/` con chiave `sha256(testo+voce+modello+istruzioni)`, **fuori** dalla
  pulizia a `RETENTION_HOURS` (rigenerare costa denaro), potata per spazio con
  `TTS_CACHE_MAX_MB`. Tetto giornaliero `TTS_MAX_CHARS_DAY`
- **La posizione e' UNA SOLA**, in `reading`, condivisa fra lettura e ascolto. Mentre
  la voce legge, il gestore di `relocate` NON deve salvare: sovrascriverebbe il CFI
  della frase con quello di inizio pagina
- **iPhone**: il primo `play()` deve partire dentro il gesto dell'utente
  (`sbloccaAudio()` con un file di silenzio), altrimenti l'audio muore appena si
  blocca lo schermo. E niente `previoustrack`/`nexttrack` in Media Session: in
  CarPlay il tasto del volante salterebbe un capitolo
- L'allowlist e' controllata in tre punti (`request_otp`, `verify_otp`,
  `session_email`): togliere un indirizzo da `ALLOWED_EMAILS` chiude l'accesso
  subito, anche a sessioni e codici gia' emessi
