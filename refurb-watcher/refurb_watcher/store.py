"""Zustand als JSON-Datei: lesbar, diff-bar und einfach ins Git-Repo zu committen."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .matching import Watch
from .models import Offer

MAX_RUNS = 100


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Event:
    kind: str  # new | back | price_drop | price_rise | gone | alert
    watch: str
    entry: dict
    old_price: float | None = None


@dataclass
class RunResult:
    events: list[Event] = field(default_factory=list)
    alerts: list[Event] = field(default_factory=list)


class Store:
    def __init__(self, path: str):
        self.path = path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}
        self.data.setdefault("offers", {})
        self.data.setdefault("runs", [])

    @property
    def offers(self) -> dict[str, dict]:
        return self.data["offers"]

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def record_run(self, ts: str, shop_status: dict):
        self.data["runs"] = (self.data["runs"] + [{"ts": ts, "shops": shop_status}])[-MAX_RUNS:]

    def apply(self, ts: str, matched: list[tuple[Watch, Offer]], crawled_shops: set[str],
              gone_after: int = 2) -> RunResult:
        """Gleicht die gefundenen Angebote mit dem Zustand ab und erzeugt Events/Alarme."""
        res = RunResult()
        # gleiches Angebot evtl. mehrfach (Listing + Produktseite) -> günstigstes behalten
        best: dict[str, tuple[Watch, Offer]] = {}
        for watch, offer in matched:
            key = f"{watch.id}::{offer.key}"
            if key not in best or offer.price < best[key][1].price:
                best[key] = (watch, offer)
        seen = set(best)
        for key, (watch, offer) in best.items():
            e = self.offers.get(key)
            if e is None:
                e = self.offers[key] = {
                    "watch": watch.id, "shop": offer.shop, "first_seen": ts,
                    "history": [[ts, offer.price]], "min_price": offer.price,
                    "price": offer.price, "last_alert_price": None,
                }
                res.events.append(Event("new", watch.id, e))
            else:
                if not e.get("active", True):
                    res.events.append(Event("back", watch.id, e))
                    e["last_alert_price"] = None
                if abs(offer.price - e["price"]) >= 0.01:
                    kind = "price_drop" if offer.price < e["price"] else "price_rise"
                    res.events.append(Event(kind, watch.id, e, old_price=e["price"]))
                    e["history"].append([ts, offer.price])
                    e["price"] = offer.price
                    e["min_price"] = min(e["min_price"], offer.price)
            e.update(url=offer.url, title=offer.title, condition=offer.condition, color=offer.color,
                     seller=offer.seller, price_is_from=offer.price_is_from,
                     last_seen=ts, active=True, missed=0)

            if watch.alert_below is not None:
                if e["price"] < watch.alert_below:
                    last = e.get("last_alert_price")
                    if last is None or e["price"] < last - 0.01:
                        e["last_alert_price"] = e["price"]
                        res.alerts.append(Event("alert", watch.id, e))
                else:
                    e["last_alert_price"] = None  # wieder drüber -> nächster Unterschreiter alarmiert erneut

        for key, e in self.offers.items():
            if key in seen or not e.get("active", True) or e["shop"] not in crawled_shops:
                continue
            e["missed"] = e.get("missed", 0) + 1
            if e["missed"] >= gone_after:
                e["active"] = False
                e["gone_since"] = ts
                res.events.append(Event("gone", e["watch"], e))
        return res
