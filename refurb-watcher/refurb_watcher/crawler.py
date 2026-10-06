import logging
from dataclasses import dataclass, field

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


def crawl_shop(shop: dict, fetcher: Fetcher, watches: list[Watch]) -> ShopResult:
    """Lädt die Start-URLs, liest Angebote aus und folgt Produktlinks eine Ebene tief."""
    res = ShopResult(shop["id"])
    mode = shop.get("fetcher", "requests")
    pattern = shop.get("product_link_regex")
    queue: list[str] = []
    visited: set[str] = set()

    def load(url: str) -> list[str]:
        visited.add(url)
        html = fetcher.get(url, mode=mode)
        res.pages += 1
        offers, links = extract_page(html, url, shop["id"], pattern)
        relevant = [o for o in offers if any(w.matches(o) for w in watches)]
        log.info("%s: %s -> %d Angebote (%d relevant), %d Links",
                 shop["id"], url, len(offers), len(relevant), len(links))
        res.offers.extend(offers)
        return links

    for url in shop.get("start_urls", []):
        try:
            for link in load(url):
                if link not in visited and link not in queue:
                    queue.append(link)
        except FetchError as e:
            log.warning("%s: %s", shop["id"], e)
            res.errors.append(str(e))

    for url in queue[: shop.get("max_product_pages", 30)]:
        if url in visited:
            continue
        try:
            load(url)
        except FetchError as e:
            log.warning("%s: %s", shop["id"], e)
            res.errors.append(str(e))
    return res


def match_offers(offers: list[Offer], watches: list[Watch]) -> list[tuple[Watch, Offer]]:
    return [(w, o) for o in offers for w in watches if w.matches(o)]
