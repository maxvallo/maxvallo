import os
import pathlib

import pytest
import yaml

from refurb_watcher import __main__ as cli
from refurb_watcher.crawler import crawl_shop, match_offers
from refurb_watcher.extract import extract_page, parse_price
from refurb_watcher.matching import Watch
from refurb_watcher.models import Offer
from refurb_watcher.store import Store

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures"
CONFIG = yaml.safe_load((HERE.parent / "config.yaml").read_text(encoding="utf-8"))
WATCHES = {w["id"]: Watch.from_config(w) for w in CONFIG["watches"]}
MAC, IPAD = WATCHES["macbook-air-m4"], WATCHES["ipad-air-11-m3"]


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("raw,expected", [
    ("1.099,00 €", 1099.0), ("969.00", 969.0), ("ab 1.129 €", 1129.0), (899, 899.0),
    ("1,099.50", 1099.5), ("479,9", 479.9), ({"amount": "12,50"}, 12.5), ("kostenlos", None), (None, None),
])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def offer(title, price=900.0, **kw):
    return Offer(shop="t", url=kw.pop("url", "https://x/p/1"), title=title, price=price, **kw)


@pytest.mark.parametrize("title,color,hit", [
    ('Apple MacBook Air 13,6" M4 (2025) 16 GB 256 GB', "Mitternacht", True),
    ("MacBook Air 15 Zoll M4 2025 Midnight", "", True),
    ("MacBook Air M4 2025 16GB", "Polarstern", False),
    ("MacBook Air M3 2024 Mitternacht", "", False),
    ("MacBook Pro 14 M4 Mitternacht", "", False),
    ("MacBook Air M4 Mitternachtsblau", "", True),
])
def test_macbook_matching(title, color, hit):
    assert MAC.matches(offer(title, color=color)) is hit


@pytest.mark.parametrize("title,url,hit", [
    ('iPad Air 11" (2025) M3 128 GB Wi-Fi', "https://x/p/1", True),
    ("Apple iPad Air (M3)", "https://www.refurbed.de/p/apple-ipad-air-11-m3-2025/42/", True),
    ('iPad Air 13" M3 2025', "https://x/p/1", False),
    ("iPad Air 11 M2 2024", "https://x/p/1", False),
    ('iPad Pro 11" M4', "https://x/p/1", False),
    ("iPad mini M3 11 Zoll", "https://x/p/1", False),
])
def test_ipad_matching(title, url, hit):
    assert IPAD.matches(offer(title, url=url)) is hit


def test_jsonld_product_group():
    url = "https://www.refurbed.de/p/apple-macbook-air-m4-2025/"
    offers, links = extract_page(fixture("product_jsonld.html"), url, "refurbed",
                                 CONFIG["shops"][0]["product_link_regex"])
    by_sku = {o.sku: o for o in offers if o.available}
    assert set(by_sku) == {"307995b", "308115", "307979c"}
    assert [(o.sku, o.price) for o in offers if not o.available] == [("309000", 1050.0)]
    a = by_sku["307995b"]
    assert a.price == 969.0 and a.condition == "RefurbishedCondition" and a.seller == "Händler A"
    assert a.url == "https://www.refurbed.de/p/apple-macbook-air-m4-2025/307995b/"
    assert "MacBook Air M4" in a.title and "Mitternacht" in a.title
    assert by_sku["308115"].price == 1129.0
    assert [o.sku for o in offers if MAC.matches(o) and o.available] == ["307995b", "308115"]
    assert links == ["https://www.refurbed.de/p/apple-macbook-air-m4-2025-15/308544b/"]


def test_embedded_next_data():
    url = "https://www.backmarket.de/de-de/search?q=ipad"
    offers, links = extract_page(fixture("listing_nextdata.html"), url, "backmarket", r"/de-de/p/ipad-air")
    prices = {o.sku: o.price for o in offers}
    assert prices == {"a1": 479.0, "a2": 649.0, "a3": 999.0, "a4": 399.0}
    hits = [o for o in offers if IPAD.matches(o)]
    assert [o.sku for o in hits] == ["a1"]
    assert hits[0].url == "https://www.backmarket.de/de-de/p/ipad-air-11-2025-m3/a1"
    assert hits[0].condition == "Sehr gut"
    assert links == ["https://www.backmarket.de/de-de/p/ipad-air-11-2025-m3/a1"]


def test_meta_fallback():
    offers, _ = extract_page(fixture("meta_only.html"), "https://asgoodasnew.de/x", "asgoodasnew")
    assert len(offers) == 1 and offers[0].price == 529.9 and IPAD.matches(offers[0])


def test_store_events_and_alerts(tmp_path):
    store = Store(str(tmp_path / "s.json"))
    o1 = offer("MacBook Air M4 Mitternacht", 1150.0, sku="A")
    o2 = offer("MacBook Air M4 Mitternacht", 1200.0, sku="B")

    r = store.apply("2026-01-01T00:00:00+00:00", [(MAC, o1), (MAC, o2)], {"t"})
    assert sorted(e.kind for e in r.events) == ["new", "new"] and not r.alerts

    o1.price = 1049.0  # fällt unter 1100 -> Alarm
    r = store.apply("2026-01-02T00:00:00+00:00", [(MAC, o1), (MAC, o2)], {"t"})
    assert [e.kind for e in r.events] == ["price_drop"] and len(r.alerts) == 1

    r = store.apply("2026-01-03T00:00:00+00:00", [(MAC, o1), (MAC, o2)], {"t"})
    assert not r.events and not r.alerts  # gleicher Preis -> kein zweiter Alarm

    o1.price = 999.0  # noch günstiger -> erneut Alarm
    r = store.apply("2026-01-04T00:00:00+00:00", [(MAC, o1), (MAC, o2)], {"t"})
    assert len(r.alerts) == 1

    # B fehlt zweimal in vollständigen Läufen -> weg; fehlt es nur bei Shop-Fehler, bleibt es
    store.apply("2026-01-05T00:00:00+00:00", [(MAC, o1)], set())
    r = store.apply("2026-01-06T00:00:00+00:00", [(MAC, o1)], {"t"})
    assert not r.events
    r = store.apply("2026-01-07T00:00:00+00:00", [(MAC, o1)], {"t"})
    assert [e.kind for e in r.events] == ["gone"]
    r = store.apply("2026-01-08T00:00:00+00:00", [(MAC, o1), (MAC, o2)], {"t"})
    assert [e.kind for e in r.events] == ["back"]

    store.save()
    again = Store(str(tmp_path / "s.json"))
    entry = next(e for e in again.offers.values() if e["price"] == 999.0)
    assert entry["min_price"] == 999.0 and [p for _, p in entry["history"]] == [1150.0, 1049.0, 999.0]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, mode="requests"):
        from refurb_watcher.fetch import FetchError
        self.calls.append(url)
        if url not in self.pages:
            raise FetchError(f"{url}: HTTP 404")
        return self.pages[url]

    def close(self):
        pass


def test_crawl_follows_product_links():
    shop = {"id": "refurbed", "start_urls": ["https://www.refurbed.de/p/apple-macbook-air-m4-2025/"],
            "product_link_regex": CONFIG["shops"][0]["product_link_regex"]}
    page15 = fixture("product_jsonld.html").replace("13,6", "15,3").replace("307995b", "15a")
    fetcher = FakeFetcher({
        shop["start_urls"][0]: fixture("product_jsonld.html"),
        "https://www.refurbed.de/p/apple-macbook-air-m4-2025-15/308544b/": page15,
    })
    res = crawl_shop(shop, fetcher, list(WATCHES.values()))
    assert res.complete and res.pages == 2
    skus = sorted(o.sku for w, o in match_offers(res.offers, list(WATCHES.values())))
    assert skus == ["15a", "307995b", "308115", "308115"]


def test_cli_run_end_to_end(tmp_path, monkeypatch, capsys):
    cfg = dict(CONFIG)
    cfg["settings"] = {**CONFIG["settings"], "state_file": str(tmp_path / "state.json"),
                       "report_file": str(tmp_path / "latest.md")}
    cfg["shops"] = [{"id": "refurbed", "start_urls": ["https://www.refurbed.de/p/apple-macbook-air-m4-2025/"],
                     "product_link_regex": "^$"}]
    fake = FakeFetcher({"https://www.refurbed.de/p/apple-macbook-air-m4-2025/": fixture("product_jsonld.html")})
    monkeypatch.setattr(cli, "Fetcher", lambda **kw: fake)
    sent = []
    monkeypatch.setattr(cli.notify, "send", lambda *a, **kw: sent.append((a, kw)) or True)
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    assert cli.main(["-c", str(cfg_path), "run"]) == 0
    titles = [a[0] for a, _ in sent]
    assert any("969,00 €" in t for t in titles)          # Alarm < 1100
    assert not any("1.129,00" in t for t in titles)       # über Schwelle -> kein Alarm
    report = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "969,00 €" in report and "MacBook Air M4" in report
    assert os.path.exists(tmp_path / "state.json")


def test_telegram_message_is_html_escaped(monkeypatch):
    from refurb_watcher import notify
    calls = []

    class Resp:
        ok, status_code, text = True, 200, ""

    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: calls.append((url, json)) or Resp())
    for var in ("NTFY_TOPIC", "SMTP_HOST", "MAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert notify.send("🔔 MacBook <13\">", "Air_M4 *Mitternacht* & mehr",
                       url="https://x/p/a_b?c=1&d=2", priority="high")
    url, payload = calls[0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage" and payload["chat_id"] == "42"
    assert payload["parse_mode"] == "HTML"
    assert "&lt;13&quot;&gt;" in payload["text"] and "&amp; mehr" in payload["text"]
    assert 'href="https://x/p/a_b?c=1&amp;d=2"' in payload["text"]


def test_report_escapes_pipes_in_table(tmp_path):
    from refurb_watcher.report import build_report
    store = Store(str(tmp_path / "s.json"))
    o = offer('Apple MacBook Air 2025 | 13.6" | M4 - Mitternacht 256 GB', 1293.75, sku="X")
    store.apply("2026-01-01T00:00:00+00:00", [(MAC, o)], {"t"})
    row = next(l for l in build_report(store, [MAC]).splitlines() if l.startswith("| 1.293,75"))
    assert "2025 \\| 13.6\" \\| M4" in row
    assert len(row.replace("\\|", "").split("|")) == 6  # 4 Spalten


def test_debug_dump(tmp_path, monkeypatch):
    from refurb_watcher.fetch import _dump
    _dump("https://x.de/a?b=1", "<html>", 200)
    assert not list(tmp_path.iterdir())
    monkeypatch.setenv("DEBUG_HTML_DIR", str(tmp_path))
    _dump("https://x.de/a?b=1", "<html>", 403)
    assert [p.name for p in tmp_path.iterdir()] == ["x_de_a_b_1__403.html"]


@pytest.mark.parametrize("title,url,hit", [
    ('iPad Air 7 (2025) | 11" | 128 GB', "https://www.refurbed.de/p/ipad-air-7-2025-11/307913/", True),
    ('Apple iPad Air (2025) 11 Zoll 256 GB', "https://x/p/1", True),
    ('Apple iPad Air (2024) 11 Zoll M2', "https://x/p/1", False),
    ('iPad Air 7 (2025) | 13" | 128 GB', "https://www.refurbed.de/p/ipad-air-7-2025-13/1/", False),
])
def test_ipad_matching_without_chip_name(title, url, hit):
    assert IPAD.matches(offer(title, url=url)) is hit


def _variant_page(skus_prices, current, options):
    """Nachbau einer refurbed-Produktseite: ProductGroup + Zustands-Auswahl."""
    import json
    variants = [{"@type": "Product", "sku": sku, "name": f"Apple MacBook Air 2025 | 13.6\" | M4 - {color} 256 GB",
                 "color": color, "offers": {"@type": "Offer", "url": f"/p/apple-macbook-air-m4-2025/{path}/?offer=1",
                                            "price": price, "availability": avail}}
                for sku, color, path, price, avail in skus_prices]
    ld = {"@context": "https://schema.org", "@type": "ProductGroup", "name": "MacBook Air", "hasVariant": variants}
    opts = "".join(f'<option value="/p/apple-macbook-air-m4-2025/{o}/?offer=9">x</option>' for o in options)
    return (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head>'
            f'<body><select id="product-grade">{opts}</select></body></html>')


def test_crawl_follows_only_matching_variants_and_conditions():
    base = "https://www.refurbed.de/p/apple-macbook-air-m4-2025/"
    shop = {"id": "refurbed", "start_urls": [base], "max_depth": 2, "max_product_pages": 50,
            "product_link_regex": CONFIG["shops"][0]["product_link_regex"],
            "link_id_regex": CONFIG["shops"][0]["link_id_regex"], "link_ignore_query": True,
            "condition_from_url": CONFIG["shops"][0]["condition_from_url"]}
    IS, OOS = "https://schema.org/InStock", "https://schema.org/OutOfStock"
    pages = {
        # Startseite: Mitternacht (Sehr gut), Polarstern, Mitternacht 15" ausverkauft
        base: _variant_page([("100", "Mitternacht", "100b", 1290, IS), ("200", "Polarstern", "200", 1250, IS),
                             ("300", "Mitternacht", "300aa", 0, OOS)], "100b", ["200", "200c"]),
        base + "100b/": _variant_page([("100", "Mitternacht", "100b", 1290, IS)], "100b", ["100", "100c", "200c"]),
        base + "100/": _variant_page([("100", "Mitternacht", "100", 1350, IS)], "100", []),
        base + "100c/": _variant_page([("100", "Mitternacht", "100c", 1079, IS)], "100c", []),
        base + "300aa/": _variant_page([("300", "Mitternacht", "300aa", 0, OOS)], "300aa", ["300c"]),
        base + "300c/": _variant_page([("300", "Mitternacht", "300c", 1099, IS)], "300c", []),
    }
    fetcher = FakeFetcher(pages)
    res = crawl_shop(shop, fetcher, list(WATCHES.values()))
    assert res.complete, res.errors
    assert not any("/200" in u for u in fetcher.calls)  # Polarstern wird nie geladen
    assert len(fetcher.calls) == len(set(fetcher.calls))
    found = {(o.sku, o.condition, o.price) for w, o in match_offers(res.offers, [MAC])}
    assert ("100", "Gut", 1079.0) in found and ("100", "Exzellent", 1350.0) in found
    assert ("100", "Sehr gut", 1290.0) in found and ("300", "Gut", 1099.0) in found
    assert not any(o.price == 0 for o in res.offers)


def test_crawl_respects_page_cap():
    base = "https://www.refurbed.de/p/apple-macbook-air-m4-2025/"
    many = [(str(i), "Mitternacht", str(i), 1200 + i, "InStock") for i in range(100, 130)]
    shop = {"id": "refurbed", "start_urls": [base], "max_product_pages": 5, "link_ignore_query": True,
            "product_link_regex": CONFIG["shops"][0]["product_link_regex"],
            "link_id_regex": CONFIG["shops"][0]["link_id_regex"]}
    fetcher = FakeFetcher({base: _variant_page(many, "100", [])} |
                          {f"{base}{i}/": _variant_page(many, str(i), []) for i in range(100, 130)})
    res = crawl_shop(shop, fetcher, [MAC])
    assert res.pages == 6 and res.complete
