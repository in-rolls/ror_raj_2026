"""Compatibility shim for the crawl that was running when the code moved to src/.

That detached ``crawl.sh`` still invokes ``uv run python fetch_plots.py``
from the script text it opened; zsh keeps reading the old inode. Delete this
file once that crawl has finished. New runs use ``rajasthan-ror-fetch``.
"""

from rajasthan_ror.plots import main

main()
