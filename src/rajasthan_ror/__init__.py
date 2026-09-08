"""Rajasthan land records (jamabandi) via Bhu-Naksha.

Khatedar name, father, jati and residence per plot.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rajasthan-ror")
except PackageNotFoundError:  # pragma: no cover - not installed
    __version__ = "0.0.0"
