"""Build the v0 song list for the prototype: domestic CZ/SK songs from 2020 on.

1. Candidates from the IFPI domestic Top 50 charts (radio + digital, CZ + SK) — being on one of
   these charts is the evidence that the song is Czech/Slovak, so no name-based artist
   classification is needed. Only songs that first charted in 2020 or later; the top N by radio
   weeks plus the top N by digital weeks, so pop (radio) and rap (digital) are both in.
2. Resolve each to an iTunes track (store cz, then sk): title must match, artist must match,
   versions (live/remix/acoustic/…) are skipped, the earliest release wins, release year >= 2020.
3. Write site/songs.json (only what the game needs) and data/v0/review.csv (all candidates with
   the reason when a song was dropped, for manual review).

Usage: python pipeline/build_v0.py [--per-chart-type 200]
"""

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

from pilot import ROOT, fetch_json, norm

IFPI = ROOT / "data" / "ifpi" / "charts.csv"
OUT_JSON = ROOT / "site" / "songs.json"
OUT_REVIEW = ROOT / "data" / "v0" / "review.csv"

DOMESTIC = {"cz_radio_top50_cz": "cz", "sk_radio_top50_sk": "sk",
            "cz_digital_top50_cz": "cz", "sk_digital_top50_sk": "sk"}
MIN_YEAR = 2020

# IFPI joins artists with "/", "&", ",", " a ", " x ", "feat."
ARTIST_SPLIT = re.compile(r"\s*(?:/|&|,|\ba\b|\bx\b(?!\S)|\bfeat\.?|\bft\.?)\s*", re.IGNORECASE)
BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
VERSION = re.compile(r"\b(live|remix|remaster\w*|acoustic|akustick\w*|unplugged|instrumental|karaoke|"
                     r"sped up|slowed|radio edit|extended|demo|version|verzia|verze)\b", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Title without (feat. …), [Remix] etc. — used for matching and for the guess list."""
    return BRACKETS.sub("", title).split(" - ")[0].strip()


def artists_of(artist: str) -> list[str]:
    return [a for a in (norm(x) for x in ARTIST_SPLIT.split(artist)) if a]


def candidates(per_type: int) -> list[dict]:
    songs: dict[tuple, dict] = {}
    with open(IFPI, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["chart"] not in DOMESTIC or not r["pos"].isdigit():
                continue
            key = (norm(r["artist"]), norm(clean_title(r["title"])))
            s = songs.setdefault(key, {"artist": r["artist"], "title": r["title"], "first_year": 9999,
                                       "radio_weeks": 0, "digital_weeks": 0, "countries": set()})
            year = int(r["year"]) if r["year"].isdigit() else 0
            if 2006 <= year <= 2026:  # a few IFPI labels have typos in the year
                s["first_year"] = min(s["first_year"], year)
            s["radio_weeks" if "radio" in r["chart"] else "digital_weeks"] += 1
            s["countries"].add(DOMESTIC[r["chart"]])
    recent = [s for s in songs.values() if s["first_year"] >= MIN_YEAR]
    by_radio = sorted(recent, key=lambda s: -s["radio_weeks"])[:per_type]
    by_digital = sorted(recent, key=lambda s: -s["digital_weeks"])[:per_type]
    picked = {id(s): s for s in by_radio + by_digital}
    return list(picked.values())


def resolve(song: dict) -> tuple[dict | None, str]:
    """Best iTunes track for an IFPI song, or (None, reason)."""
    want_title = norm(clean_title(song["title"]))
    want_artists = artists_of(song["artist"])
    reason = "no search results"
    first_artist = want_artists[0] if want_artists else ""
    # Second attempt searches the title alone: "artist title" sometimes returns nothing.
    attempts = [(store, f"{first_artist} {clean_title(song['title'])}") for store in ("cz", "sk")]
    attempts.append(("cz", clean_title(song["title"])))
    for store, term in attempts:
        data = fetch_json("https://itunes.apple.com/search",
                          {"term": term, "country": store, "entity": "song", "limit": 50})
        matches = []
        for t in data.get("results", []):
            if norm(clean_title(t.get("trackName", ""))) != want_title:
                continue
            artist = norm(t.get("artistName", ""))
            if not any(a in artist or artist in a for a in want_artists):
                continue
            if VERSION.search(t.get("trackName", "")) or not t.get("previewUrl"):
                continue
            matches.append(t)
        if not matches:
            reason = f"no title+artist match ({len(data.get('results', []))} results)"
            continue
        best = min(matches, key=lambda t: t.get("releaseDate", "9999"))
        if int(best.get("releaseDate", "0000")[:4]) < MIN_YEAR:
            return None, f"released {best.get('releaseDate', '')[:4]} (before {MIN_YEAR})"
        best["_store"] = store
        return best, "ok"
    return None, reason


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-chart-type", type=int, default=200)
    args = parser.parse_args()

    cands = candidates(args.per_chart_type)
    print(f"{len(cands)} candidates", flush=True)
    games, review, seen_ids = [], [], set()
    for i, s in enumerate(cands, 1):
        track, reason = resolve(s)
        if track and track["trackId"] in seen_ids:
            track, reason = None, "duplicate of another candidate"
        review.append({"ifpi_artist": s["artist"], "ifpi_title": s["title"], "first_year": s["first_year"],
                       "radio_weeks": s["radio_weeks"], "digital_weeks": s["digital_weeks"],
                       "status": reason, "itunes_id": track["trackId"] if track else "",
                       "itunes_artist": track["artistName"] if track else "",
                       "itunes_title": track["trackName"] if track else "",
                       "album": track.get("collectionName", "") if track else "",
                       "release": track.get("releaseDate", "")[:10] if track else ""})
        if track:
            seen_ids.add(track["trackId"])
            games.append({
                "id": track["trackId"],
                "artist": track["artistName"],
                "title": clean_title(track["trackName"]),
                "year": int(track["releaseDate"][:4]),
                "store": track["_store"],
                "cover": track.get("artworkUrl100", "").replace("100x100bb", "300x300bb"),
                "url": track.get("trackViewUrl", ""),
            })
        if i % 25 == 0:
            print(f"  {i}/{len(cands)} resolved, {len(games)} playable", flush=True)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(games, ensure_ascii=False, indent=0), encoding="utf-8")
    OUT_REVIEW.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_REVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(review[0]))
        w.writeheader()
        w.writerows(review)
    print(f"{len(games)} playable songs → {OUT_JSON.relative_to(ROOT)}")
    print("dropped:", Counter(r["status"].split(" (")[0] for r in review if r["status"] != "ok"))


if __name__ == "__main__":
    main()
