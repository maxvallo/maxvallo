"""Shop-unabhängige Extraktion von Angeboten aus HTML.

Strategie (in dieser Reihenfolge):
1. schema.org JSON-LD (Product / ProductGroup / Offer / AggregateOffer / ItemList)
   – nutzen fast alle Shops für Google Shopping, daher am stabilsten.
2. Eingebettete JSON-Zustände (__NEXT_DATA__, __NUXT__, application/json-Skripte),
   heuristisch nach Objekten mit Name + Preis durchsucht.
3. Meta-Tags (og:title + product:price:amount / itemprop=price).
"""

import json
import re
from typing import Any, Iterable, Iterator
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from .models import Offer

UNAVAILABLE = ("outofstock", "soldout", "discontinued")
TITLE_KEYS = ("name", "title", "productName", "product_name", "displayName")
PRICE_KEYS = ("price", "priceValue", "salePrice", "grossPrice", "currentPrice",
              "minPrice", "lowPrice", "price_cents", "priceCents", "priceInCents")
URL_KEYS = ("url", "href", "link", "productUrl", "canonicalUrl", "path", "slug")
COLOR_KEYS = ("color", "colour", "colorName", "farbe")
CONDITION_KEYS = ("condition", "grade", "conditionName", "quality", "itemCondition")


def parse_price(value: Any) -> float | None:
    """'1.099,00 €' -> 1099.0, '969.00' -> 969.0, {'amount': '12'} -> 12.0"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for k in ("amount", "value", "price", "gross"):
            if k in value:
                return parse_price(value[k])
        return None
    m = re.search(r"\d[\d.,\s  ]*", str(value))
    if not m:
        return None
    num = re.sub(r"[\s  ]", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, tail = num.rsplit(",", 1)
        num = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else num.replace(",", "")
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.rsplit(".", 1)[1]) == 3):
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    if isinstance(t, str):
        t = [t]
    return {str(x).rsplit("/", 1)[-1] for x in t}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("@id") or "")
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return "" if value is None else str(value)


def _enum(value: Any) -> str:
    """'https://schema.org/RefurbishedCondition' -> 'RefurbishedCondition'"""
    return _text(value).rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------- JSON-LD

def iter_jsonld(soup: BeautifulSoup) -> Iterator[dict]:
    for tag in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        yield from _flatten(data)


def _flatten(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for d in data:
            yield from _flatten(d)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten(data["@graph"])
        if "@type" in data:
            yield data


def _product_extra(node: dict) -> list[str]:
    extra = [_text(node.get(k)) for k in ("description", "model", "category", "mpn", "gtin13")]
    for prop in _as_list(node.get("additionalProperty")):
        if isinstance(prop, dict):
            extra.append(f"{_text(prop.get('name'))} {_text(prop.get('value'))}")
    # Beschreibungen können lang sein und andere Modelle erwähnen -> kürzen
    return [e[:200] for e in extra if e]


def offers_from_product(node: dict, page_url: str, shop: str, parent_name: str = "") -> Iterator[Offer]:
    name = _text(node.get("name")) or parent_name
    if parent_name and parent_name not in name:
        name = f"{parent_name} {name}"
    url = urljoin(page_url, _text(node.get("url")) or page_url)
    color = _text(node.get("color"))
    sku = _text(node.get("sku") or node.get("productID"))
    extra = _product_extra(node)

    for variant in _as_list(node.get("hasVariant")):
        if isinstance(variant, dict):
            yield from offers_from_product(variant, url, shop, parent_name=name)

    for offer in _as_list(node.get("offers")):
        if not isinstance(offer, dict):
            continue
        nested = _as_list(offer.get("offers"))
        if "AggregateOffer" in _types(offer) and nested:
            yield from offers_from_product({**node, "offers": nested, "hasVariant": None},
                                           page_url, shop, parent_name)
            continue
        available = not any(u in _enum(offer.get("availability")).lower() for u in UNAVAILABLE)
        is_from = False
        price = parse_price(offer.get("price"))
        if price is None:
            spec = _as_list(offer.get("priceSpecification"))
            price = parse_price(spec[0].get("price")) if spec and isinstance(spec[0], dict) else None
        if price is None and offer.get("lowPrice") is not None:
            price, is_from = parse_price(offer.get("lowPrice")), True
        if not price:
            if available:
                continue
            price = 0.0
        offer_name = _text(offer.get("name"))
        yield Offer(
            shop=shop,
            url=urljoin(url, _text(offer.get("url")) or url),
            title=f"{name} {offer_name}".strip() if offer_name and offer_name not in name else name,
            price=price,
            currency=_text(offer.get("priceCurrency")) or "EUR",
            condition=_enum(offer.get("itemCondition")),
            color=color,
            sku=_text(offer.get("sku")) or sku,
            seller=_text(offer.get("seller")),
            price_is_from=is_from,
            available=available,
            extra=extra,
        )


def extract_jsonld(soup: BeautifulSoup, page_url: str, shop: str) -> tuple[list[Offer], list[str]]:
    offers, links = [], []
    for node in iter_jsonld(soup):
        types = _types(node)
        if types & {"Product", "ProductGroup", "IndividualProduct"}:
            offers.extend(offers_from_product(node, page_url, shop))
        elif "ItemList" in types:
            for el in _as_list(node.get("itemListElement")):
                if not isinstance(el, dict):
                    continue
                item = el.get("item", el)
                if isinstance(item, dict) and _types(item) & {"Product", "ProductGroup"}:
                    offers.extend(offers_from_product(item, page_url, shop))
                link = item.get("url") if isinstance(item, dict) else item
                if isinstance(link, str):
                    links.append(urljoin(page_url, link))
    return offers, links


# ------------------------------------------------------- eingebettetes JSON

EMBEDDED_ASSIGN = re.compile(r"window\.(__[A-Z_]+__|__NUXT__)\s*=\s*(\{.*\})\s*;?\s*$", re.S)


def iter_embedded_json(soup: BeautifulSoup) -> Iterator[Any]:
    for tag in soup.find_all("script"):
        typ = (tag.get("type") or "").lower()
        raw = tag.string or ""
        if "ld+json" in typ or not raw.strip():
            continue
        if typ == "application/json" or tag.get("id") in ("__NEXT_DATA__", "__NUXT_DATA__"):
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                pass
        else:
            m = EMBEDDED_ASSIGN.search(raw.strip())
            if m:
                try:
                    yield json.loads(m.group(2))
                except json.JSONDecodeError:
                    pass


def _walk(data: Any, depth: int = 0) -> Iterator[dict]:
    if depth > 40:
        return
    if isinstance(data, dict):
        yield data
        for v in data.values():
            yield from _walk(v, depth + 1)
    elif isinstance(data, list):
        for v in data:
            yield from _walk(v, depth + 1)


def _first(d: dict, keys: Iterable[str]) -> tuple[str, Any] | tuple[None, None]:
    for k in keys:
        if d.get(k) not in (None, "", [], {}):
            return k, d[k]
    return None, None


def extract_embedded(soup: BeautifulSoup, page_url: str, shop: str) -> list[Offer]:
    offers = []
    for blob in iter_embedded_json(soup):
        for d in _walk(blob):
            _, title = _first(d, TITLE_KEYS)
            pkey, raw_price = _first(d, PRICE_KEYS)
            if not isinstance(title, str) or pkey is None:
                continue
            price = parse_price(raw_price)
            if price is None:
                continue
            if "cent" in pkey.lower() or (isinstance(raw_price, dict) and "cent" in json.dumps(raw_price).lower()):
                price /= 100
            if not 20 <= price <= 20000:  # offensichtlich kein Gerätepreis
                continue
            _, link = _first(d, URL_KEYS)
            _, color = _first(d, COLOR_KEYS)
            _, cond = _first(d, CONDITION_KEYS)
            _, sku = _first(d, ("sku", "id", "offerId", "uuid"))
            offers.append(Offer(
                shop=shop,
                url=urljoin(page_url, link) if isinstance(link, str) else page_url,
                title=title,
                price=price,
                color=_text(color),
                condition=_enum(cond),
                sku=str(sku) if isinstance(sku, (str, int)) else "",
            ))
    return offers


# ------------------------------------------------------------- Meta-Tags

def extract_meta(soup: BeautifulSoup, page_url: str, shop: str) -> list[Offer]:
    def meta(*names):
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
                or soup.find(attrs={"itemprop": n})
            if tag:
                return tag.get("content") or tag.get_text(strip=True)
        return None

    title = meta("og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else None)
    price = parse_price(meta("product:price:amount", "og:price:amount", "price"))
    if not title or not price:
        return []
    return [Offer(shop=shop, url=page_url, title=title, price=price,
                  color=meta("product:color") or "", condition=meta("product:condition") or "")]


# ------------------------------------------------------------------ Links

def extract_links(soup: BeautifulSoup, page_url: str, pattern: str | None) -> list[str]:
    """Links aus <a href> und aus Auswahlmenüs (<option value="/p/...">, z. B. Zustand/Farbe)."""
    if not pattern:
        return []
    rx = re.compile(pattern, re.I)
    host = urlparse(page_url).netloc
    hrefs = [a["href"] for a in soup.find_all("a", href=True)]
    hrefs += [o["value"] for o in soup.find_all("option", value=True) if o["value"].startswith(("/", "http"))]
    seen, out = set(), []
    for href in hrefs:
        url = urldefrag(urljoin(page_url, href))[0]
        if urlparse(url).netloc != host or url in seen:
            continue
        if rx.search(urlparse(url).path):
            seen.add(url)
            out.append(url)
    return out


def extract_page(html: str, page_url: str, shop: str, link_pattern: str | None = None) -> tuple[list[Offer], list[str]]:
    """Gibt (Angebote, Produktlinks zum Weiterverfolgen) zurück."""
    soup = BeautifulSoup(html, "html.parser")
    offers, ld_links = extract_jsonld(soup, page_url, shop)
    if not offers:
        offers = extract_embedded(soup, page_url, shop)
    if not offers:
        offers = extract_meta(soup, page_url, shop)
    links = extract_links(soup, page_url, link_pattern)
    if link_pattern:
        rx = re.compile(link_pattern, re.I)
        links += [u for u in ld_links if rx.search(urlparse(u).path) and u not in links]
    return offers, links
