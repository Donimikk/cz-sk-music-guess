"""Chart-based song popularity ranking.

1. Snapshot today's charts into data/charts/<date>/<source>_<country>.json:
   - Apple Music "most played" top 100 (official RSS feed), SK and CZ
   - Deezer Charts "Top Slovakia" / "Top Czech Republic" playlists (official Deezer Charts account)
2. Aggregate all snapshots taken so far into data/charts/ranking.csv and ranking.md.
   Points per chart day = 101 - position, summed over days and charts. Run it daily and
   the ranking turns into chart history (days on chart, best position).
3. Mark each song as domestic if one of its artists is in the Wikidata SK/CZ pool or in
   data/domestic_artists.csv (manual list for artists missing on Wikidata, e.g. Saul).

Usage: python pipeline/charts.py [--no-snapshot]
"""

import argparse
import csv
import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

from pilot import (COUNTRIES, GROUP_CLASSES, PERSON_OCCUPATIONS, ROOT, base_title, fetch_json, norm,
                   sparql)

CHARTS = ROOT / "data" / "charts"
MANUAL_DOMESTIC = ROOT / "data" / "domestic_artists.csv"

APPLE_RSS = "https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/100/songs.json"
DEEZER_PLAYLISTS = {"sk": 1266973701, "cz": 1266969571}

ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\bx\b|\bfeat\.?|\bft\.?|\bwith\b)\s*", re.IGNORECASE)
FEAT_IN_TITLE = re.compile(r"\s*[\(\[](?:feat|ft|with)\.?\s+([^\)\]]+)[\)\]]", re.IGNORECASE)


def snapshot_apple(country: str) -> list[dict]:
    feed = fetch_json(APPLE_RSS.format(country=country), {}, cache=False)["feed"]["results"]
    return [{"pos": i, "artist": r["artistName"], "title": r["name"], "apple_id": r["id"]}
            for i, r in enumerate(feed, 1)]


def snapshot_deezer(country: str) -> list[dict]:
    data = fetch_json(f"https://api.deezer.com/playlist/{DEEZER_PLAYLISTS[country]}/tracks",
                      {"limit": 100}, cache=False)["data"]
    # `rank` is Deezer's global popularity score for the track, not the chart position.
    return [{"pos": i, "artist": t["artist"]["name"], "title": t["title"], "deezer_id": t["id"],
             "deezer_rank": t["rank"]} for i, t in enumerate(data, 1)]


def take_snapshot(day: str) -> None:
    out = CHARTS / day
    out.mkdir(parents=True, exist_ok=True)
    for country in COUNTRIES:
        for source, fn in (("apple", snapshot_apple), ("deezer", snapshot_deezer)):
            rows = fn(country)
            (out / f"{source}_{country}.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{day} {source}_{country}: {len(rows)} songs")


def split_artists(artist: str, title: str) -> list[str]:
    names = ARTIST_SPLIT.split(artist)
    names += [n for m in FEAT_IN_TITLE.findall(title) for n in ARTIST_SPLIT.split(m)]
    return [n for n in (norm(x) for x in names) if n]


def domestic_artists() -> dict[str, str]:
    """Normalized artist name -> 'sk' / 'cz' / 'sk+cz' (Wikidata pool + manual list)."""
    known: dict[str, set] = defaultdict(set)
    for country, qid in COUNTRIES.items():
        # One query per type returning every sk/cs/en label: few requests (Wikidata answers
        # many small queries with 429) and alternative spellings match too.
        for where in (f"?a wdt:P27 wd:{qid}; wdt:P106 ?x. VALUES ?x {{ {PERSON_OCCUPATIONS} }}",
                      f"?a wdt:P495 wd:{qid}; wdt:P31 ?x. VALUES ?x {{ {GROUP_CLASSES} }}"):
            query = f"""SELECT DISTINCT ?label WHERE {{ {where}
              ?a rdfs:label ?label. FILTER(LANG(?label) IN ("sk", "cs", "en")) }}"""
            for b in sparql(query):
                known[norm(b["label"]["value"])].add(country)
    if MANUAL_DOMESTIC.exists():
        with open(MANUAL_DOMESTIC, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                known[norm(row["artist"])].add(row["country"])
    return {name: "+".join(sorted(c)) for name, c in known.items()}


def aggregate() -> tuple[list[dict], list[str]]:
    known = domestic_artists()
    songs: dict[tuple, dict] = {}
    days = sorted(p.name for p in CHARTS.iterdir() if p.is_dir())
    for day in days:
        for path in sorted((CHARTS / day).glob("*.json")):
            chart = path.stem  # e.g. apple_sk
            for r in json.loads(path.read_text(encoding="utf-8")):
                artists = split_artists(r["artist"], r["title"])
                # Key on title + main artist: Apple lists all artists, Deezer only the main one.
                key = (base_title(FEAT_IN_TITLE.sub("", r["title"])), artists[0] if artists else "")
                s = songs.setdefault(key, {
                    "artist": r["artist"], "title": r["title"], "points": 0, "chart_days": 0,
                    "charts": set(), "best": {}, "deezer_rank": "", "artists": set()})
                s["points"] += 101 - r["pos"]
                s["chart_days"] += 1
                s["charts"].add(chart)
                s["best"][chart] = min(s["best"].get(chart, 999), r["pos"])
                s["artists"].update(artists)
                if r.get("deezer_rank"):
                    s["deezer_rank"] = r["deezer_rank"]
                # Prefer Apple's spelling: it lists every artist.
                if chart.startswith("apple"):
                    s["artist"], s["title"] = r["artist"], r["title"]

    rows = []
    for s in songs.values():
        countries = sorted({c for a in s["artists"] for c in known.get(a, "").split("+") if c})
        rows.append({
            "artist": s["artist"], "title": s["title"], "points": s["points"],
            "chart_days": s["chart_days"], "charts": " ".join(sorted(s["charts"])),
            **{f"best_{c}": s["best"].get(c, "") for c in ("apple_sk", "deezer_sk", "apple_cz", "deezer_cz")},
            "deezer_rank": s["deezer_rank"],
            "domestic": "+".join(countries),
            "matched_artists": " | ".join(a for a in sorted(s["artists"]) if a in known),
        })
    rows.sort(key=lambda r: -r["points"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows, days


def write_outputs(rows: list[dict], days: list[str]) -> None:
    fields = ["rank"] + [k for k in rows[0] if k != "rank"]
    with open(CHARTS / "ranking.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    domestic = [r for r in rows if r["domestic"]]
    unknown_artists = sorted({r["artist"] for r in rows if not r["domestic"]})
    md = [f"# Chart ranking — {len(days)} snapshot day(s): {days[0]} … {days[-1]}\n",
          f"Charts: Apple Music top 100 + Deezer Charts top 100, SK and CZ. Points = 101 − position, "
          f"summed over charts and days. {len(rows)} songs, {len(domestic)} with a known SK/CZ artist.\n",
          "## Domestic songs (artist in Wikidata pool or manual list)\n",
          "| # | Artist | Song | Points | Best SK (Apple / Deezer) | Best CZ (Apple / Deezer) | Country |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(domestic, 1):
        md.append(f"| {i} | {r['artist']} | {r['title']} | {r['points']} | "
                  f"{r['best_apple_sk'] or '–'} / {r['best_deezer_sk'] or '–'} | "
                  f"{r['best_apple_cz'] or '–'} / {r['best_deezer_cz'] or '–'} | {r['domestic']} |")
    md.append(f"\n## Artists not recognized as SK/CZ ({len(unknown_artists)}) — review, add domestic ones "
              f"to `data/domestic_artists.csv`\n")
    md.append(", ".join(unknown_artists))
    (CHARTS / "ranking.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-snapshot", action="store_true", help="only re-aggregate existing snapshots")
    args = parser.parse_args()
    if not args.no_snapshot:
        take_snapshot(datetime.date.today().isoformat())
    rows, days = aggregate()
    write_outputs(rows, days)
    print(f"{len(rows)} songs, {sum(bool(r['domestic']) for r in rows)} domestic → data/charts/ranking.md")


if __name__ == "__main__":
    main()
