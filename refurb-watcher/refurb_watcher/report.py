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
            status = "✅" if st.get("complete") else ("⚠️ " + "; ".join(st.get("errors", []))[:200])
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
                lines.append(f"| {price_str(e)}{flag} | {e['shop']} | [{describe(e)}]({e['url']}) | {since} |")
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
