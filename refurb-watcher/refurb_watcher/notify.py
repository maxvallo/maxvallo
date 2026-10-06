"""Benachrichtigungen. Aktiv ist jeder Kanal, dessen Umgebungsvariablen gesetzt sind.

ntfy (App für iOS/Android, kein Account nötig):
    NTFY_TOPIC=mein-geheimes-topic   [NTFY_SERVER=https://ntfy.sh]   [NTFY_TOKEN=...]
Telegram (empfohlen, Einrichtung siehe README):
    TELEGRAM_BOT_TOKEN=...  TELEGRAM_CHAT_ID=...
E-Mail (SMTP, z. B. Gmail mit App-Passwort):
    SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=... SMTP_PASSWORD=... MAIL_TO=...
"""

import html
import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


def _ntfy(title: str, body: str, url: str | None, priority: str):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("utf-8"), "Priority": priority, "Tags": "apple,moneybag"}
    if url:
        headers["Click"] = url
    if os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['NTFY_TOKEN']}"
    requests.post(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers, timeout=20).raise_for_status()
    return True


def _telegram(title: str, body: str, url: str | None):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    # HTML statt Markdown: Produkttitel/URLs mit _ * [ würden Markdown-Parsing sprengen
    text = f"<b>{html.escape(title)}</b>\n{html.escape(body)}"
    if url:
        text += f'\n<a href="{html.escape(url, quote=True)}">Zum Angebot</a>'
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
    return True


def _mail(title: str, body: str, url: str | None):
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("MAIL_TO")
    if not (host and to):
        return False
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or to
    msg["To"] = to
    msg.set_content(body + (f"\n\n{url}" if url else ""))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def send(title: str, body: str, url: str | None = None, priority: str = "default") -> bool:
    sent = False
    for name, fn in (("ntfy", lambda: _ntfy(title, body, url, priority)),
                     ("telegram", lambda: _telegram(title, body, url)),
                     ("mail", lambda: _mail(title, body, url))):
        try:
            sent = fn() or sent
        except Exception as e:  # noqa: BLE001 – ein kaputter Kanal soll die anderen nicht blockieren
            log.error("Benachrichtigung über %s fehlgeschlagen: %s", name, e)
    if not sent:
        log.warning("Kein Benachrichtigungskanal konfiguriert – Nachricht nur im Log: %s | %s", title, body)
    return sent
