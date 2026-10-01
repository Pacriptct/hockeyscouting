"""Shared HTTP helpers."""

from __future__ import annotations

import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_session = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return _session


def get(url: str, *, params=None, tries: int = 3, timeout: int = 30) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = session().get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} from {url}")
            r.raise_for_status()
            time.sleep(1)  # be polite to small league sites
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** (i + 1))
    raise last


def render(url: str, wait_ms: int = 4000) -> str:
    """Load a page in headless Chromium and return the HTML after JS runs."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        browser.close()
    return html
