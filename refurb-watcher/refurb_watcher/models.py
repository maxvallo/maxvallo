from dataclasses import dataclass, field
from urllib.parse import urldefrag


@dataclass
class Offer:
    shop: str
    url: str
    title: str
    price: float
    currency: str = "EUR"
    condition: str = ""
    color: str = ""
    sku: str = ""
    seller: str = ""
    price_is_from: bool = False  # "ab X €" (AggregateOffer.lowPrice)
    available: bool = True  # ausverkaufte Varianten dienen nur zum Finden weiterer Seiten
    extra: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        ident = self.sku or urldefrag(self.url)[0]
        return "|".join([self.shop, ident, self.condition, self.color, self.seller])

    def match_text(self) -> str:
        return " ".join([self.title, self.url, self.color, self.condition, *self.extra])
