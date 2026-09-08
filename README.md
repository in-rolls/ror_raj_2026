# rajasthan-ror

[![PyPI](https://img.shields.io/pypi/v/rajasthan-ror)](https://pypi.org/project/rajasthan-ror/)
[![CI](https://github.com/in-rolls/rajasthan-ror/actions/workflows/ci.yml/badge.svg)](https://github.com/in-rolls/rajasthan-ror/actions/workflows/ci.yml)
[![Docs](https://github.com/in-rolls/rajasthan-ror/actions/workflows/docs.yml/badge.svg)](https://in-rolls.github.io/rajasthan-ror/)
[![Python](https://img.shields.io/pypi/pyversions/rajasthan-ror)](https://pypi.org/project/rajasthan-ror/)

Khatedar name, father's or husband's name, **jati** and residence for every
integer-numbered plot on Rajasthan's digitised cadastre, scraped from the
state's Bhu-Naksha map portal at <https://bhunaksha.rajasthan.gov.in>. Built
to give a name-to-caste corpus for a state that has no caste census and whose
public caste-linked name data otherwise stops at SC / ST / OBC / General.

## What it is

Rajasthan's jamabandi, the record of rights for one khata, has recorded the
tenant's caste since the Land Records Rules of 1957 put "name, parentage,
caste, residence and class of tenancy" in column 5. The nakal still prints it:

> काश्तकार का नाम:- लालाराम पुत्र मोहनराम **जाति** हरिजन सा. देह खातेदार

Lalaram son of Mohanram, caste Harijan, resident of this village, khatedar.

The nakal page itself (Apna Khata) now asks for a mobile number and an SMS
OTP for every copy. The map portal that sits on the same database does not.
Its `getPlotInfo` call returns, for one plot on one sheet, the khata number
and one line per co-owner:

> 1.) गुलाब सिंह चीता पुत्र अमर सिंह   हिस्सा- 2/3 जाति- मेर(मेहरात काठात,मेहरात-घोड़ात, चीता) सा. अजयसर खातेदार
> 2.) सुगरा पत्नि खंगार सिंह   हिस्सा- 1/3 जाति- मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)  सा देह खातेदार

Name, relation and relative, share, caste, residence, tenure, in one
free-text cell behind `पुत्र` / `पत्नि` / `हिस्सा-` / `जाति-` / `सा.` markers,
so the parse is a marker scan rather than a column read.

## What it measures, and what it cannot

**The caste is whatever the patwari wrote.** It is a jati string, not a
schedule entry: `मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)` and `चीता` are the
same community written two ways, `लोहार मुसलमान` marks religion, `हरिजन`
is a label no schedule uses. Folding these into official categories needs a
Rajasthan alias table, which is downstream of this repo.

**Landholders only.** A household with no khata is not here, and the landless
are disproportionately Scheduled Caste. Any caste rate computed from these
records describes owners of agricultural land, not the population.

**Integer plot numbers only.** Subdivided khasras are written `1256/287` on
the nakal and the portal has not answered to that form under any spelling
tried. The crawl walks integers and stops after a run of misses, so a sheet's
slashed plots are missing. In the one sheet walked end to end, 283 of the
first 300 integers existed.

## Handling

- **Codes go back with a trailing comma.** `ListsAfterLevel` takes the
  selected level codes as `01,002,0745,` and returns empty lists without the
  trailing comma. The giscode for a sheet is those six codes concatenated.
- **A missing plot is HTTP 200 with an empty body**, occasionally a 204. The
  client returns `None` for both; neither is an error.
- **The captcha is decorative.** The map's Nakal button generates a six-
  character code in JavaScript and compares the user's typing against it in
  the same function; nothing reaches the server.
- **The server serialises.** One session sees 0.5 to 3 seconds per request;
  eight parallel sessions together reach about 1.5 requests a second, so
  workers do not multiply throughput. Every hit lists the other plots on the
  same khata (`ownerplots`), and those are recorded as `via` the plot that
  named them rather than fetched, which brings the request count down from
  plots to roughly khatas.

The crawl is resumable per sheet. A plot number that yields nothing is
written with `ok` false so the tail of a sheet can be told from a gap, and a
sheet is only marked `done` after the miss run.

## Provenance and vintage

Public portal, no authentication, no rate limit observed beyond the
serialisation above. The records are the live jamabandi as of the fetch date
recorded on every row, so mutations made after that date are not reflected.

## Run

```
uv sync --all-groups
uv run rajasthan-ror-list                              # raw/villages.parquet
uv run rajasthan-ror-fetch --districts 21 --workers 6
uv run rajasthan-ror-parse                             # raw/owners.parquet
uv run pytest -q
```

`raw/` is created under the directory the commands are run from, so run
them from the repository root (`crawl.sh` does). `raw/` is not committed.
The scraper is published; the records are not.

The schedule synonyms the categoriser matches against ship with the package
as `src/rajasthan_ror/schedules/rajasthan_schedules.json`, one entry per
published synonym with its category, entry number and source.

## Pilot: do khatedars join to ration cards?

`scripts/pilot/pilot_join.py` matches the six largest Nagaur villages that share a
name between this portal and the state's ration-card list, and joins each
khatedar to the card *members* of the same village on a phonetic key of name
and father's name. On 5,909 khatedars fetched so far: 24% join, sons 34%,
wives and daughters under 5% because the khata lists a woman under her father
and the card under her husband; 11% of joined keys hit more than one card, so
the key is a candidate, not an identity. `scripts/pilot/diag_pairs.py` prints the
matched pairs for eyeballing and `scripts/pilot/diag_father.py` the near-misses.
The join needs the ration data in `../milaan_raj`, which is not public.
