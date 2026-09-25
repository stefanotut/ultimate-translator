"""
ULTIMATE TRANSLATOR - Persistent store (SQLite)

Sostituisce il dict `tasks = {}` che viveva in memoria di un singolo processo.
Tutto lo stato dei job, la cache delle traduzioni, gli OTP e le sessioni stanno
qui, cosi':
  - piu' worker gunicorn vedono gli stessi job (niente "Task non trovato");
  - un restart non perde le traduzioni in corso (vengono riprese);
  - la cache per blocco evita di ripagare l'AI per il lavoro gia' fatto.
"""

import os
import json
import time
import sqlite3
import hashlib
import logging
import threading

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "TRANSLATOR_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "translator.db"),
)

# Un job il cui heartbeat e' piu' vecchio di questo e' considerato morto
# (processo ucciso, deploy, sleep) e viene rimesso in coda.
STALE_JOB_SECONDS = max(int(os.environ.get("STALE_JOB_SECONDS", "120")), 20)

# Il worker deve battere molto piu' spesso di quanto ci metta a "scadere",
# altrimenti un job ancora vivo viene scambiato per morto e un secondo worker
# se lo prende. Derivandolo dalla soglia la configurazione non puo' sbagliare.
HEARTBEAT_INTERVAL = max(STALE_JOB_SECONDS // 4, 5)

_local = threading.local()
_init_lock = threading.Lock()
_initialised = False


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                TEXT PRIMARY KEY,
    owner             TEXT NOT NULL,
    status            TEXT NOT NULL,
    progress          REAL NOT NULL DEFAULT 0,
    status_text       TEXT,
    error             TEXT,
    original_filename TEXT,
    input_path        TEXT,
    output_path       TEXT,
    output_filename   TEXT,
    file_type         TEXT,
    source_lang       TEXT,
    target_lang       TEXT,
    provider          TEXT,
    model             TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    heartbeat_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_owner   ON jobs(owner, created_at DESC);

CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON job_logs(job_id, id);

CREATE TABLE IF NOT EXISTS tcache (
    job_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (job_id, key)
);

CREATE TABLE IF NOT EXISTS otps (
    email      TEXT PRIMARY KEY,
    code_hash  TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    sent_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    key          TEXT PRIMARY KEY,
    window_start REAL NOT NULL,
    count        INTEGER NOT NULL
);

-- A che punto sei di ogni libro. `location` e' una CFI per gli EPUB e un
-- numero di pagina per i PDF: il lettore lo rimanda com'e', il server non
-- lo interpreta.
CREATE TABLE IF NOT EXISTS reading (
    job_id     TEXT NOT NULL,
    owner      TEXT NOT NULL,
    location   TEXT,
    percent    REAL NOT NULL DEFAULT 0,
    label      TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (job_id, owner)
);

CREATE TABLE IF NOT EXISTS annotations (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    owner      TEXT NOT NULL,
    location   TEXT NOT NULL,     -- CFI (EPUB) o pagina (PDF)
    text       TEXT NOT NULL,     -- il brano evidenziato
    note       TEXT,              -- appunto libero
    color      TEXT NOT NULL DEFAULT 'giallo',
    chapter    TEXT,
    percent    REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_job ON annotations(job_id, owner, created_at);
CREATE INDEX IF NOT EXISTS idx_ann_owner ON annotations(owner, created_at DESC);

-- Ascolto: solo le preferenze. La POSIZIONE resta in `reading`, una sola per
-- libro, condivisa con la lettura: chi ascolta in macchina e poi riprende a
-- leggere sul divano deve ritrovarsi dove aveva lasciato, e viceversa.
CREATE TABLE IF NOT EXISTS ascolto (
    job_id     TEXT NOT NULL,
    owner      TEXT NOT NULL,
    voce       TEXT,
    velocita   REAL NOT NULL DEFAULT 1.0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (job_id, owner)
);

-- Cache dell'audio gia' sintetizzato. I file vivono in data/audio e NON sono
-- toccati dalla pulizia a 48 ore: rigenerarli costa denaro, non solo tempo.
-- La riga serve a sapere quanto spazio occupano e quali sono i piu' vecchi da
-- buttare per primi quando il disco si stringe.
CREATE TABLE IF NOT EXISTS audio_cache (
    chiave     TEXT PRIMARY KEY,
    percorso   TEXT NOT NULL,
    byte       INTEGER NOT NULL,
    caratteri  INTEGER NOT NULL,
    voce       TEXT,
    creato_at  REAL NOT NULL,
    usato_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audio_usato ON audio_cache(usato_at);

-- Caratteri sintetizzati giorno per giorno: e' il freno di emergenza sulla
-- spesa, e insieme il registro di quanto e' costato ascoltare.
CREATE TABLE IF NOT EXISTS tts_spesa (
    giorno    TEXT PRIMARY KEY,
    caratteri INTEGER NOT NULL DEFAULT 0
);

-- Spesa della voce a consumo, un mese per riga.
-- `prenotato` e' la somma impegnata PRIMA di chiamare il provider; `speso` e'
-- quella confermata dopo. Tenerli separati e' l'unico modo di avere un tetto
-- che regge davvero: se si contasse solo dopo la risposta, due richieste
-- lanciate insieme potrebbero sfondarlo entrambe credendo di starci dentro.
CREATE TABLE IF NOT EXISTS cloud_spesa (
    mese       TEXT PRIMARY KEY,
    prenotato  REAL NOT NULL DEFAULT 0,
    speso      REAL NOT NULL DEFAULT 0,
    caratteri  INTEGER NOT NULL DEFAULT 0,
    richieste  INTEGER NOT NULL DEFAULT 0,
    provider   TEXT
);

-- Audiolibri pre-generati, un capitolo per file.
--
-- Perche' una coda e non "subito": Kokoro produce audio ~5,6 volte piu' in
-- fretta di quanto si ascolti, quindi un libro da 12 ore costa ~2 ore di Mac.
-- Duecento libri sarebbero 17 giorni pieni: non si possono fare "subito", si
-- fanno uno alla volta, in sottofondo, dando la precedenza a cio' che serve.
CREATE TABLE IF NOT EXISTS audiolibri (
    job_id        TEXT NOT NULL,
    owner         TEXT NOT NULL,
    stato         TEXT NOT NULL DEFAULT 'in_coda',   -- in_coda|in_corso|pronto|errore
    priorita      INTEGER NOT NULL DEFAULT 0,        -- piu' alto = prima
    capitoli      INTEGER NOT NULL DEFAULT 0,
    capitoli_fatti INTEGER NOT NULL DEFAULT 0,
    byte          INTEGER NOT NULL DEFAULT 0,
    secondi       REAL NOT NULL DEFAULT 0,
    voce          TEXT,
    errore        TEXT,
    tentativi     INTEGER NOT NULL DEFAULT 0,
    creato_at     REAL NOT NULL,
    aggiornato_at REAL NOT NULL,
    ascoltato_at  REAL,
    PRIMARY KEY (job_id, owner)
);
CREATE INDEX IF NOT EXISTS idx_audio_coda ON audiolibri(stato, priorita DESC, creato_at);
"""

# Oltre questo numero di tentativi un job non viene piu' ripreso: se un file
# fa crashare il processo, senza questo limite verrebbe riclamato all'infinito
# e il server entrerebbe in crash-loop.
MAX_JOB_ATTEMPTS = int(os.environ.get("MAX_JOB_ATTEMPTS", "5"))


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL: letture concorrenti mentre un worker scrive il progresso.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def db():
    """Return this thread's connection, initialising the schema once."""
    global _initialised
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _connect()
    if not _initialised:
        with _init_lock:
            if not _initialised:
                conn.executescript(SCHEMA)
                conn.commit()
                _initialised = True
    return conn


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

JOB_FIELDS = (
    "id", "owner", "status", "progress", "status_text", "error",
    "original_filename", "input_path", "output_path", "output_filename",
    "file_type", "source_lang", "target_lang", "provider", "model",
    "attempts", "created_at", "updated_at", "heartbeat_at",
)


def create_job(**kw):
    now = time.time()
    kw.setdefault("status", "pending")
    kw.setdefault("progress", 0.0)
    kw.setdefault("status_text", "In coda...")
    kw["created_at"] = now
    kw["updated_at"] = now
    cols = [k for k in JOB_FIELDS if k in kw]
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO jobs (%s) VALUES (%s)"
            % (",".join(cols), ",".join("?" * len(cols))),
            [kw[c] for c in cols],
        )
    return kw["id"]


def get_job(job_id, owner=None):
    sql = "SELECT * FROM jobs WHERE id = ?"
    args = [job_id]
    if owner:
        sql += " AND owner = ?"
        args.append(owner)
    row = db().execute(sql, args).fetchone()
    return dict(row) if row else None


ACTIVE_STATUSES = ('pending', 'running')


def list_jobs(owner, limit=50, offset=0, search=None, status=None):
    """
    Jobs for one user, newest first, with optional text search and filter.

    `status` accepts a single status or the pseudo-values 'active'
    (in coda o in corso) and 'done' (completati).
    """
    sql = ["SELECT * FROM jobs WHERE owner = ?"]
    args = [owner]

    if search:
        sql.append("AND LOWER(original_filename) LIKE ?")
        args.append("%%%s%%" % search.strip().lower())

    if status == 'active':
        sql.append("AND status IN ('pending','running')")
    elif status == 'done':
        sql.append("AND status = 'completed'")
    elif status in ('completed', 'error', 'canceled', 'pending', 'running'):
        sql.append("AND status = ?")
        args.append(status)

    sql.append("ORDER BY created_at DESC LIMIT ? OFFSET ?")
    args += [limit, offset]

    rows = db().execute(" ".join(sql), args).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Lettura: segnalibro e annotazioni
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Audiolibri
# ---------------------------------------------------------------------------

MAX_TENTATIVI_AUDIO = int(os.environ.get("AUDIOLIBRO_MAX_TENTATIVI", "3"))


def audiolibro_accoda(job_id, owner, priorita=0):
    """Mette un libro in coda per l'audio. Se c'e' gia', alza solo la priorita'."""
    conn = db()
    now = time.time()
    with conn:
        conn.execute(
            "INSERT INTO audiolibri (job_id, owner, stato, priorita, creato_at, aggiornato_at) "
            "VALUES (?,?,'in_coda',?,?,?) "
            "ON CONFLICT(job_id, owner) DO UPDATE SET "
            "  priorita = MAX(priorita, excluded.priorita), "
            # un libro gia' pronto non si rifa'; uno in errore si riprova
            "  stato = CASE WHEN stato = 'errore' THEN 'in_coda' ELSE stato END, "
            "  tentativi = CASE WHEN stato = 'errore' THEN 0 ELSE tentativi END, "
            "  aggiornato_at = excluded.aggiornato_at",
            (job_id, owner, int(priorita), now, now))


def audiolibro_prossimo():
    """Prende UN libro dalla coda, in modo atomico. Priorita', poi ordine d'arrivo."""
    conn = db()
    now = time.time()
    with conn:
        riga = conn.execute(
            "SELECT job_id, owner FROM audiolibri "
            " WHERE stato = 'in_coda' AND tentativi < ? "
            " ORDER BY priorita DESC, creato_at ASC LIMIT 1", (MAX_TENTATIVI_AUDIO,)
        ).fetchone()
        if not riga:
            return None
        cur = conn.execute(
            "UPDATE audiolibri SET stato='in_corso', tentativi = tentativi + 1, "
            "  aggiornato_at = ? WHERE job_id = ? AND owner = ? AND stato = 'in_coda'",
            (now, riga["job_id"], riga["owner"]))
        if cur.rowcount == 0:
            return None
    return {"job_id": riga["job_id"], "owner": riga["owner"]}


def audiolibro_aggiorna(job_id, owner, **kw):
    kw["aggiornato_at"] = time.time()
    campi = ", ".join("%s = ?" % k for k in kw)
    with db() as conn:
        conn.execute("UPDATE audiolibri SET %s WHERE job_id = ? AND owner = ?" % campi,
                     list(kw.values()) + [job_id, owner])


def audiolibro_stato(job_id, owner):
    r = db().execute("SELECT * FROM audiolibri WHERE job_id = ? AND owner = ?",
                     (job_id, owner)).fetchone()
    return dict(r) if r else None


def audiolibro_mappa(owner):
    """Stato dell'audio di tutti i libri, per la libreria."""
    return {r["job_id"]: dict(r) for r in
            db().execute("SELECT * FROM audiolibri WHERE owner = ?", (owner,))}


def audiolibro_ascoltato(job_id, owner):
    """Segna che si sta ascoltando: serve a non buttarlo per primo quando lo
    spazio finisce."""
    with db() as conn:
        conn.execute("UPDATE audiolibri SET ascoltato_at = ? WHERE job_id = ? AND owner = ?",
                     (time.time(), job_id, owner))


def audiolibro_dimentica(job_id, owner):
    with db() as conn:
        conn.execute("DELETE FROM audiolibri WHERE job_id = ? AND owner = ?", (job_id, owner))


def audiolibri_da_liberare(byte_massimi):
    """Quali audiolibri togliere per rientrare nello spazio concesso.

    Si comincia da quelli che non si ascoltano da piu' tempo: rigenerarli non
    costa denaro (la voce e' locale), costa solo tempo di Mac, ed e' un prezzo
    accettabile per non riempire il disco.
    """
    righe = db().execute(
        "SELECT job_id, owner, byte, COALESCE(ascoltato_at, creato_at) AS quando "
        "  FROM audiolibri WHERE stato = 'pronto' ORDER BY quando ASC").fetchall()
    totale = sum(r["byte"] for r in righe)
    if totale <= byte_massimi:
        return []
    scelti, liberati = [], 0
    for r in righe:
        scelti.append(dict(r))
        liberati += r["byte"]
        if totale - liberati <= byte_massimi:
            break
    return scelti


def audiolibri_spazio():
    r = db().execute(
        "SELECT COUNT(*) n, COALESCE(SUM(byte),0) byte, COALESCE(SUM(secondi),0) sec "
        "  FROM audiolibri WHERE stato = 'pronto'").fetchone()
    coda = db().execute(
        "SELECT COUNT(*) n FROM audiolibri WHERE stato IN ('in_coda','in_corso')").fetchone()
    return {"pronti": r["n"], "byte": r["byte"], "secondi": r["sec"], "in_coda": coda["n"]}


def delete_job(job_id, owner):
    """Cancella un libro e tutto cio' che gli sta attorno.

    E' l'unica cancellazione voluta del sistema, quindi porta via davvero tutto:
    la riga del job, i log, la cache delle traduzioni, la posizione di lettura,
    le annotazioni e le preferenze d'ascolto. Lasciare in giro annotazioni
    orfane significherebbe ritrovarsele nella pagina degli appunti senza piu'
    un libro a cui appartengono.
    """
    conn = db()
    with conn:
        conn.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM tcache WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM reading WHERE job_id = ? AND owner = ?", (job_id, owner))
        conn.execute("DELETE FROM annotations WHERE job_id = ? AND owner = ?", (job_id, owner))
        conn.execute("DELETE FROM ascolto WHERE job_id = ? AND owner = ?", (job_id, owner))
        conn.execute("DELETE FROM audiolibri WHERE job_id = ? AND owner = ?", (job_id, owner))
        cur = conn.execute("DELETE FROM jobs WHERE id = ? AND owner = ?", (job_id, owner))
    return cur.rowcount


def get_reading(job_id, owner):
    row = db().execute(
        "SELECT location, percent, label, updated_at FROM reading "
        "WHERE job_id = ? AND owner = ?", (job_id, owner)).fetchone()
    return dict(row) if row else {"location": None, "percent": 0,
                                  "label": None, "updated_at": None}


def save_reading(job_id, owner, location, percent, label=None):
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO reading (job_id, owner, location, percent, label, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(job_id, owner) DO UPDATE SET "
            "  location=excluded.location, percent=excluded.percent, "
            "  label=excluded.label, updated_at=excluded.updated_at",
            (job_id, owner, location, float(percent or 0), label, time.time()))


def reading_map(owner):
    """Avanzamento di tutti i libri in un colpo solo, per la libreria."""
    rows = db().execute(
        "SELECT job_id, percent, location, label, updated_at FROM reading "
        "WHERE owner = ?", (owner,)).fetchall()
    return {r["job_id"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# Ascolto
# ---------------------------------------------------------------------------

def get_ascolto(job_id, owner):
    row = db().execute(
        "SELECT voce, velocita FROM ascolto WHERE job_id = ? AND owner = ?",
        (job_id, owner)).fetchone()
    return dict(row) if row else {"voce": None, "velocita": 1.0}


def save_ascolto(job_id, owner, voce=None, velocita=None):
    conn = db()
    corrente = get_ascolto(job_id, owner)
    voce = voce if voce is not None else corrente["voce"]
    velocita = float(velocita if velocita is not None else corrente["velocita"] or 1.0)
    # La velocita' e' un moltiplicatore dell'elemento audio: fuori da questo
    # intervallo il parlato diventa incomprensibile invece che veloce.
    velocita = min(max(velocita, 0.5), 3.0)
    with conn:
        conn.execute(
            "INSERT INTO ascolto (job_id, owner, voce, velocita, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(job_id, owner) DO UPDATE SET "
            "  voce=excluded.voce, velocita=excluded.velocita, "
            "  updated_at=excluded.updated_at",
            (job_id, owner, voce, velocita, time.time()))
    return {"voce": voce, "velocita": velocita}


# ---------------------------------------------------------------------------
# Cache dell'audio e tetto di spesa
# ---------------------------------------------------------------------------

def audio_registrato(chiave, percorso, byte, caratteri, voce):
    conn = db()
    now = time.time()
    with conn:
        conn.execute(
            "INSERT INTO audio_cache (chiave, percorso, byte, caratteri, voce, "
            "                         creato_at, usato_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(chiave) DO UPDATE SET usato_at=excluded.usato_at",
            (chiave, percorso, int(byte), int(caratteri), voce, now, now))


def audio_usato(chiave):
    conn = db()
    with conn:
        conn.execute("UPDATE audio_cache SET usato_at = ? WHERE chiave = ?",
                     (time.time(), chiave))


def audio_statistiche():
    r = db().execute(
        "SELECT COUNT(*) n, COALESCE(SUM(byte),0) byte, "
        "       COALESCE(SUM(caratteri),0) caratteri FROM audio_cache").fetchone()
    return {"pezzi": r["n"], "byte": r["byte"], "caratteri": r["caratteri"]}


def _oggi():
    return time.strftime("%Y-%m-%d", time.gmtime())


def tts_caratteri_oggi():
    r = db().execute("SELECT caratteri FROM tts_spesa WHERE giorno = ?",
                     (_oggi(),)).fetchone()
    return r["caratteri"] if r else 0


def tts_entro_il_tetto(caratteri, tetto):
    """Vero se sintetizzare `caratteri` resta sotto il tetto giornaliero.

    Non addebita: serve a decidere PRIMA di spendere. L'addebito avviene solo
    se la sintesi riesce davvero, cosi' una chiamata fallita non consuma
    budget che non e' stato speso.
    """
    return tts_caratteri_oggi() + int(caratteri) <= int(tetto)


def tts_addebita(caratteri):
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO tts_spesa (giorno, caratteri) VALUES (?,?) "
            "ON CONFLICT(giorno) DO UPDATE SET caratteri = caratteri + excluded.caratteri",
            (_oggi(), int(caratteri)))


def audio_da_buttare(byte_massimi):
    """I pezzi audio meno usati di recente, finche' si rientra nel limite.

    Restituisce le righe da eliminare; non tocca il disco (lo fa chi chiama,
    cosi' un errore sul filesystem non lascia il database che mente).
    """
    stat = audio_statistiche()
    if stat["byte"] <= byte_massimi:
        return []
    da_liberare = stat["byte"] - byte_massimi
    righe = db().execute(
        "SELECT chiave, percorso, byte FROM audio_cache ORDER BY usato_at ASC").fetchall()
    scelte, somma = [], 0
    for r in righe:
        scelte.append(dict(r))
        somma += r["byte"]
        if somma >= da_liberare:
            break
    return scelte


def audio_dimentica(chiavi):
    if not chiavi:
        return 0
    conn = db()
    with conn:
        conn.executemany("DELETE FROM audio_cache WHERE chiave = ?",
                         [(c,) for c in chiavi])
    return len(chiavi)


def add_annotation(**kw):
    now = time.time()
    kw.setdefault("created_at", now)
    kw["updated_at"] = now
    campi = ("id", "job_id", "owner", "location", "text", "note", "color",
             "chapter", "percent", "created_at", "updated_at")
    cols = [c for c in campi if c in kw]
    conn = db()
    with conn:
        conn.execute("INSERT INTO annotations (%s) VALUES (%s)"
                     % (",".join(cols), ",".join("?" * len(cols))),
                     [kw[c] for c in cols])
    return kw["id"]


def update_annotation(ann_id, owner, **kw):
    kw = {k: v for k, v in kw.items() if k in ("note", "color", "text")}
    if not kw:
        return False
    kw["updated_at"] = time.time()
    sets = ",".join("%s = ?" % k for k in kw)
    conn = db()
    with conn:
        cur = conn.execute("UPDATE annotations SET %s WHERE id = ? AND owner = ?" % sets,
                           list(kw.values()) + [ann_id, owner])
    return cur.rowcount > 0


def delete_annotation(ann_id, owner):
    conn = db()
    with conn:
        cur = conn.execute("DELETE FROM annotations WHERE id = ? AND owner = ?",
                           (ann_id, owner))
    return cur.rowcount > 0


def list_annotations(owner, job_id=None, search=None, limit=500):
    sql = ["SELECT * FROM annotations WHERE owner = ?"]
    args = [owner]
    if job_id:
        sql.append("AND job_id = ?")
        args.append(job_id)
    if search:
        sql.append("AND (LOWER(text) LIKE ? OR LOWER(note) LIKE ?)")
        like = "%%%s%%" % search.strip().lower()
        args += [like, like]
    sql.append("ORDER BY created_at DESC LIMIT ?")
    args.append(limit)
    return [dict(r) for r in db().execute(" ".join(sql), args).fetchall()]


def annotation_counts(owner):
    rows = db().execute(
        "SELECT job_id, COUNT(*) AS n FROM annotations WHERE owner = ? GROUP BY job_id",
        (owner,)).fetchall()
    return {r["job_id"]: r["n"] for r in rows}


def job_counts(owner):
    rows = db().execute(
        "SELECT status, COUNT(*) AS n FROM jobs WHERE owner = ? GROUP BY status",
        (owner,),
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    counts["active"] = counts.get("pending", 0) + counts.get("running", 0)
    counts["total"] = sum(n for s, n in counts.items() if s != "active")
    return counts


def update_job(job_id, **kw):
    if not kw:
        return
    kw["updated_at"] = time.time()
    sets = ",".join("%s = ?" % k for k in kw)
    conn = db()
    with conn:
        conn.execute(
            "UPDATE jobs SET %s WHERE id = ?" % sets,
            list(kw.values()) + [job_id],
        )


def heartbeat(job_id):
    conn = db()
    with conn:
        conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
                     (time.time(), job_id))


def add_log(job_id, message, level="info"):
    conn = db()
    with conn:
        conn.execute(
            "INSERT INTO job_logs (job_id, ts, level, message) VALUES (?,?,?,?)",
            (job_id, time.time(), level, message[:1000]),
        )
    logger.info("[%s] %s", job_id[:8], message)


def get_logs(job_id, after_id=0, limit=400):
    rows = db().execute(
        "SELECT id, ts, level, message FROM job_logs "
        "WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
        (job_id, after_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def claim_next_job():
    """
    Atomically take one runnable job. Returns the job dict or None.

    Runnable means: pending, or running but whose worker stopped sending a
    heartbeat (crash, restart, deploy) — that one gets resumed, not restarted
    from scratch, because the per-block cache survives in `tcache`.
    """
    conn = db()
    cutoff = time.time() - STALE_JOB_SECONDS

    # Job avvelenati: se un file fa morire il worker ogni volta, dopo N
    # tentativi si smette di riprenderlo invece di andare in crash-loop.
    with conn:
        conn.execute(
            "UPDATE jobs SET status='error', "
            "       status_text='Interrotto troppe volte', "
            "       error=COALESCE(error, 'Il job ha superato %d tentativi: "
            "il file potrebbe far crashare il worker.') "
            " WHERE status IN ('pending','running') AND attempts >= %d"
            % (MAX_JOB_ATTEMPTS, MAX_JOB_ATTEMPTS)
        )

    with conn:
        row = conn.execute(
            "SELECT id FROM jobs "
            " WHERE attempts < ? "
            "   AND (status = 'pending' "
            "        OR (status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at < ?))) "
            " ORDER BY created_at LIMIT 1",
            (MAX_JOB_ATTEMPTS, cutoff),
        ).fetchone()
        if not row:
            return None
        job_id = row["id"]
        cur = conn.execute(
            "UPDATE jobs SET status='running', heartbeat_at=?, updated_at=?, "
            "       attempts = attempts + 1 "
            " WHERE id = ? AND (status='pending' OR heartbeat_at IS NULL OR heartbeat_at < ?)",
            (time.time(), time.time(), job_id, cutoff),
        )
        if cur.rowcount == 0:
            return None  # un altro worker l'ha preso per primo
    return get_job(job_id)


def count_active_jobs(owner):
    """Queued or running jobs for one user — usato per limitare la spesa."""
    row = db().execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE owner = ? AND status IN ('pending','running')",
        (owner,),
    ).fetchone()
    return row["n"] if row else 0


def rate_limit_ok(key, limit, window_seconds):
    """
    Fixed-window rate limit condiviso fra i processi.

    Ritorna True se l'azione e' consentita. Sta nel DB e non in memoria perche'
    con piu' worker un contatore per-processo non limiterebbe niente.
    """
    now = time.time()
    conn = db()
    with conn:
        row = conn.execute(
            "SELECT window_start, count FROM rate_limits WHERE key = ?", (key,)
        ).fetchone()
        if row is None or now - row["window_start"] >= window_seconds:
            conn.execute(
                "INSERT OR REPLACE INTO rate_limits (key, window_start, count) "
                "VALUES (?,?,1)", (key, now))
            return True
        if row["count"] >= limit:
            return False
        conn.execute("UPDATE rate_limits SET count = count + 1 WHERE key = ?", (key,))
        return True


def requeue_stale_jobs():
    """On boot, hand orphaned jobs back to the queue."""
    conn = db()
    cutoff = time.time() - STALE_JOB_SECONDS
    with conn:
        cur = conn.execute(
            "UPDATE jobs SET status='pending', status_text='Ripresa dopo riavvio...' "
            " WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            (cutoff,),
        )
    if cur.rowcount:
        logger.info("Rimessi in coda %d job interrotti", cur.rowcount)
    return cur.rowcount


# ---------------------------------------------------------------------------
# Translation cache — resume senza ripagare l'AI
# ---------------------------------------------------------------------------

def cache_key(text, model, source_lang, target_lang, context=""):
    h = hashlib.sha256()
    for part in (text, model, source_lang, target_lang, context):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def cache_get(job_id, key):
    row = db().execute(
        "SELECT value FROM tcache WHERE job_id = ? AND key = ?", (job_id, key)
    ).fetchone()
    return row["value"] if row else None


def cache_put(job_id, key, value):
    conn = db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO tcache (job_id, key, value, created_at) "
            "VALUES (?,?,?,?)",
            (job_id, key, value, time.time()),
        )


def cache_size(job_id):
    row = db().execute(
        "SELECT COUNT(*) AS n FROM tcache WHERE job_id = ?", (job_id,)
    ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Retention — bug: uploads/ e outputs/ crescevano all'infinito
# ---------------------------------------------------------------------------

def purge_expired(retention_hours=48, job_retention_days=3650):
    """Rimuove i file CARICATI dopo la scadenza. Il libro tradotto NON si tocca.

    Perche' e' cambiata.
    --------------------
    La prima versione cancellava dopo 48 ore sia l'originale caricato sia il
    LIBRO TRADOTTO, e con essi la cache delle traduzioni. E' successo davvero:
    cinque libri su sei sono spariti dalla libreria mentre l'utente li stava
    leggendo, e per riaverli bisognava ripagare la traduzione.

    L'originale caricato e' un file di passaggio: si puo' buttare. Il libro
    tradotto e' il prodotto — l'unica cosa per cui si e' pagato — e resta finche'
    non lo si cancella apposta. La cache resta anche lei: e' cio' che rende
    quasi gratuita una ri-traduzione.
    """
    now = time.time()
    file_cutoff = now - retention_hours * 3600
    job_cutoff = now - job_retention_days * 86400
    conn = db()
    removed_files = 0

    rows = conn.execute(
        "SELECT id, input_path FROM jobs "
        " WHERE updated_at < ? AND status IN ('completed','error','canceled') "
        "   AND input_path IS NOT NULL",
        (file_cutoff,),
    ).fetchall()

    for row in rows:
        path = row["input_path"]
        if path and os.path.exists(path):
            try:
                os.remove(path)
                removed_files += 1
            except OSError as e:
                logger.warning("Impossibile rimuovere %s: %s", path, e)
        with conn:
            conn.execute("UPDATE jobs SET input_path=NULL WHERE id = ?", (row["id"],))

    # Solo quando un job viene davvero eliminato si porta via il suo tradotto:
    # altrimenti resterebbe un file senza padre che nessuno cancellera' mai.
    vecchi = conn.execute(
        "SELECT id, output_path FROM jobs WHERE created_at < ?", (job_cutoff,)).fetchall()
    for row in vecchi:
        if row["output_path"] and os.path.exists(row["output_path"]):
            try:
                os.remove(row["output_path"])
                removed_files += 1
            except OSError:
                pass

    with conn:
        conn.execute("DELETE FROM job_logs WHERE job_id IN "
                     "(SELECT id FROM jobs WHERE created_at < ?)", (job_cutoff,))
        conn.execute("DELETE FROM tcache WHERE job_id IN "
                     "(SELECT id FROM jobs WHERE created_at < ?)", (job_cutoff,))
        conn.execute("DELETE FROM jobs WHERE created_at < ?", (job_cutoff,))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM otps WHERE expires_at < ?", (now - 3600,))
        conn.execute("DELETE FROM rate_limits WHERE window_start < ?", (now - 86400,))

    return removed_files


def purge_orphan_files(dirs, retention_hours=48):
    """Rimuove i file che nessun job rivendica (resti di un crash).

    Due protezioni, e non sono teoriche: e' successo davvero.
    --------------------------------------------------------
    1. `realpath`, non `abspath`. Se il database dice `/app/outputs/x.epub` e la
       cartella viene letta come `/dati/outputs/x.epub` — cioe' lo stesso file
       visto attraverso un collegamento — `abspath` li considera diversi e il
       libro finisce fra gli orfani. Basta un collegamento simbolico nel deploy
       per cancellare l'intera libreria.

    2. Se in una cartella NESSUN file risulta rivendicato, non si cancella
       niente. Un file orfano e' plausibile; una cartella intera di orfani no:
       vuol dire che il database non corrisponde al disco (percorsi vecchi dopo
       una migrazione, database ripristinato, disco montato altrove). In quel
       caso l'ipotesi giusta e' "mi sto sbagliando io", non "sono tutti da
       buttare". E' esattamente cosi' che e' sparito un libro dopo il
       trasferimento sul server: il database conteneva ancora i percorsi del Mac.
    """
    conn = db()
    known = set()
    for row in conn.execute(
        "SELECT input_path, output_path FROM jobs "
        "WHERE input_path IS NOT NULL OR output_path IS NOT NULL"
    ):
        for p in (row["input_path"], row["output_path"]):
            if p:
                known.add(os.path.realpath(p))

    cutoff = time.time() - retention_hours * 3600
    removed = 0
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        presenti = [os.path.realpath(os.path.join(directory, n))
                    for n in os.listdir(directory)]
        presenti = [p for p in presenti if os.path.isfile(p)]
        if presenti and not any(p in known for p in presenti):
            logger.warning(
                "Pulizia sospesa in %s: nessuno dei %d file risulta di un libro. "
                "Probabilmente il database non corrisponde al disco: non cancello nulla.",
                directory, len(presenti))
            continue
        for path in presenti:
            if path in known:
                continue
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    return removed


# ---------------------------------------------------------------------------
# Spesa della voce a consumo
# ---------------------------------------------------------------------------
# Il tetto si difende PRENOTANDO prima di chiamare, non contando dopo. Se si
# contasse dopo la risposta, due richieste partite insieme potrebbero sfondarlo
# entrambe credendo di starci dentro: e' il classico errore che si scopre
# leggendo la fattura.

def cloud_prenota(mese, importo, tetto):
    """Impegna `importo` se ci sta nel tetto. Ritorna True solo se ha impegnato."""
    conn = db()
    with conn:
        conn.execute("INSERT OR IGNORE INTO cloud_spesa (mese) VALUES (?)", (mese,))
        cur = conn.execute(
            "UPDATE cloud_spesa SET prenotato = prenotato + ? "
            "WHERE mese = ? AND prenotato + ? <= ?",
            (importo, mese, importo, tetto))
        return cur.rowcount > 0


def cloud_rilascia(mese, importo):
    """Restituisce una prenotazione: la chiamata non e' andata a buon fine.

    Senza questo, una rete ballerina esaurirebbe il budget senza produrre un
    solo secondo di audio.
    """
    conn = db()
    with conn:
        conn.execute(
            "UPDATE cloud_spesa SET prenotato = MAX(0, prenotato - ?) WHERE mese = ?",
            (importo, mese))


def cloud_registra(mese, caratteri, importo, provider=None):
    conn = db()
    with conn:
        conn.execute(
            "UPDATE cloud_spesa SET speso = speso + ?, caratteri = caratteri + ?, "
            "richieste = richieste + 1, provider = COALESCE(?, provider) WHERE mese = ?",
            (importo, caratteri, provider, mese))


def cloud_correggi(mese, stimato, reale):
    """Il provider ha detto quanto e' costato davvero: si aggiusta la prenotazione."""
    conn = db()
    with conn:
        conn.execute(
            "UPDATE cloud_spesa SET prenotato = MAX(0, prenotato - ? + ?) WHERE mese = ?",
            (stimato, reale, mese))


def cloud_speso(mese):
    """Quanto e' impegnato in questo mese: il MAGGIORE fra prenotato e speso.

    Si prende il maggiore apposta: finche' una chiamata e' in volo il denaro e'
    gia' fuori, anche se la conferma non e' ancora arrivata.
    """
    r = db().execute(
        "SELECT prenotato, speso FROM cloud_spesa WHERE mese = ?", (mese,)).fetchone()
    if not r:
        return 0.0
    return max(float(r["prenotato"] or 0), float(r["speso"] or 0))


def cloud_riepilogo(mese):
    r = db().execute("SELECT * FROM cloud_spesa WHERE mese = ?", (mese,)).fetchone()
    return dict(r) if r else {"mese": mese, "prenotato": 0, "speso": 0,
                              "caratteri": 0, "richieste": 0, "provider": None}
