import re
from datetime import datetime, timedelta, timezone

from .matching import Watch
from .store import Event, Store

CONDITION_DE = {
    "NewCondition": "neu", "RefurbishedCondition": "refurbished", "UsedCondition": "gebraucht",
    "DamagedCondition": "beschädigt",
}
EVENT_DE = {"new": "🆕 neu", "back": "🔁 wieder da", "price_drop": "📉 günstiger",
            "price_rise": "📈 teurer", "gone": "❌ weg", "alert": "🔔 Alarm"}


def eur(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def describe(e: dict) -> str:
    parts = [e.get("title", "?")]
    cond = CONDITION_DE.get(e.get("condition"), e.get("condition"))
    if cond:
        parts.append(f"Zustand: {cond}")
    if e.get("seller"):
        parts.append(f"Händler: {e['seller']}")
    return " · ".join(parts)


def price_str(e: dict) -> str:
    return ("ab " if e.get("price_is_from") else "") + eur(e["price"])


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def build_report(store: Store, watches: list[Watch], events: list[Event] | None = None,
                 days_new: int = 7) -> str:
    now = datetime.now(timezone.utc)
    lines = [f"# Refurb-Watcher – Stand {now.astimezone().strftime('%d.%m.%Y %H:%M')}", ""]
    last_run = store.data["runs"][-1] if store.data["runs"] else None
    if last_run:
        lines += ["| Shop | Seiten | Angebote gelesen | Status |", "|---|---|---|---|"]
        for shop, st in last_run["shops"].items():
            status = "✅" if st.get("complete") else ("⚠️ " + "; ".join(st.get("errors", []))[:200]).replace("|", "\\|")
            lines.append(f"| {shop} | {st['pages']} | {st['offers']} | {status} |")
        lines.append("")

    for w in watches:
        entries = [e for e in store.offers.values() if e["watch"] == w.id]
        active = sorted((e for e in entries if e.get("active", True)), key=lambda e: e["price"])
        lines.append(f"## {w.name}")
        lines.append("")
        alltime = min((e["min_price"] for e in entries), default=None)
        lines.append(f"Aktive Angebote: **{len(active)}** · günstigstes jetzt: **{eur(active[0]['price']) if active else '–'}**"
                     f" · Tiefstpreis seit Beobachtung: {eur(alltime)}"
                     + (f" · Alarm unter {eur(w.alert_below)}" if w.alert_below else ""))
        lines.append("")
        if active:
            lines += ["| Preis | Shop | Angebot | seit |", "|---:|---|---|---|"]
            for e in active[:15]:
                flag = " 🔔" if w.alert_below and e["price"] < w.alert_below else ""
                since = _parse(e["first_seen"]).astimezone().strftime("%d.%m.")
                cell = describe(e).replace("|", "\\|")
                lines.append(f"| {price_str(e)}{flag} | {e['shop']} | [{cell}]({e['url']}) | {since} |")
            lines.append("")
        recent = sorted((e for e in entries if _parse(e["first_seen"]) > now - timedelta(days=days_new)),
                        key=lambda e: e["first_seen"], reverse=True)
        if recent:
            lines.append(f"**Neu in den letzten {days_new} Tagen:**")
            lines.append("")
            for e in recent[:15]:
                when = _parse(e["first_seen"]).astimezone().strftime("%d.%m. %H:%M")
                lines.append(f"- {when} – {price_str(e)} – {e['shop']} – [{describe(e)}]({e['url']})")
            lines.append("")

    if events:
        lines += ["## Änderungen im letzten Lauf", ""]
        for ev in events:
            old = f" (vorher {eur(ev.old_price)})" if ev.old_price is not None else ""
            lines.append(f"- {EVENT_DE.get(ev.kind, ev.kind)}: {price_str(ev.entry)}{old} – "
                         f"{ev.entry['shop']} – [{describe(ev.entry)}]({ev.entry['url']})")
        lines.append("")
    return "\n".join(lines)


def short_title(e: dict) -> str:
    """'Apple MacBook Air 2025 | 13.6" | M4 - Mitternacht 256 GB' -> '13.6" Mitternacht 256 GB'"""
    title = e.get("title", "")
    if " - " not in title:
        return title[:60]
    head, variant = title.rsplit(" - ", 1)
    inch = re.search(r'(\d{2}(?:[.,]\d)?)\s*(?:"|zoll|inch)', head, re.I)
    return f'{inch.group(1)}" {variant}' if inch else variant


def build_summary(store: Store, watches: list[Watch], shop_status: dict,
                  top_n: int = 3) -> tuple[str, list[tuple[str, list[tuple[str, str]]]]]:
    """Kurzübersicht für Telegram: je Suche die günstigsten aktiven Angebote.

    Merkt sich den günstigsten Preis je Suche, um beim nächsten Mal die Veränderung zu zeigen.
    """
    last = store.data.setdefault("summary_best", {})
    sections = []
    for w in watches:
        active = sorted((e for e in store.offers.values() if e["watch"] == w.id and e.get("active", True)),
                        key=lambda e: e["price"])
        if not active:
            sections.append((f"{w.name}: keine Angebote", []))
            last.pop(w.id, None)
            continue
        best = active[0]["price"]
        prev = last.get(w.id)
        delta = ""
        if prev is not None and abs(best - prev) >= 0.01:
            delta = f" ({'↓' if best < prev else '↑'} {eur(abs(best - prev))})"
        alarm = f" · Alarm < {eur(w.alert_below)}" if w.alert_below else ""
        links = []
        for e in active[:top_n]:
            cond = CONDITION_DE.get(e.get("condition"), e.get("condition"))
            flag = "🔔 " if w.alert_below and e["price"] < w.alert_below else ""
            label = " · ".join(x for x in (f"{flag}{price_str(e)}", cond, short_title(e)) if x)
            if len(shop_status) > 1:
                label += f" ({e['shop']})"
            links.append((label, e["url"]))
        sections.append((f"{w.name}: ab {eur(best)}{delta}{alarm}", links))
        last[w.id] = best
    broken = [shop for shop, st in shop_status.items() if not st.get("complete")]
    if broken:
        sections.append((f"⚠️ Nicht vollständig gelesen: {', '.join(broken)} – Preise evtl. veraltet", []))
    return "📊 Günstigste Angebote", sections
