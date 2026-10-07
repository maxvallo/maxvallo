"""CLI:  python -m refurb_watcher run | report | parse | test-notify"""

import argparse
import logging
import os
import sys

import yaml

from . import notify
from .crawler import crawl_shop, match_offers
from .extract import extract_page
from .fetch import Fetcher
from .matching import Watch
from .report import build_report, build_summary, describe, eur, price_str
from .store import Store, now_iso

log = logging.getLogger("refurb_watcher")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_run(cfg: dict, args) -> int:
    settings = cfg.get("settings", {})
    watches = [Watch.from_config(w) for w in cfg["watches"]]
    store = Store(settings.get("state_file", "data/state.json"))
    fetcher = Fetcher(delay=tuple(settings.get("delay_seconds", (2, 5))),
                      timeout=settings.get("timeout_seconds", 25),
                      respect_robots=settings.get("respect_robots_txt", True))
    ts = now_iso()
    matched, complete, status = [], set(), {}
    try:
        for shop in cfg["shops"]:
            if not shop.get("enabled", True) or (args.shop and shop["id"] not in args.shop):
                continue
            res = crawl_shop(shop, fetcher, watches)
            status[shop["id"]] = res.status()
            if res.complete:
                complete.add(shop["id"])
            matched += match_offers(res.offers, watches)
    finally:
        fetcher.close()

    result = store.apply(ts, matched, complete, gone_after=settings.get("gone_after_missed_runs", 2))
    store.record_run(ts, status)

    names = {w.id: w.name for w in watches}
    if not args.dry_run:
        for a in result.alerts:
            e = a.entry
            notify.send(f"🔔 {names[a.watch]}: {price_str(e)}",
                        f"{describe(e)}\nShop: {e['shop']}", url=e["url"], priority="high")
        new = [ev for ev in result.events if ev.kind in ("new", "back")
               and ev.entry not in [a.entry for a in result.alerts]]
        if new and settings.get("notify_new_offers", True):
            new.sort(key=lambda ev: ev.entry["price"])
            body = "\n".join(f"{price_str(ev.entry)} – {ev.entry['shop']} – {ev.entry['title']}"
                             for ev in new[:8])
            if len(new) > 8:
                body += f"\n… und {len(new) - 8} weitere"
            notify.send(f"🆕 {len(new)} neue Angebote", body, url=new[0].entry["url"])
        if settings.get("notify_summary", False):
            title, sections = build_summary(store, watches, status, settings.get("summary_top_n", 3))
            notify.send(title, sections=sections, priority="low")

    store.save()
    report = build_report(store, watches, result.events)
    report_file = settings.get("report_file", "reports/latest.md")
    os.makedirs(os.path.dirname(report_file) or ".", exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)

    log.info("Fertig: %d passende Angebote, %d Ereignisse, %d Alarme",
             len(matched), len(result.events), len(result.alerts))
    for a in result.alerts:
        print(f"ALARM {names[a.watch]}: {eur(a.entry['price'])} {a.entry['url']}")
    # Exit-Code 2, wenn kein einziger Shop erreichbar war -> fällt im CI auf
    return 2 if status and not any(s["pages"] for s in status.values()) else 0


def cmd_report(cfg: dict, args) -> int:
    settings = cfg.get("settings", {})
    store = Store(settings.get("state_file", "data/state.json"))
    print(build_report(store, [Watch.from_config(w) for w in cfg["watches"]]))
    return 0


def cmd_parse(cfg: dict, args) -> int:
    """Gespeicherte HTML-Seite auswerten – zum Anpassen der Konfiguration ohne Crawl."""
    watches = [Watch.from_config(w) for w in cfg["watches"]]
    shop = next((s for s in cfg["shops"] if s["id"] == args.shop_id), {"id": args.shop_id})
    with open(args.file, encoding="utf-8") as f:
        offers, links = extract_page(f.read(), args.url, shop["id"], shop.get("product_link_regex"))
    for o in offers:
        hits = [w.id for w in watches if w.matches(o)]
        print(f"{'✔' if hits else ' '} {eur(o.price):>12}  {o.title[:80]}  [{o.condition} {o.color}] {hits or ''}")
    print(f"\n{len(offers)} Angebote, {len(links)} Produktlinks:")
    for link in links:
        print("  ", link)
    return 0


def cmd_test_notify(cfg: dict, args) -> int:
    ok = notify.send("Refurb-Watcher Test", "Wenn du das liest, funktionieren die Benachrichtigungen. 🎉")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="refurb_watcher")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Shops crawlen, Zustand aktualisieren, benachrichtigen")
    r.add_argument("--dry-run", action="store_true", help="keine Benachrichtigungen senden")
    r.add_argument("--shop", action="append", help="nur diesen Shop (mehrfach möglich)")
    sub.add_parser("report", help="Bericht aus gespeichertem Zustand ausgeben")
    pa = sub.add_parser("parse", help="gespeicherte HTML-Datei auswerten")
    pa.add_argument("file")
    pa.add_argument("--shop-id", default="test")
    pa.add_argument("--url", default="https://example.invalid/")
    sub.add_parser("test-notify", help="Testnachricht senden")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    return {"run": cmd_run, "report": cmd_report, "parse": cmd_parse,
            "test-notify": cmd_test_notify}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
