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
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

BASE = "https://bhunaksha.rajasthan.gov.in/"
STATE = "08"
LEVELS = 6
LEVEL_LABELS = ("district", "tehsil", "ri", "halka", "village", "sheet")

log = logging.getLogger(__name__)

# The portal's name stopped resolving for seven hours on 8 Sept 2026 while the
# host itself, once reached, answered normally. A crawl that gives up in
# fifteen seconds loses the whole window; one that waits it out loses nothing.
CONNECT_BACKOFF_START = 5.0
CONNECT_BACKOFF_CAP = 600.0
CONNECT_BACKOFF_TOTAL = 7200.0


class PortalError(RuntimeError):
    """The portal answered, but not with what the call asked for."""


@dataclass(frozen=True)
class Level:
    """One entry in the portal's location tree.

    Attributes:
        code: The portal's code for this entry, zero-padded as it sends it.
        name: The Devanagari name shown in the map's drop-down.
        has_data: Whether the portal reports a map (and so plot records)
            beneath this entry.
    """

    code: str
    name: str
    has_data: bool


def with_connection_backoff[T](
    call: Callable[[], T],
    *,
    what: str,
    sleep: Callable[[float], None] = time.sleep,
    total: float = CONNECT_BACKOFF_TOTAL,
) -> T:
    """Run ``call`` until it stops failing at the connection level.

    Name resolution, connect and read failures are retried with a delay that
    doubles from :data:`CONNECT_BACKOFF_START` to :data:`CONNECT_BACKOFF_CAP`
    for up to ``total`` seconds. Anything the portal actually answers is not
    a connection failure and is returned to the caller as is.

    Args:
        call: The request to make.
        what: Label for the log.
        sleep: Sleep function; tests pass a recorder.
        total: Seconds of waiting after which to give up.

    Returns:
        Whatever ``call`` returns once it succeeds.

    Raises:
        PortalError: When ``total`` seconds of retrying did not get through.
    """
    delay, waited, attempt = CONNECT_BACKOFF_START, 0.0, 0
    while True:
        try:
            return call()
        except (requests.ConnectionError, requests.Timeout) as exc:
            attempt += 1
            if waited >= total:
                raise PortalError(
                    f"{what}: unreachable for {waited:.0f}s: {exc}"
                ) from exc
            if attempt > 3:
                log.warning(
                    "%s: attempt %d failed (%s); retrying in %.0fs",
                    what,
                    attempt,
                    exc,
                    delay,
                )
            sleep(delay)
            waited += delay
            delay = min(delay * 2, CONNECT_BACKOFF_CAP)


class Session:
    """One HTTP session against the portal, with bounded retries.

    The portal has shown no 429s, but latency swings from under a second to
    several, and a crawl that hammers it is a crawl that gets blocked. A
    connection-level failure is waited out (see
    :func:`with_connection_backoff`); an answer the portal gives that is not
    what was asked for is retried ``retries`` times and then raised.
    """

    def __init__(
        self, pause: float = 0.2, timeout: float = 60.0, retries: int = 4
    ) -> None:
        """Open the session and fetch the map page once to collect cookies.

        Args:
            pause: Minimum gap in seconds between two requests.
            timeout: Per-request timeout in seconds.
            retries: Attempts per call before giving up, with exponential
                back-off between them.
        """
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
        self._last_request_at = 0.0
        with_connection_backoff(
            lambda: self.http.get(BASE + "Viewmap/", timeout=timeout),
            what="GET Viewmap/",
        )

    def _post(self, path: str, **form: str | int) -> str:
        """POST a form to ``path`` and return the body, retrying on failure.

        Args:
            path: Path under :data:`BASE`.
            **form: Form fields.

        Returns:
            The response body; empty for a 204.

        Raises:
            PortalError: After ``retries`` unexpected answers, or once the
                connection back-off is exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(self.retries):
            wait = self.pause - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            response = with_connection_backoff(
                lambda: self.http.post(BASE + path, data=form, timeout=self.timeout),
                what=f"POST {path}",
            )
            self._last_request_at = time.monotonic()
            if response.status_code in (200, 204):
                return response.text
            last_error = PortalError(f"{path}: HTTP {response.status_code}")
            time.sleep(2**attempt)
        raise PortalError(f"{path}: gave up after {self.retries} tries: {last_error}")

    def children(self, codes: list[str]) -> list[Level]:
        """List the entries one level below ``codes``.

        Args:
            codes: Level codes from the district down; empty for the
                districts themselves.

        Returns:
            The child entries in the portal's order.

        Raises:
            ValueError: When ``codes`` already reaches the sheet level.
            PortalError: When the portal's answer is not a non-empty list.
        """
        level = len(codes)
        if level >= LEVELS:
            raise ValueError("already at sheet level")
        joined = "".join(code + "," for code in codes)
        text = self._post(
            "rest/Levels/ListsAfterLevel",
            state=STATE,
            level=level,
            codes=joined,
            hasmap="true",
        )
        payload = json.loads(text)
        if not isinstance(payload, list) or not payload:
            raise PortalError(f"ListsAfterLevel({joined}): unexpected {text[:120]!r}")
        children: list[Level] = []
        for entry in payload[0]:
            extra = {
                item["key"]["value"]: item["value"]["value"]
                for item in entry.get("extraParms", {}).get("entry", [])
            }
            children.append(
                Level(
                    str(entry["code"]),
                    str(entry["value"]),
                    extra.get("hasData", "Y") == "Y",
                )
            )
        return children

    def plot_info(self, giscode: str, plotno: str) -> dict[str, Any] | None:
        """Fetch the record for one plot.

        Args:
            giscode: The six level codes concatenated; see :func:`giscode`.
            plotno: Plot (khasra) number as printed on the map.

        Returns:
            The portal's JSON object, or ``None`` when the portal says there
            is no such plot.

        Raises:
            PortalError: When the body is neither empty nor JSON.
        """
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
        except json.JSONDecodeError as exc:
            raise PortalError(
                f"getPlotInfo({giscode},{plotno}): not JSON: {text[:120]!r}"
            ) from exc

    def extent(self, giscode: str) -> dict[str, Any]:
        """Fetch the georeferenced bounding box of one sheet.

        Args:
            giscode: The six level codes concatenated.

        Returns:
            The portal's JSON object describing the extent.
        """
        text = self._post(
            "rest/MapInfo/getVVVVExtentGeoref", state=STATE, giscode=giscode
        )
        return json.loads(text)


def giscode(codes: list[str]) -> str:
    """Build the sheet key ``getPlotInfo`` wants from the six level codes.

    Args:
        codes: District, tehsil, RI circle, halka, village and sheet codes.

    Returns:
        The codes concatenated in that order.

    Raises:
        ValueError: When there are not exactly six codes.
    """
    if len(codes) != LEVELS:
        raise ValueError(f"need {LEVELS} codes, got {len(codes)}")
    return "".join(codes)
