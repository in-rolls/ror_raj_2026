"""Where the crawl keeps its files.

Everything lives under ``raw/`` in the *current working directory*, not next
to the package: the package is installed into a virtualenv, while the crawl
is run from the repository root (``crawl.sh`` does ``cd`` there first) and
its checkpoints must survive reinstalls. Run the console scripts from the
directory that should hold ``raw/``.
"""

from pathlib import Path

RAW_DIR = Path.cwd() / "raw"
LOCATIONS_DIR = RAW_DIR / "locations"
VILLAGES_FILE = RAW_DIR / "villages.parquet"
PLOTS_DIR = RAW_DIR / "plots"
OWNERS_FILE = RAW_DIR / "owners.parquet"
