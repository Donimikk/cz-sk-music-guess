"""
Language (CZ / SK) of artists with no IFPI domestic-chart rows, from the language of their song titles.

catalog.py takes the language from the CZ vs SK IFPI chart rows; artists that never charted there end up "unknown".
For those, this script reads the lead artist's Spotify discography (release titles, spotifyscraper) and counts
Czech and Slovak
markers in the titles: letters that exist in only one of the languages (ř ů ě vs ä ô ĺ ľ ŕ) and common words that
differ (jsem/som, proč/prečo…). The majority wins (user 2026-10-25). With --write the result goes to
catalog/artist_country.csv, where it can be corrected by hand like any other row.

    .venv/Scripts/python pipeline/title_language.py            # print the proposal
    .venv/Scripts/python pipeline/title_language.py --write    # append it to catalog/artist_country.csv
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date

from spotify_scraper import CacheConfig, FileCache, RateLimit, SpotifyClient

from catalog import ARTIST_COUNTRY, ITUNES_ARTIST_SPLIT, ROOT, norm
from spotify import CACHE

SONGS = ROOT / "site" / "songs.json"
CZ_CHARS, SK_CHARS = set("řůě"), set("äôĺľŕ")
CZ_WORDS = {"jsem", "jsi", "jsme", "jste", "jako", "co", "proč", "ještě", "nejsem", "mě", "tě", "když", "jenom", "jen",
            "taky", "všechno", "všichni", "den", "holka", "kluk", "tady", "bejt", "není", "jsou", "nebo", "moje", "tvoje"}
SK_WORDS = {"som", "sme", "ste", "ako", "čo", "prečo", "ešte", "nie", "nesom", "ma", "ťa", "keď", "iba", "len", "tiež",
            "všetko", "všetci", "deň", "dievča", "chalan", "tu", "byť", "aj", "sú", "alebo", "moja", "tvoja", "mám"}


def score(title: str) -> tuple[int, int]:
    """(Czech markers, Slovak markers) in one title; a distinct word counts double a letter."""
    t = title.lower()
    words = set(re.findall(r"[^\W\d_]+", t))
    cz = sum(c in CZ_CHARS for c in t) + 2 * len(words & CZ_WORDS)
    sk = sum(c in SK_CHARS for c in t) + 2 * len(words & SK_WORDS)
    return cz, sk


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    unknown = defaultdict(list)  # normalized lead → songs
    for s in json.loads(SONGS.read_text(encoding="utf-8")):
        if s["country"] == "unknown":
            unknown[norm(ITUNES_ARTIST_SPLIT.split(s["artist"])[0])].append(s)

    rows = []
    cache = CacheConfig(store=FileCache(CACHE), ttl_seconds=30 * 86400)
    client = SpotifyClient(cache=cache, rate_limit=RateLimit(per_second=2))
    for lead, songs in sorted(unknown.items()):
        s = songs[0]
        # first exact-name hit; a namesake would only add foreign titles, which score neither CZ nor SK
        hit = next((a for a in client.search(lead, types=("artist",), limit=10).artists if norm(a.name) == lead), None)
        titles = {r.name for r in client.get_discography(hit.id)} if hit else set()
        titles |= {x["title"] for x in songs}
        cz = sk = 0
        for t in titles:
            c, k = score(t)
            cz, sk = cz + c, sk + k
        country = "cz" if cz > sk else "sk" if sk > cz else ""
        name = ITUNES_ARTIST_SPLIT.split(s["artist"])[0]
        print(f"{name:28} titles {len(titles):3}  CZ {cz:3} / SK {sk:3}  -> {country or 'undecided'}")
        if country:
            rows.append({"artist": name, "country": country,
                         "note": f"title language CZ {cz} / SK {sk} in {len(titles)} titles ({date.today()})"})

    if args.write and rows:
        with open(ARTIST_COUNTRY, "a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=["artist", "country", "note"]).writerows(rows)
        print(f"appended {len(rows)} rows to {ARTIST_COUNTRY}")


if __name__ == "__main__":
    main()
