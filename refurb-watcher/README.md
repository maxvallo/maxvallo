# Refurb-Watcher

Beobachtet Refurbished-Shops nach

| Suche | Alarm |
|---|---|
| **MacBook Air M4 (2025)**, 13" oder 15", Farbe **Mitternacht** | unter **1.100 €** |
| **iPad Air 11" M3 (2025)**, beliebige Farbe/Speicher | unter **500 €** |

Shops: **refurbed.de**, **asgoodasnew.de**, **backmarket.de** (optional **rebuy.de**).

Pro Lauf entstehen:
- ein **Bericht** `reports/latest.md`: aktuelle Preise (günstigste zuerst), neue Angebote der letzten 7 Tage, Tiefstpreis seit Beobachtung, Änderungen seit dem letzten Lauf;
- eine **Preis-Historie** `data/state.json` (wann ist welches Angebot aufgetaucht, verschwunden, billiger/teurer geworden);
- **Push-Nachrichten**: sofort bei Unterschreiten der Preisgrenze (hohe Priorität), sonst eine Sammelnachricht bei neuen Angeboten.

## Konzept

```
 GitHub Actions (alle 2 h)          ┌──────────── config.yaml ────────────┐
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
  ntfy / Telegram / E-Mail   +   reports/latest.md   +   Commit ins Repo (Historie)
```

Designentscheidungen:
- **Keine shop-spezifischen CSS-Selektoren.** Die werden bei jedem Redesign kaputt. Stattdessen werden die strukturierten Produktdaten (JSON-LD) gelesen, die alle großen Shops für Suchmaschinen ausliefern. Neue Shops brauchen nur einen Eintrag in `config.yaml`.
- **Baujahr 2025** wird über den Chip abgedeckt: Das MacBook Air mit M4 und das iPad Air mit M3 gibt es nur als 2025er-Modelle. Größe beim MacBook ist egal, weil das M4-Air ausschließlich in 13" und 15" existiert.
- **„Weg"-Erkennung** nur, wenn ein Shop fehlerfrei gecrawlt wurde und das Angebot 2 Läufe in Folge fehlt – ein Timeout erzeugt also keine Fehlalarme.
- **Zustand als JSON im Git-Repo:** kein Server, keine Datenbank, Historie über Git-Commits nachvollziehbar, Bericht direkt auf GitHub lesbar.

## Einrichten

### 1. Benachrichtigung (ntfy – empfohlen, kostenlos, ohne Account)
1. App **ntfy** installieren (iOS/Android).
2. Ein schwer zu erratendes Topic abonnieren, z. B. `maxvallo-refurb-7f3k2`.
3. Im GitHub-Repo unter *Settings → Secrets and variables → Actions* das Secret `NTFY_TOPIC` mit diesem Namen anlegen.

Alternativ/zusätzlich: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` oder `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (Gmail: App-Passwort, Port 587).

### 2. Regelmäßig laufen lassen
Der Workflow `.github/workflows/refurb-watcher.yml` läuft alle 2 Stunden (tagsüber) und committet Bericht + Historie zurück ins Repo. **GitHub führt zeitgesteuerte Workflows nur auf dem Default-Branch (`master`) aus** – der Branch muss also gemergt sein. Manuell starten: *Actions → refurb-watcher → Run workflow*.

### 3. Lokal
```bash
cd refurb-watcher
pip install -r requirements-dev.txt
python -m pytest -q                       # Tests
NTFY_TOPIC=… python -m refurb_watcher test-notify
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
- **Back Market** setzt einen starken Bot-Schutz ein und blockt einfache HTTP-Requests (gerade von Cloud-IPs wie GitHub Actions) oft mit HTTP 403. Dann `fetcher: playwright` setzen und im Workflow `pip install playwright && playwright install --with-deps chromium` ergänzen. Klappt es trotzdem nicht, den Shop mit `enabled: false` abschalten.
- Bitte die Abfrage-Frequenz moderat lassen (≥ 1 h) und die AGB der Shops respektieren; der Crawler hält sich standardmäßig an `robots.txt`.

## Stand / Einschränkungen

Der Crawler wurde mit nachgebauten Shop-Seiten getestet (`tests/`), aber **noch nicht live gegen die Shops** – die Entwicklungsumgebung hatte keinen Netzwerkzugriff auf sie. Die refurbed-URLs (`/p/apple-macbook-air-m4-2025/…`) sind real, die Such-URLs der anderen Shops sind nach deren üblichem Schema angenommen. Der erste Lauf (lokal oder per *Run workflow*) zeigt im Bericht pro Shop, wie viele Seiten und Angebote gelesen wurden – bei 0 bitte wie oben beschrieben nachjustieren.
