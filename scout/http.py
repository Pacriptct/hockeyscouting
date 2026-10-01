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
            if 400 <= r.status_code < 500:
                r.raise_for_status()  # our fault, retrying won't help
            r.raise_for_status()
            time.sleep(1)  # be polite to small league sites
            return r
        except requests.RequestException as e:
            last = e
            resp = getattr(e, "response", None)
            if resp is not None and 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise
            time.sleep(2 ** (i + 1))
    raise last

