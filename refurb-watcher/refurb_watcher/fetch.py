import logging
import random
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.6",
}


class FetchError(Exception):
    pass


class Fetcher:
    """Höflicher HTTP-Client: Pausen zwischen Requests, Retries, robots.txt.

    mode="playwright" lädt Seiten in einem echten Chromium (für Shops mit Bot-Schutz).
    """

    def __init__(self, delay=(2, 5), timeout=25, respect_robots=True, retries=2):
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_request = 0.0
        self._pw = self._browser = None

    def _wait(self):
        pause = random.uniform(*self.delay) - (time.monotonic() - self._last_request)
        if pause > 0:
            time.sleep(pause)
        self._last_request = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = None
            try:
                r = self.session.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                pass
            self._robots[base] = rp
        rp = self._robots[base]
        return rp is None or rp.can_fetch(HEADERS["User-Agent"], url)

    def get(self, url: str, mode: str = "requests") -> str:
        if not self.allowed(url):
            raise FetchError(f"robots.txt verbietet {url}")
        if mode == "playwright":
            return self._get_browser(url)
        last = None
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
                continue
            if r.status_code == 200:
                return r.text
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 410):  # Bot-Schutz / weg – Retry bringt nichts
                break
            time.sleep(2 ** attempt * 5)
        raise FetchError(f"{url}: {last}")

    def _get_browser(self, url: str) -> str:
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:
                raise FetchError("playwright nicht installiert (pip install playwright)") from e
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        self._wait()
        page = self._browser.new_page(locale="de-DE", user_agent=HEADERS["User-Agent"])
        try:
            page.goto(url, timeout=self.timeout * 1000, wait_until="networkidle")
            return page.content()
        except Exception as e:  # noqa: BLE001 – Playwright wirft eigene Typen
            raise FetchError(f"{url}: {e}") from e
        finally:
            page.close()

    def close(self):
        if self._browser:
            self._browser.close()
            self._pw.stop()
        self.session.close()
