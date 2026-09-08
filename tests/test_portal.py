"""Connection-level failures are waited out, portal answers are not."""

from __future__ import annotations

import pytest
import requests

from rajasthan_ror import portal
from rajasthan_ror.portal import PortalError, with_connection_backoff


def test_backoff_retries_connection_errors_then_succeeds() -> None:
    calls, slept = [], []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 5:
            raise requests.ConnectionError("name resolution failed")
        return "ok"

    assert with_connection_backoff(flaky, what="t", sleep=slept.append) == "ok"
    assert len(calls) == 5
    assert slept == [5.0, 10.0, 20.0, 40.0]


def test_backoff_caps_the_delay_and_gives_up_after_total() -> None:
    slept: list[float] = []

    def never() -> str:
        raise requests.Timeout("read timed out")

    with pytest.raises(PortalError, match="unreachable"):
        with_connection_backoff(never, what="t", sleep=slept.append, total=3000)
    assert max(slept) == portal.CONNECT_BACKOFF_CAP
    assert sum(slept) >= 3000


def test_non_connection_errors_pass_straight_through() -> None:
    def bad() -> str:
        raise ValueError("not a network problem")

    with pytest.raises(ValueError, match="network"):
        with_connection_backoff(bad, what="t", sleep=lambda _: None)
