import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from .extract import extract_page
from .fetch import FetchError, Fetcher
from .matching import Watch
from .models import Offer

log = logging.getLogger(__name__)


@dataclass
class ShopResult:
    shop: str
    offers: list[Offer] = field(default_factory=list)
    pages: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Nur bei fehlerfreiem Lauf dürfen fehlende Angebote als 'verschwunden' gelten."""
        return self.pages > 0 and not self.errors

    def status(self) -> dict:
        return {"pages": self.pages, "offers": len(self.offers), "errors": self.errors[:10],
                "complete": self.complete}


def _norm(url: str, ignore_query: bool) -> str:
    return urlunparse(urlparse(url)._replace(query="", fragment="")) if ignore_query else url


def crawl_shop(shop: dict, fetcher: Fetcher, watches: list[Watch]) -> ShopResult:
    """Breitensuche ab den Start-URLs bis max_depth, höchstens max_product_pages Unterseiten.

    Mit link_id_regex werden nur Links verfolgt, deren ID (1. Gruppe des Regex) zu einem
    passenden Angebot gehört – z. B. nur die Zustands-Seiten der Mitternacht-Varianten
    statt aller Farben. Die Angebots-URLs passender Treffer werden dann ebenfalls besucht.
    """
    res = ShopResult(shop["id"])
    mode = shop.get("fetcher", "requests")
    pattern = shop.get("product_link_regex")
    max_depth = shop.get("max_depth", 1)
    cap = shop.get("max_product_pages", 30)
    ignore_query = shop.get("link_ignore_query", False)
    id_rx = re.compile(shop["link_id_regex"]) if shop.get("link_id_regex") else None
    conditions = [(re.compile(rx), label) for rx, label in shop.get("condition_from_url", [])]

    frontier = [(url, 0) for url in shop.get("start_urls", [])]
    seen = {_norm(url, ignore_query) for url, _ in frontier}
    subpages = 0

    while frontier:
        url, depth = frontier.pop(0)
        if depth > 0:
            if subpages >= cap:
                log.info("%s: Seitenlimit %d erreicht, %d Links übrig", shop["id"], cap, len(frontier) + 1)
                break
            subpages += 1
        try:
            html = fetcher.get(url, mode=mode)
        except FetchError as e:
            log.warning("%s: %s", shop["id"], e)
            res.errors.append(str(e))
            continue
        res.pages += 1
        offers, links = extract_page(html, url, shop["id"], pattern)
        for o in offers:
            for rx, label in conditions:
                if rx.search(urlparse(o.url).path):
                    o.condition = label
                    break
        relevant = [o for o in offers if any(w.matches(o) for w in watches)]
        log.info("%s: %s -> %d Angebote (%d relevant), %d Links",
                 shop["id"], url, len(offers), len(relevant), len(links))
        res.offers.extend(o for o in offers if o.available and o.price > 0)

        if depth >= max_depth:
            continue
        if id_rx:
            ids = {o.sku for o in relevant if o.sku}
            ids |= {m.group(1) for o in relevant if (m := id_rx.search(o.url))}
            links = [o.url for o in relevant] + [l for l in links if (m := id_rx.search(l)) and m.group(1) in ids]
        for link in links:
            key = _norm(link, ignore_query)
            if key not in seen and urlparse(key).netloc == urlparse(url).netloc:
                seen.add(key)
                frontier.append((key, depth + 1))
    return res


def match_offers(offers: list[Offer], watches: list[Watch]) -> list[tuple[Watch, Offer]]:
    return [(w, o) for o in offers for w in watches if w.matches(o)]
