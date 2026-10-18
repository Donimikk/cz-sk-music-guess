"""Data pilot: how well do iTunes (and Deezer) cover Czech and Slovak artists?

Steps:
  1. Wikidata: singers/rappers and bands from SK and CZ, ranked by sitelink count
     (number of Wikipedia articles = rough popularity proxy).
  2. For the top N per country: iTunes artist search + song lookup (country store sk/cz),
     Deezer artist search (fan count as a second popularity signal).
  3. Write per-artist CSV, per-track CSV and a Markdown coverage report.

Every HTTP response is cached in data/cache/, so re-runs don't hit the APIs again.
Standard library only.

Usage: python pipeline/pilot.py [--top 50]
"""

import argparse
import csv
import hashlib
import json
import re
import statistics
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "pilot"
USER_AGENT = "hudle-pilot/0.1 (+https://github.com/Donimikk/cz-sk-music-guess)"

COUNTRIES = {"sk": "Q214", "cz": "Q213"}
# singer, rapper, singer-songwriter
PERSON_OCCUPATIONS = "wd:Q177220 wd:Q2252262 wd:Q488205"
# musical group, rock band, band
GROUP_CLASSES = "wd:Q215380 wd:Q5741069 wd:Q2088357"

# Minimum seconds between calls per host. iTunes is documented as ~20 calls/min,
# but 3.1 s still got 403 after ~50 calls.
MIN_INTERVAL = {"itunes.apple.com": 4.0, "api.deezer.com": 0.2, "query.wikidata.org": 1.0}
_last_call: dict[str, float] = {}


def fetch_json(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    key = hashlib.sha1(full.encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    host = urllib.parse.urlparse(url).hostname
    wait = MIN_INTERVAL.get(host, 1.0) - (time.monotonic() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)

    req = urllib.request.Request(full, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/sparql-results+json, application/json",
    })
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            # Retry only rate limits and server errors.
            if e.code not in (403, 429) and e.code < 500 or attempt == 5:
                raise
            # iTunes answers a rate limit with 403 and needs minutes, not seconds.
            print(f"    HTTP {e.code} from {host}, waiting {60 * 2 ** attempt} s", flush=True)
            time.sleep(60 * 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            # Dropped connection (e.g. TLS EOF through a proxy): short backoff.
            if attempt == 5:
                raise
            time.sleep(2 ** (attempt + 1))
    _last_call[host] = time.monotonic()

    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def sparql(query: str) -> list[dict]:
    data = fetch_json("https://query.wikidata.org/sparql", {"query": query, "format": "json"})
    return data["results"]["bindings"]


def wikidata_artists(country_qid: str) -> list[dict]:
    # Persons and groups are separate queries: a combined UNION with the label
    # service times out on the public endpoint.
    persons = f"""
    SELECT DISTINCT ?a ?links WHERE {{
      ?a wdt:P27 wd:{country_qid}; wdt:P106 ?occ.
      VALUES ?occ {{ {PERSON_OCCUPATIONS} }}
      ?a wikibase:sitelinks ?links.
    }}"""
    groups = f"""
    SELECT DISTINCT ?a ?links WHERE {{
      ?a wdt:P495 wd:{country_qid}; wdt:P31 ?g.
      VALUES ?g {{ {GROUP_CLASSES} }}
      ?a wikibase:sitelinks ?links.
    }}"""
    artists = {}
    for kind, query in (("person", persons), ("group", groups)):
        for b in sparql(query):
            qid = b["a"]["value"].rsplit("/", 1)[1]
            artists.setdefault(qid, {"qid": qid, "kind": kind, "links": int(b["links"]["value"])})
    return sorted(artists.values(), key=lambda a: -a["links"])


def add_labels_and_ids(artists: list[dict]) -> None:
    values = " ".join(f"wd:{a['qid']}" for a in artists)
    query = f"""
    SELECT ?a ?sk ?cs ?en ?apple ?deezer WHERE {{
      VALUES ?a {{ {values} }}
      OPTIONAL {{ ?a wdt:P2850 ?apple }}
      OPTIONAL {{ ?a wdt:P2722 ?deezer }}
      OPTIONAL {{ ?a rdfs:label ?sk FILTER(LANG(?sk) = "sk") }}
      OPTIONAL {{ ?a rdfs:label ?cs FILTER(LANG(?cs) = "cs") }}
      OPTIONAL {{ ?a rdfs:label ?en FILTER(LANG(?en) = "en") }}
    }}"""
    by_qid = {a["qid"]: a for a in artists}
    for a in artists:
        a.update(name=a["qid"], wd_apple_id="", wd_deezer_id="")
    for b in sparql(query):
        a = by_qid[b["a"]["value"].rsplit("/", 1)[1]]
        a["name"] = next((b[k]["value"] for k in ("sk", "cs", "en") if k in b), a["qid"])
        # An artist can have several IDs; the first one is enough for the pilot.
        a["wd_apple_id"] = a["wd_apple_id"] or b.get("apple", {}).get("value", "")
        a["wd_deezer_id"] = a["wd_deezer_id"] or b.get("deezer", {}).get("value", "")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Suffixes that mark another version of the same song.
VERSION_RE = re.compile(
    r"\s*[\(\[-].*?(live|remaster|remix|edit|version|verzia|verze|akusti|acoustic|instrumental|mix|demo|karaoke).*$",
    re.IGNORECASE)
COMPILATION_RE = re.compile(r"best of|greatest|hits|the very|zlat|kolekc|collection|výber|výběr|komplet|platinum",
                            re.IGNORECASE)


def base_title(title: str) -> str:
    return norm(VERSION_RE.sub("", title))


def itunes_artist(name: str, store: str) -> tuple[dict | None, str, int]:
    """Returns (artist, match method, number of exact-name candidates)."""
    data = fetch_json("https://itunes.apple.com/search",
                      {"term": name, "entity": "musicArtist", "country": store, "limit": 10})
    exact = [r for r in data.get("results", []) if norm(r.get("artistName", "")) == norm(name)]
    return (exact[0] if exact else None), "name", len(exact)


def itunes_songs(artist_id: int, store: str) -> list[dict]:
    data = fetch_json("https://itunes.apple.com/lookup",
                      {"id": artist_id, "entity": "song", "country": store, "limit": 200})
    return [r for r in data.get("results", []) if r.get("wrapperType") == "track"]


def deezer_artist(name: str, deezer_id: str) -> tuple[dict | None, str]:
    if deezer_id:
        data = fetch_json(f"https://api.deezer.com/artist/{deezer_id}", {})
        if "error" not in data:
            return data, "wikidata"
    data = fetch_json("https://api.deezer.com/search/artist", {"q": name, "limit": 10})
    exact = [r for r in data.get("data", []) if norm(r.get("name", "")) == norm(name)]
    # Same name, several artists: the one with most fans is the best guess.
    return (max(exact, key=lambda r: r["nb_fan"]), "name") if exact else (None, "")


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f} %" if whole else "–"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50, help="artists per country")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    artist_rows, track_rows, report = [], [], []
    report.append("# Data pilot — iTunes / Deezer coverage of CZ/SK artists\n")
    report.append(f"Top {args.top} artists per country from Wikidata, ranked by sitelink count. "
                  f"iTunes store = country of the artist. Generated by `pipeline/pilot.py`.\n")

    for store, qid in COUNTRIES.items():
        pool = wikidata_artists(qid)
        top = pool[:args.top]
        add_labels_and_ids(top)
        print(f"[{store}] Wikidata: {len(pool)} artists, checking top {len(top)}", flush=True)

        found = deezer_found = ambiguous = 0
        match_methods = Counter()
        songs_total = preview_total = 0
        unique_per_artist, years, genres, compilations = [], [], Counter(), 0

        for i, a in enumerate(top, 1):
            songs, it_method, name_candidates, it_artist_id = [], "", 0, None
            if a["wd_apple_id"].isdigit():
                it_artist_id = int(a["wd_apple_id"])
                songs = itunes_songs(it_artist_id, store)
                it_method = "wikidata" if songs else ""
            if not songs:
                it, method, name_candidates = itunes_artist(a["name"], store)
                if it:
                    it_artist_id = it["artistId"]
                    songs, it_method = itunes_songs(it_artist_id, store), method
            dz, dz_method = deezer_artist(a["name"], a["wd_deezer_id"])
            row = {"store": store, **a, "itunes_match": it_method, "itunes_name_candidates": name_candidates,
                   "songs": 0, "with_preview": 0, "unique_titles": 0, "first_year": "",
                   "deezer_match": dz_method, "deezer_fans": dz["nb_fan"] if dz else ""}
            deezer_found += bool(dz)
            match_methods[it_method or "none"] += 1
            ambiguous += name_candidates > 1
            if songs:
                found += 1
                # Lookup also returns songs where the artist is only featured; keep own songs.
                songs = [s for s in songs if s.get("artistId") == it_artist_id]
                with_preview = [s for s in songs if s.get("previewUrl")]
                titles = {base_title(s["trackName"]) for s in with_preview}
                own_years = [s["releaseDate"][:4] for s in songs if s.get("releaseDate")]
                row.update(first_year=min(own_years, default=""), songs=len(songs), with_preview=len(with_preview), unique_titles=len(titles))
                songs_total += len(songs)
                preview_total += len(with_preview)
                unique_per_artist.append(len(titles))
                for s in with_preview:
                    year = s.get("releaseDate", "")[:4]
                    is_comp = bool(COMPILATION_RE.search(s.get("collectionName", "")))
                    compilations += is_comp
                    if year:
                        years.append(int(year))
                    genres[s.get("primaryGenreName", "?")] += 1
                    track_rows.append({
                        "store": store, "artist": a["name"], "artist_qid": a["qid"],
                        "track_id": s["trackId"], "track": s["trackName"], "base_title": base_title(s["trackName"]),
                        "album": s.get("collectionName", ""), "compilation_like": int(is_comp),
                        "release_date": s.get("releaseDate", "")[:10], "genre": s.get("primaryGenreName", ""),
                        "duration_s": round(s.get("trackTimeMillis", 0) / 1000), "preview_url": s["previewUrl"],
                    })
            artist_rows.append(row)
            print(f"  {i:>3}/{len(top)} {a['name']}: itunes={it_method or 'no'} "
                  f"previews={row['with_preview']} unique={row['unique_titles']} "
                  f"deezer={dz_method or 'no'}", flush=True)

        missing = [r["name"] for r in artist_rows if r["store"] == store and not r["itunes_match"]]
        with_ids = sum(bool(a["wd_apple_id"]) for a in top)
        report.append(f"\n## {store.upper()}\n")
        report.append(f"- Wikidata pool: **{len(pool)}** artists (singers/rappers + bands)")
        report.append(f"- Apple Music ID on Wikidata: {with_ids}/{len(top)}")
        report.append(f"- Found on iTunes: **{found}/{len(top)}** ({pct(found, len(top))}) — by method: "
                      + ", ".join(f"{m} {n}" for m, n in match_methods.most_common()))
        report.append(f"- Name search with several exact-name artists (risk of wrong artist): {ambiguous}")
        report.append(f"- Found on Deezer: {deezer_found}/{len(top)} ({pct(deezer_found, len(top))})")
        report.append(f"- Tracks: {songs_total}, with preview: {preview_total} ({pct(preview_total, songs_total)})")
        if unique_per_artist:
            report.append(f"- Unique song titles per found artist (versions merged): "
                          f"median {statistics.median(unique_per_artist):.0f}, total {sum(unique_per_artist)}")
        report.append(f"- Tracks on compilation-like albums (release date likely wrong): "
                      f"{compilations} ({pct(compilations, preview_total)})")
        if years:
            decades = Counter(y // 10 * 10 for y in years)
            report.append("- iTunes release year by decade: "
                          + ", ".join(f"{d}s: {n}" for d, n in sorted(decades.items())))
        report.append("- Top genres: " + ", ".join(f"{g} ({n})" for g, n in genres.most_common(6)))
        report.append(f"- Not found on iTunes: {', '.join(missing) or '–'}")

    with open(OUT / "artists.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(artist_rows[0]))
        w.writeheader()
        w.writerows(artist_rows)
    with open(OUT / "tracks.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(track_rows[0]))
        w.writeheader()
        w.writerows(track_rows)
    (OUT / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
