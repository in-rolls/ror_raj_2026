"""Thin client for the Rajasthan Bhu-Naksha portal.

The portal is the map front-end to the state's land records. Its REST layer,
unlike the Apna Khata nakal page, asks for no login, no OTP and no captcha:
the "captcha" on the map's Nakal button is generated and checked in browser
JavaScript and is never sent to the server.

Two calls matter.

``ListsAfterLevel`` walks a six-level tree: district, tehsil, RI circle,
halka, village, sheet. Codes are passed back as a comma-separated string with
a trailing comma, exactly as the page builds it; without the trailing comma
the call returns empty lists.

``getPlotInfo`` takes a *giscode*, which is the six level codes concatenated
(``01`` + ``002`` + ``0745`` + ``02920`` + ``11035`` + ``001``), and a plot
number, and returns one JSON object whose ``info`` field holds the khata
number and one line per co-owner: name, father or husband, share, ``जाति-``
and residence. A plot that does not exist answers with an empty body and
HTTP 200 (occasionally 204), not an error, so "miss" is a first-class
outcome here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import requests

BASE = "https://bhunaksha.rajasthan.gov.in/"
STATE = "08"
LEVELS = 6
LEVEL_LABELS = ("district", "tehsil", "ri", "halka", "village", "sheet")

log = logging.getLogger("bhunaksha")


class PortalError(RuntimeError):
    """The portal answered, but not with what the call asked for."""


@dataclass(frozen=True)
class Level:
    code: str
    name: str
    has_data: bool


class Session:
    """One HTTP session against the portal, with bounded retries.

    ``pause`` is the minimum gap between requests. The portal has shown no
    429s, but latency swings from under a second to several, and a crawl that
    hammers it is a crawl that gets blocked.
    """

    def __init__(self, pause: float = 0.2, timeout: float = 60.0, retries: int = 4):
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (research crawler; contact in README)",
                "Referer": BASE + "Viewmap/",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.pause = pause
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        self.http.get(BASE + "Viewmap/", timeout=timeout)

    def _post(self, path: str, **data) -> str:
        err: Exception | None = None
        for attempt in range(self.retries):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.http.post(BASE + path, data=data, timeout=self.timeout)
                self._last = time.monotonic()
                if r.status_code in (200, 204):
                    return r.text
                err = PortalError(f"{path}: HTTP {r.status_code}")
            except requests.RequestException as e:
                err = e
            time.sleep(2**attempt)
        raise PortalError(f"{path}: gave up after {self.retries} tries: {err}")

    def children(self, codes: list[str]) -> list[Level]:
        """Entries one level below ``codes`` (empty list means the districts)."""
        level = len(codes)
        if level >= LEVELS:
            raise ValueError("already at sheet level")
        joined = "".join(c + "," for c in codes)
        text = self._post(
            "rest/Levels/ListsAfterLevel", state=STATE, level=level, codes=joined, hasmap="true"
        )
        data = json.loads(text)
        if not isinstance(data, list) or not data:
            raise PortalError(f"ListsAfterLevel({joined}): unexpected {text[:120]!r}")
        out = []
        for e in data[0]:
            extra = {
                x["key"]["value"]: x["value"]["value"]
                for x in e.get("extraParms", {}).get("entry", [])
            }
            out.append(Level(str(e["code"]), str(e["value"]), extra.get("hasData", "Y") == "Y"))
        return out

    def plot_info(self, giscode: str, plotno: str) -> dict | None:
        """The record for one plot, or None when the portal says there is none."""
        text = self._post(
            "rest/MapInfo/getPlotInfo",
            state=STATE,
            giscode=giscode,
            plotno=plotno,
            srcpage="viewmap",
        )
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise PortalError(f"getPlotInfo({giscode},{plotno}): not JSON: {text[:120]!r}") from e

    def extent(self, giscode: str) -> dict:
        text = self._post("rest/MapInfo/getVVVVExtentGeoref", state=STATE, giscode=giscode)
        return json.loads(text)


def giscode(codes: list[str]) -> str:
    if len(codes) != LEVELS:
        raise ValueError(f"need {LEVELS} codes, got {len(codes)}")
    return "".join(codes)
