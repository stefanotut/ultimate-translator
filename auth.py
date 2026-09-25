"""
ULTIMATE TRANSLATOR - Autenticazione via OTP email

Il portale e' privato: solo gli indirizzi in ALLOWED_EMAILS possono entrare.
Flusso: chiedi codice -> arriva per email -> inserisci -> sessione da 30 giorni.

Se l'SMTP non e' configurato il codice finisce nel log del server invece che
nella posta: l'app resta usabile in locale, ma dal telefono serve l'SMTP.
"""

import os
import ssl
import hmac
import time
import smtplib
import logging
import secrets
import hashlib
from email.message import EmailMessage
from functools import wraps

from flask import request, jsonify, redirect, g

import store

logger = logging.getLogger(__name__)

COOKIE_NAME = "ut_session"
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 60
SESSION_TTL_SECONDS = 30 * 86400


def allowed_emails():
    raw = os.environ.get("ALLOWED_EMAILS", "stefanotuts@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _pepper():
    """Secret used to hash OTPs at rest. Generated once, kept in .env."""
    key = os.environ.get("SECRET_KEY")
    if not key:
        # Senza SECRET_KEY gli hash degli OTP non sarebbero legati a questa
        # installazione. Ne generiamo una e la persistiamo accanto al DB.
        path = os.path.join(os.path.dirname(store.DB_PATH), ".secret_key")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            with open(path) as fh:
                key = fh.read().strip()
        else:
            key = secrets.token_urlsafe(48)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(key)
        os.environ["SECRET_KEY"] = key
    return key.encode()


def _hash_code(email, code):
    return hmac.new(_pepper(), ("%s:%s" % (email, code)).encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Invio email
# ---------------------------------------------------------------------------

def smtp_configured():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER")
                and os.environ.get("SMTP_PASSWORD"))


def _send_email(to_addr, code):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = "Ultimate Translator - codice di accesso %s" % code
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(
        "Il tuo codice di accesso e':\n\n"
        "    %s\n\n"
        "Scade tra 10 minuti e vale per un solo accesso.\n"
        "Se non hai richiesto tu questo codice, ignora il messaggio.\n" % code
    )

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.send_message(msg)


def request_otp(email, client_ip=""):
    """
    Generate and deliver an OTP. Returns (ok, message, delivered_by_email).

    Deliberately returns the same shape for allowed and non-allowed addresses
    so the endpoint can't be used to enumerate who has access.
    """
    email = (email or "").strip().lower()
    generic = "Se l'indirizzo e' abilitato, il codice e' stato inviato."

    if "@" not in email or len(email) > 254:
        return False, "Indirizzo email non valido.", False

    if email not in allowed_emails():
        logger.warning("Richiesta OTP per indirizzo non abilitato: %s (ip=%s)",
                       email, client_ip)
        time.sleep(0.5)
        return True, generic, False

    conn = store.db()
    row = conn.execute("SELECT sent_at FROM otps WHERE email = ?", (email,)).fetchone()
    if row and time.time() - row["sent_at"] < OTP_RESEND_COOLDOWN:
        wait = int(OTP_RESEND_COOLDOWN - (time.time() - row["sent_at"]))
        return False, "Aspetta %d secondi prima di richiedere un altro codice." % wait, False

    code = "%06d" % secrets.randbelow(1_000_000)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO otps (email, code_hash, expires_at, attempts, sent_at) "
            "VALUES (?,?,?,0,?)",
            (email, _hash_code(email, code), time.time() + OTP_TTL_SECONDS, time.time()),
        )

    if smtp_configured():
        try:
            _send_email(email, code)
            logger.info("OTP inviato a %s", email)
            return True, generic, True
        except Exception as e:
            logger.error("Invio OTP fallito: %s", e)
            logger.warning("CODICE DI ACCESSO per %s: %s", email, code)
            return True, ("Invio email fallito (%s). Il codice e' nel log del server."
                          % str(e)[:80]), False

    logger.warning("SMTP non configurato. CODICE DI ACCESSO per %s: %s", email, code)
    return True, "SMTP non configurato: il codice e' nel log del server.", False


def verify_otp(email, code):
    """Check an OTP and, on success, return a fresh session token."""
    email = (email or "").strip().lower()
    code = (code or "").strip()

    # Difesa in profondita': i codici nascono solo per indirizzi autorizzati,
    # ma se la lista viene ristretta mentre un codice e' ancora valido quel
    # codice non deve piu' aprire niente.
    if email not in allowed_emails():
        logger.warning("Verifica OTP per indirizzo non abilitato: %s", email)
        return None, "Nessun codice richiesto per questo indirizzo."

    conn = store.db()
    row = conn.execute("SELECT * FROM otps WHERE email = ?", (email,)).fetchone()
    if not row:
        return None, "Nessun codice richiesto per questo indirizzo."
    if time.time() > row["expires_at"]:
        with conn:
            conn.execute("DELETE FROM otps WHERE email = ?", (email,))
        return None, "Codice scaduto. Richiedine uno nuovo."
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        with conn:
            conn.execute("DELETE FROM otps WHERE email = ?", (email,))
        return None, "Troppi tentativi. Richiedi un nuovo codice."

    if not hmac.compare_digest(row["code_hash"], _hash_code(email, code)):
        with conn:
            conn.execute("UPDATE otps SET attempts = attempts + 1 WHERE email = ?", (email,))
        left = OTP_MAX_ATTEMPTS - (row["attempts"] + 1)
        return None, "Codice errato. Tentativi rimasti: %d." % max(left, 0)

    token = secrets.token_urlsafe(32)
    with conn:
        conn.execute("DELETE FROM otps WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO sessions (token, email, created_at, expires_at) VALUES (?,?,?,?)",
            (token, email, time.time(), time.time() + SESSION_TTL_SECONDS),
        )
    logger.info("Accesso riuscito: %s", email)
    return token, "Accesso effettuato."


def session_email(token):
    if not token:
        return None
    row = store.db().execute(
        "SELECT email, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if not row or time.time() > row["expires_at"]:
        return None
    if row["email"] not in allowed_emails():
        return None  # accesso revocato cambiando ALLOWED_EMAILS
    return row["email"]


def destroy_session(token):
    if not token:
        return
    conn = store.db()
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def set_session_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=request.headers.get("X-Forwarded-Proto", request.scheme) == "https",
        path="/",
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def current_user():
    if not hasattr(g, "_ut_user"):
        g._ut_user = session_email(request.cookies.get(COOKIE_NAME))
    return g._ut_user


def login_required(view):
    """Guard for API endpoints: 401 JSON instead of a redirect."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Non autenticato", "login_required": True}), 401
        return view(*args, **kwargs)
    return wrapper


def page_login_required(view):
    """Guard for HTML pages: redirect to the login screen."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect("/login")
        return view(*args, **kwargs)
    return wrapper
