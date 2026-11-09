# Refurb-Watcher

Beobachtet Refurbished-Shops nach

| Suche | Alarm |
|---|---|
| **MacBook Air 13" M4 (2025)**, Farbe **Mitternacht** | unter **1.100 €** |
| **MacBook Air 15" M4 (2025)**, Farbe **Mitternacht** | unter **1.100 €** |
| **iPad Air 11" M3 (2025)**, beliebige Farbe/Speicher | unter **500 €** |

Shops: **refurbed.de** (aktiv). **asgoodasnew.de**, **backmarket.de** und **rebuy.de** sind vorbereitet, aber abgeschaltet – siehe *Stand / Einschränkungen*.

Pro Lauf entstehen:
- ein **Bericht** `reports/latest.md`: aktuelle Preise (günstigste zuerst), neue Angebote der letzten 7 Tage, Tiefstpreis seit Beobachtung, Änderungen seit dem letzten Lauf;
- eine **Preis-Historie** `data/state.json` (wann ist welches Angebot aufgetaucht, verschwunden, billiger/teurer geworden);
- **Telegram-Übersicht nach jedem Lauf** (alle 3 h): je Suche (MacBook Air 13", MacBook Air 15", iPad Air 11") die 3 günstigsten Angebote mit Preis, Zustand, Link und Veränderung zum letzten Lauf (`notify_summary`, `summary_top_n` in `config.yaml`).
- **Telegram-Preisalarm** (hohe Priorität), sobald ein Angebot die Preisgrenze unterschreitet (je Preisstufe einmal). Optional auch bei neuen Angeboten: `notify_new_offers: true`.

## Konzept

```
 GitHub Actions (alle 3 h)          ┌──────────── config.yaml ────────────┐
          │                         │ watches: Suchkriterien + Preisgrenze │
          ▼                         │ shops:   Start-URLs + Link-Muster    │
   ┌─────────────┐  HTML            └──────────────────────────────────────┘
   │  Fetcher    │◀──── refurbed / asgoodasnew / Back Market / rebuy
   │ (requests,  │      höflich: 2–5 s Pause, robots.txt, Retries,
   │  optional   │      optional echter Browser (Playwright)
   │  Playwright)│
   └─────┬───────┘
         ▼
   ┌─────────────┐  1. schema.org JSON-LD (Product/Offer – nutzen die Shops für Google Shopping)
   │  Extraktion │  2. eingebettetes JSON (__NEXT_DATA__ & Co.)
   │             │  3. Meta-Tags (og:title + product:price)
   └─────┬───────┘  + Produktlinks einsammeln und eine Ebene tiefer crawlen
         ▼
   ┌─────────────┐  Text aus Titel, URL, Farbe, Zustand, Merkmalen;
   │  Matching   │  "macbook air" UND "m4" UND (mitternacht|midnight), ohne "macbook pro" …
   └─────┬───────┘
         ▼
   ┌─────────────┐  Abgleich mit letztem Stand -> Ereignisse:
   │  Store      │  neu · wieder da · günstiger · teurer · weg · Alarm
   │ (JSON-Datei)│  Alarm nur einmal je Preisstufe (erneut erst bei weiterem Preisrutsch
   └─────┬───────┘  oder nachdem der Preis zwischenzeitlich über der Grenze lag)
         ▼
  Telegram (ntfy/E-Mail)    +   reports/latest.md   +   Commit ins Repo (Historie)
```

Designentscheidungen:
- **Keine shop-spezifischen CSS-Selektoren.** Die werden bei jedem Redesign kaputt. Stattdessen werden die strukturierten Produktdaten (JSON-LD) gelesen, die alle großen Shops für Suchmaschinen ausliefern. Neue Shops brauchen nur einen Eintrag in `config.yaml`.
- **Baujahr 2025** wird über den Chip abgedeckt: Das MacBook Air mit M4 und das iPad Air mit M3 gibt es nur als 2025er-Modelle. 13" und 15" sind getrennte Suchen, damit Übersicht und Alarme je Größe kommen.
- **„Weg"-Erkennung** nur, wenn ein Shop fehlerfrei gecrawlt wurde und das Angebot 2 Läufe in Folge fehlt – ein Timeout erzeugt also keine Fehlalarme.
- **Zustand als JSON im Git-Repo:** kein Server, keine Datenbank, Historie über Git-Commits nachvollziehbar, Bericht direkt auf GitHub lesbar.

## Einrichten

### 1. Benachrichtigung per Telegram
1. In Telegram **@BotFather** öffnen, `/newbot` senden, Namen vergeben (z. B. `Refurb Watcher`, Benutzername muss auf `bot` enden). BotFather antwortet mit dem **Token** (`123456789:AA…`).
2. Den neuen Bot über den Link von BotFather öffnen und ihm **eine beliebige Nachricht schicken** (sonst darf er dir nicht schreiben).
3. Im Browser `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen und bei `"chat":{"id":…}` die Zahl ablesen – das ist die **Chat-ID**.
4. Im GitHub-Repo unter *Settings → Secrets and variables → Actions → New repository secret* anlegen:
   - `TELEGRAM_BOT_TOKEN` = Token aus Schritt 1
   - `TELEGRAM_CHAT_ID` = Zahl aus Schritt 3
5. Testen: *Actions → refurb-watcher → Run workflow*, Haken bei **„Nur eine Testnachricht senden“** → nach ca. 1 Minute kommt „Refurb-Watcher Test“ in Telegram.

Alternativ/zusätzlich funktionieren ntfy (`NTFY_TOPIC`) und E-Mail (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO`; Gmail: App-Passwort, Port 587).

### 2. Regelmäßig laufen lassen
Der Workflow `.github/workflows/refurb-watcher.yml` läuft alle 3 Stunden (ca. 7–22 Uhr) und committet Bericht + Historie zurück ins Repo. **GitHub führt zeitgesteuerte Workflows nur auf dem Default-Branch (`master`) aus** – der Branch muss also gemergt sein. Manuell starten: *Actions → refurb-watcher → Run workflow*.

### 3. Lokal
```bash
cd refurb-watcher
pip install -r requirements-dev.txt
python -m pytest -q                       # Tests
TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=… python -m refurb_watcher test-notify
python -m refurb_watcher run --dry-run    # crawlen ohne Benachrichtigung
python -m refurb_watcher run --shop refurbed
python -m refurb_watcher report           # Bericht aus gespeichertem Stand
```
Per Cron (z. B. auf einem Raspberry Pi): `17 7-23/2 * * * cd ~/maxvallo/refurb-watcher && python3 -m refurb_watcher run`

## Anpassen & Fehlersuche

- **Suchkriterien / Preisgrenzen:** `watches` in `config.yaml` (z. B. nur 512 GB: weitere Zeile `- ["512 gb"]` unter `require`).
- **Ein Shop liefert 0 Angebote?** Seite im Browser öffnen, als HTML speichern und auswerten:
  ```bash
  python -m refurb_watcher parse seite.html --shop-id refurbed --url https://www.refurbed.de/…
  ```
  Zeigt alle erkannten Angebote (✔ = passt auf eine Suche) und die gefundenen Produktlinks. Danach `start_urls` bzw. `product_link_regex` anpassen.
- **Debug-Lauf:** *Run workflow* mit Haken bei „Debug“ speichert alle geladenen Seiten im Branch `refurb-watcher-debug` (ohne Benachrichtigung, ohne Commit auf `master`). Die Dateien lassen sich dann mit `parse` auswerten.
- **refurbed-Besonderheit:** Jede Farb-/Speicher-Variante hat eine ID (`/p/…/307979b/`), der Buchstabe dahinter ist der Zustand (ohne = Exzellent, `aa` = Premium, `b` = Sehr gut, `c` = Gut). Der Crawler folgt von der Startseite nur den Varianten, die zur Suche passen, und von dort den Zustands-Auswahlmenüs (`link_id_regex`, `max_depth: 2`, `condition_from_url` in der Config). So werden ca. 80–100 Seiten pro Lauf geladen statt des ganzen Shops.
- Bitte die Abfrage-Frequenz moderat lassen (≥ 1 h) und die AGB der Shops respektieren; der Crawler hält sich standardmäßig an `robots.txt`.

## Stand / Einschränkungen

- **refurbed** funktioniert von GitHub aus (live getestet).
- **asgoodasnew** und **Back Market** antworten Zugriffen von GitHub-Servern mit HTTP 403 – auch mit echtem Browser (`fetcher: playwright`). Das ist Bot-Schutz gegen Rechenzentrums-IPs. Von einem privaten Internetanschluss aus (z. B. Raspberry Pi per Cron, siehe oben) können sie funktionieren: dort `enabled: true` setzen und `pip install playwright && python -m playwright install chromium` ausführen. Die Such-URLs dieser Shops sind noch nicht live geprüft.
