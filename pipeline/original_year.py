"""
Original release year check for older songs, against Wikidata.

The catalogue year is the earliest release found on iTunes or Spotify. For older songs that trusts wrong compilation
dates (Gott & Charlotte "Srdce nehasnou", a 2019 duet, came out as 1995). This script asks Wikidata, per lead artist,
for works they perform (P175) with a publication date (P577) and compares them with our year by normalized title.

Output: catalog/song_year_proposal.csv — every song before --before with a Wikidata match where the years differ.
It is a proposal: Wikidata is wrong too at times, so each row is checked (Wikipedia) and the decision goes to
catalog/song_year.csv, which catalog.py applies.

    python pipeline/original_year.py [--before 2010]
"""
import argparse
import csv
import json
from collections import defaultdict

from catalog import CATALOG, ITUNES_ARTIST_SPLIT, ROOT, clean_title
from pilot import norm, sparql

SONGS = ROOT / "site" / "songs.json"
OUT = CATALOG / "song_year_proposal.csv"

QUERY = """SELECT ?w ?wLabel ?date ?type WHERE {
  VALUES ?name { %s }
  ?a rdfs:label ?name .
  ?w wdt:P175 ?a ; wdt:P577 ?date .
  OPTIONAL { ?w wdt:P31 ?type . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "cs,sk,en". }
}"""
ALBUM = {"http://www.wikidata.org/entity/Q482994", "http://www.wikidata.org/entity/Q169930",
         "http://www.wikidata.org/entity/Q209939", "http://www.wikidata.org/entity/Q222910"}  # album, EP, live, compilation


def works(artist: str) -> dict[str, dict]:
    """Normalized title → {year, qid, album} of the earliest dated work by this artist on Wikidata."""
    names = " ".join(f'"{artist}"@{lang}' for lang in ("cs", "sk", "en"))
    out: dict[str, dict] = {}
    for r in sparql(QUERY % names):
        title = norm(clean_title(r["wLabel"]["value"]))
        year = int(r["date"]["value"][:4])
        album = r.get("type", {}).get("value") in ALBUM
        if title and (title not in out or year < out[title]["year"]):
            out[title] = {"year": year, "qid": r["w"]["value"].rsplit("/", 1)[-1], "album": album}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=int, default=2010)
    args = ap.parse_args()
    songs = [s for s in json.loads(SONGS.read_text(encoding="utf-8")) if (s["year"] or 0) < args.before]
    by_artist = defaultdict(list)
    for s in songs:
        by_artist[ITUNES_ARTIST_SPLIT.split(s["artist"])[0]].append(s)
    rows, found = [], 0
    for i, (artist, items) in enumerate(sorted(by_artist.items()), 1):
        known = works(artist)
        for s in items:
            w = known.get(norm(clean_title(s["title"])))
            if not w:
                continue
            found += 1
            if w["year"] != s["year"]:
                rows.append({"key": s["key"], "artist": s["artist"], "title": s["title"], "our_year": s["year"] or "",
                             "wikidata_year": w["year"], "wikidata": w["qid"], "is_album": w["album"]})
        if i % 25 == 0:
            print(f"  {i}/{len(by_artist)} artists, {found} songs on Wikidata, {len(rows)} differ", flush=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "artist", "title", "our_year", "wikidata_year", "wikidata", "is_album"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(songs)} songs before {args.before}, {found} found on Wikidata, {len(rows)} differ → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
