"""Map a jati string as the patwari wrote it to a Rajasthan schedule category.

The schedules (``schedules/rajasthan_schedules.json``, shipped with the
package) are in Roman script, as the Social Justice department publishes
them; the records are in Devanagari, spelled by hand. Matching goes through a
phonetic key: Devanagari is transliterated (Harvard-Kyoto), the inherent
vowel and aspiration are dropped, long and short vowels merged, ``w`` and
``v`` merged, doubled letters collapsed. ``मेघवाल`` and ``Meghwal`` both key
to ``megvl``; ``जाट`` and ``Jat`` to ``jt``; ``Jatia`` stays ``jti``.

A record's caste field can name several communities at once
(``मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)``) or carry a religion
(``लोहार मुसलमान``). The string is split on brackets, commas and hyphens and
every piece is tried; the first schedule hit wins, in the order SC, ST, MBC,
OBC, because a community that appears in two lists is in the earlier one by
a narrower definition (Dholi Bhil is ST, Dholi is SC). A string that hits no
schedule is labelled ``unlisted``, which is where Rajput, Brahmin, Bania and
Jain sit, and is *not* the same as General: an unlisted string may be a
misspelling the key did not bridge.

Usage as a library:
``categorise("मेघवाल") -> ("SC", "Megh, Meghval, Meghwal, Menghvar", "exact")``.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Any, TypedDict

from indic_transliteration import sanscript

SCHEDULES_RESOURCE = files("rajasthan_ror") / "schedules" / "rajasthan_schedules.json"
SCHEMA_VERSION = 1
CATEGORY_ORDER = ("SC", "ST", "MBC", "OBC")
ENTRY_KEYS = {
    "category": str,
    "entry_no": int,
    "entry": str,
    "synonym": str,
    "source": str,
}
# \u2013 is the en dash, which the records use as a hyphen.
SPLIT = re.compile(r"[()\[\],/\-\u2013]|\s+(?:व|एवं|और|तथा)\s+")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class ScheduleEntry(TypedDict):
    """One synonym row of a schedule.

    Attributes:
        category: ``SC``, ``ST``, ``MBC`` or ``OBC``.
        entry_no: The entry's serial number in that schedule.
        entry: The entry as published, all its names in one string.
        synonym: One name from ``entry``, the unit matching works on.
        source: Where the schedule was taken from.
    """

    category: str
    entry_no: int
    entry: str
    synonym: str
    source: str


def key(text: str) -> str:
    """Phonetic key shared by a Devanagari spelling and its Roman schedule entry.

    The inherent vowel ``a`` goes (it is what Roman spellings drop: ``jATa``
    and ``Jat``); the other vowels stay, with long and short merged, because
    without them ``Jat`` and ``Jatia`` or ``Bhil`` and ``Balai`` collide.

    Args:
        text: A caste name in Devanagari or Roman script.

    Returns:
        The key; empty when nothing alphabetic survives.
    """
    if DEVANAGARI.search(text):
        text = sanscript.transliterate(text, sanscript.DEVANAGARI, sanscript.HK)
    reduced = text.lower()
    reduced = re.sub(r"[^a-z]", "", reduced)
    for long_form, short_form in (
        ("ai", "e"),
        ("au", "o"),
        ("ee", "i"),
        ("oo", "u"),
        ("w", "v"),
        ("z", "j"),
    ):
        reduced = reduced.replace(long_form, short_form)
    reduced = re.sub(r"(?<=[bcdgjkptr])h", "", reduced)
    reduced = reduced.replace("sh", "s")
    reduced = reduced.replace("a", "")
    return re.sub(r"(.)\1+", r"\1", reduced)


def _validated_entry(entry: Any, position: int) -> ScheduleEntry:
    """Check one JSON entry against the schema.

    Args:
        entry: The parsed JSON value.
        position: Its index in ``entries``, for the error message.

    Returns:
        The entry, typed.

    Raises:
        ValueError: When a key is missing, mistyped, empty, or the category
            is not one of the four schedules.
    """
    if not isinstance(entry, dict) or set(entry) != set(ENTRY_KEYS):
        raise ValueError(f"entry {position}: keys must be {sorted(ENTRY_KEYS)}")
    for name, expected_type in ENTRY_KEYS.items():
        value = entry[name]
        if type(value) is not expected_type:
            raise ValueError(
                f"entry {position}: {name} must be {expected_type.__name__}"
            )
        if expected_type is str and not value.strip():
            raise ValueError(f"entry {position}: {name} is empty")
    if entry["category"] not in CATEGORY_ORDER:
        raise ValueError(
            f"entry {position}: category {entry['category']!r} not in {CATEGORY_ORDER}"
        )
    if entry["entry_no"] < 1:
        raise ValueError(f"entry {position}: entry_no must be positive")
    return ScheduleEntry(**entry)


@lru_cache(maxsize=1)
def schedule_entries() -> tuple[ScheduleEntry, ...]:
    """Load and validate the packaged schedules.

    Returns:
        Every synonym row, in file order.

    Raises:
        ValueError: When the file's schema version is not
            :data:`SCHEMA_VERSION`, an entry fails :func:`_validated_entry`,
            or a ``(category, entry_no, synonym)`` triple repeats.
    """
    document = json.loads(SCHEDULES_RESOURCE.read_text(encoding="utf-8"))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"schedules: schema_version {document.get('schema_version')!r}, "
            f"expected {SCHEMA_VERSION}"
        )
    entries = tuple(
        _validated_entry(entry, position)
        for position, entry in enumerate(document.get("entries", []))
    )
    if not entries:
        raise ValueError("schedules: no entries")
    seen: set[tuple[str, int, str]] = set()
    for entry in entries:
        triple = (entry["category"], entry["entry_no"], entry["synonym"])
        if triple in seen:
            raise ValueError(f"schedules: duplicate {triple}")
        seen.add(triple)
    return entries


@lru_cache(maxsize=1)
def schedules() -> dict[str, list[tuple[str, str]]]:
    """Index the schedules by phonetic key.

    Returns:
        Key to ``[(category, entry)]``, each list in schedule order so the
        narrowest listing comes first.
    """
    table: dict[str, list[tuple[str, str]]] = {}
    for row in schedule_entries():
        synonym_key = key(row["synonym"])
        listing = (row["category"], row["entry"])
        if synonym_key and listing not in table.setdefault(synonym_key, []):
            table[synonym_key].append(listing)
    for listings in table.values():
        listings.sort(key=lambda listing: CATEGORY_ORDER.index(listing[0]))
    return table


def pieces(jati: str) -> list[str]:
    """Split a caste string into the community names it carries.

    Args:
        jati: The caste field as written.

    Returns:
        Non-empty pieces, split on brackets, commas, slashes, hyphens and
        the Hindi conjunctions.
    """
    return [piece.strip() for piece in SPLIT.split(jati) if piece and piece.strip()]


def categorise(
    jati: str | None, fuzzy_cutoff: float = 0.9
) -> tuple[str, str | None, str]:
    """Look up one caste string in the schedules.

    Args:
        jati: The caste field as written; ``None`` or blank is unlisted.
        fuzzy_cutoff: :func:`difflib.get_close_matches` ratio a key must
            reach to count as a near miss. Keys shorter than five letters are
            never fuzzy-matched.

    Returns:
        Category (``SC``, ``ST``, ``MBC``, ``OBC`` or ``unlisted``), the
        schedule entry matched or ``None``, and how: ``exact`` for a key
        match, ``fuzzy`` for a close key, ``none`` when nothing in the
        schedules is near it.
    """
    if not isinstance(jati, str) or not jati.strip():
        return "unlisted", None, "none"
    table = schedules()
    keys = list(table)
    best: tuple[int, str, str, str] | None = None
    for piece in pieces(jati):
        piece_key = key(piece)
        if not piece_key:
            continue
        if piece_key in table:
            category, entry = table[piece_key][0]
            candidate = (CATEGORY_ORDER.index(category), category, entry, "exact")
        else:
            near = difflib.get_close_matches(piece_key, keys, n=1, cutoff=fuzzy_cutoff)
            if not near or len(piece_key) < 5:
                continue
            category, entry = table[near[0]][0]
            candidate = (CATEGORY_ORDER.index(category), category, entry, "fuzzy")
        if (
            best is None
            or candidate[0] < best[0]
            or (candidate[0] == best[0] and candidate[3] == "exact")
        ):
            best = candidate
    if best is None:
        return "unlisted", None, "none"
    return best[1], best[2], best[3]
