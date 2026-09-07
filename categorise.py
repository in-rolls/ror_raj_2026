"""Map a jati string as the patwari wrote it to a Rajasthan schedule category.

The schedules (``schedules/rajasthan_schedules.tsv``) are in Roman script,
as the Social Justice department publishes them; the records are in
Devanagari, spelled by hand. Matching goes through a phonetic key: Devanagari
is transliterated (Harvard-Kyoto), the inherent vowel and aspiration are
dropped, long and short vowels merged, ``w`` and ``v`` merged, doubled
letters collapsed. ``मेघवाल`` and ``Meghwal`` both key to ``megvl``; ``जाट``
and ``Jat`` to ``jt``; ``Jatia`` stays ``jti``.

A record's caste field can name several communities at once
(``मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)``) or carry a religion
(``लोहार मुसलमान``). The string is split on brackets, commas and hyphens and
every piece is tried; the first schedule hit wins, in the order SC, ST, MBC,
OBC, because a community that appears in two lists is in the earlier one by
a narrower definition (Dholi Bhil is ST, Dholi is SC). A string that hits no
schedule is labelled ``unlisted``, which is where Rajput, Brahmin, Bania and
Jain sit, and is *not* the same as General: an unlisted string may be a
misspelling the key did not bridge.

Usage as a library: ``categorise("मेघवाल") -> ("SC", "Megh, Meghval, Meghwal, Menghvar", "exact")``.
"""

from __future__ import annotations

import csv
import difflib
import re
from functools import lru_cache
from pathlib import Path

from indic_transliteration import sanscript

HERE = Path(__file__).resolve().parent
SCHEDULES = HERE / "schedules" / "rajasthan_schedules.tsv"
ORDER = ("SC", "ST", "MBC", "OBC")
SPLIT = re.compile(r"[()\[\],/\-–]|\s+(?:व|एवं|और|तथा)\s+")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def key(text: str) -> str:
    """Phonetic key shared by a Devanagari spelling and its Roman schedule entry.

    The inherent vowel ``a`` goes (it is what Roman spellings drop: ``jATa``
    and ``Jat``); the other vowels stay, with long and short merged, because
    without them ``Jat`` and ``Jatia`` or ``Bhil`` and ``Balai`` collide.
    """
    if DEVANAGARI.search(text):
        text = sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.HK)
    t = text.lower()
    t = re.sub(r"[^a-z]", "", t)
    for a, b in (("ai", "e"), ("au", "o"), ("ee", "i"), ("oo", "u"), ("w", "v"), ("z", "j")):
        t = t.replace(a, b)
    t = re.sub(r"(?<=[bcdgjkptr])h", "", t)
    t = t.replace("sh", "s")
    t = t.replace("a", "")
    t = re.sub(r"(.)\1+", r"\1", t)
    return t


@lru_cache(maxsize=1)
def schedules() -> dict[str, list[tuple[str, str]]]:
    """key -> [(category, entry)] in schedule order."""
    table: dict[str, list[tuple[str, str]]] = {}
    with SCHEDULES.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            k = key(row["synonym"])
            if k and (row["category"], row["entry"]) not in table.setdefault(k, []):
                table[k].append((row["category"], row["entry"]))
    for k in table:
        table[k].sort(key=lambda ce: ORDER.index(ce[0]))
    return table


def pieces(jati: str) -> list[str]:
    return [p.strip() for p in SPLIT.split(jati) if p and p.strip()]


def categorise(jati: str | None, fuzzy: float = 0.9) -> tuple[str, str | None, str]:
    """(category, schedule entry, how) for one caste string.

    ``how`` is ``exact`` for a key match, ``fuzzy`` for a close key, ``none``
    when nothing in the schedules is near it.
    """
    if not isinstance(jati, str) or not jati.strip():
        return "unlisted", None, "none"
    table = schedules()
    keys = list(table)
    best: tuple[int, str, str, str] | None = None
    for piece in pieces(jati):
        k = key(piece)
        if not k:
            continue
        if k in table:
            cat, entry = table[k][0]
            cand = (ORDER.index(cat), cat, entry, "exact")
        else:
            near = difflib.get_close_matches(k, keys, n=1, cutoff=fuzzy)
            if not near or len(k) < 5:
                continue
            cat, entry = table[near[0]][0]
            cand = (ORDER.index(cat), cat, entry, "fuzzy")
        if best is None or cand[0] < best[0] or (cand[0] == best[0] and cand[3] == "exact"):
            best = cand
    if best is None:
        return "unlisted", None, "none"
    return best[1], best[2], best[3]
