"""Song catalogue: stable keys + tags on top of the matched song list (run after build_v0.py).

Input:  data/v0/matched.json and data/v0/review.csv (from build_v0.py), data/ifpi/charts.csv,
        hand-made decisions in catalog/*.csv (tracked in git, survive every rebuild).
Output: catalog/catalog.json — every song with its stable key, sources and tags (tracked in git);
        site/songs.json — the same songs with the fields the game needs (key, level, country, genre).

Stable key = "<lead artist>--<title>" (normalized). The iTunes id can change between rebuilds when another
release of the same song is picked; the key doesn't, so tags and manual fixes stay with the song.

Tags (each with its source):
- level: 5 difficulty levels by popularity percentile. Two channels, the higher wins: streaming (Spotify plays)
  and radio (weeks on the IFPI domestic radio charts). Radio weeks correlate −0.75 with plays (WORKLOG 2026-10-22),
  so radio is not converted to plays — a radio hit is known to its own audience.
- country: of the lead artist, from the share of its rows on the CZ vs SK domestic IFPI charts; catalog/artist_country.csv
  overrides it.
- genre: iTunes primaryGenreName grouped (noisy; to be replaced by Spotify playlists / Last.fm).
- era: release decade.
- group: crews / labels from catalog/groups.csv (group, artist). A song gets a group when any of its credited artists
  (iTunes artist field or "feat." in the title) is a member; a song can be in several groups.

Usage: python pipeline/catalog.py
"""

import csv
import json
import re
from collections import Counter, defaultdict

from build_v0 import DOMESTIC, IFPI, OUT_JSON as MATCHED, OUT_REVIEW, artists_of, clean_title, excluded
from pilot import ROOT, norm

SITE_JSON = ROOT / "site" / "songs.json"
CATALOG = ROOT / "catalog"
ARTIST_COUNTRY = CATALOG / "artist_country.csv"
EXCLUDED_SONGS = CATALOG / "excluded_songs.csv"
GROUPS = CATALOG / "groups.csv"
FEAT = re.compile(r"[(\[]\s*(?:feat|ft|with)\.?\s+([^)\]]+)", re.I)

# top share of songs per level, from easiest (decided 2026-10-22, Songspot-like 5 levels)
LEVELS = [("easy", 0.08), ("medium", 0.12), ("hard", 0.20), ("expert", 0.25), ("impossible", 0.35)]
SK_SHARE = (0.4, 0.6)  # below → CZ, above → SK, between → CZ+SK
ITUNES_ARTIST_SPLIT = re.compile(r",\s*|\s+&\s+")
GENRES = {"Hip-Hop/Rap": "hip-hop", "Rap": "hip-hop", "Hip-Hop": "hip-hop", "Alternative Rap": "hip-hop",
          "UK Hip-Hop": "hip-hop", "Pop": "pop", "Indie Pop": "pop", "Pop/Rock": "rock", "Rock": "rock",
          "Alternative": "rock", "Metal": "rock", "Punk": "rock", "Pop Punk": "rock", "R&B/Soul": "rnb"}


def slug(text: str) -> str:
    return norm(text).replace(" ", "-")


def song_key(artist: str, title: str) -> str:
    lead = ITUNES_ARTIST_SPLIT.split(artist)[0]
    return f"{slug(lead)}--{slug(clean_title(title))}"


def ifpi_country_share() -> dict[str, tuple[int, int]]:
    """Normalized artist → (rows on CZ domestic charts, rows on SK domestic charts), all years."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with open(IFPI, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["chart"] in DOMESTIC and r["pos"].isdigit():
                for a in artists_of(r["artist"]):
                    counts[a][DOMESTIC[r["chart"]] == "sk"] += 1
    return {a: (c[0], c[1]) for a, c in counts.items()}


def manual_countries() -> dict[str, str]:
    if not ARTIST_COUNTRY.exists():
        return {}
    with open(ARTIST_COUNTRY, encoding="utf-8") as f:
        return {norm(r["artist"]): r["country"] for r in csv.DictReader(f)}


def country_of(artist: str, share: dict, manual: dict) -> tuple[str, str]:
    lead = norm(ITUNES_ARTIST_SPLIT.split(artist)[0])
    if lead in manual:
        return manual[lead], "manual"
    cz, sk = share.get(lead, (0, 0))
    if cz + sk == 0:
        return "unknown", "no IFPI rows"
    ratio = sk / (cz + sk)
    country = "cz" if ratio < SK_SHARE[0] else "sk" if ratio > SK_SHARE[1] else "cz+sk"
    return country, f"IFPI rows CZ {cz} / SK {sk}"


def group_members() -> dict[str, set[str]]:
    """Normalized artist → groups."""
    groups: dict[str, set[str]] = defaultdict(set)
    with open(GROUPS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            groups[norm(r["artist"])].add(r["group"])
    return groups


def groups_of(artist: str, title: str, members: dict) -> list[str]:
    credited = ITUNES_ARTIST_SPLIT.split(artist)
    for feat in FEAT.findall(title):
        credited += ITUNES_ARTIST_SPLIT.split(feat)
    return sorted({g for a in credited for g in members.get(norm(a), ())})


def merge_duplicates(games: list[dict], log: list[str]) -> list[dict]:
    """The same song can reach the match twice under different credits (IFPI "Cavalier & Ben Cristovao" vs Spotify
    "Cavalier", two Karel Gott "Trezor" releases): keep the one with more plays, with the higher plays and earlier year."""
    by_key: dict[str, dict] = {}
    for g in games:
        if g["year"] >= 9999:  # no real date anywhere (only placeholders like Spotify 1900-01-01): unknown, not hidden
            g = {**g, "year": None}
        key = song_key(g["artist"], g["title"])
        if key not in by_key:
            by_key[key] = g
            continue
        first = by_key[key]
        keep, other = (first, g) if (first.get("plays") or 0) >= (g.get("plays") or 0) else (g, first)
        keep = {**keep, "plays": max(keep.get("plays") or 0, other.get("plays") or 0) or None,
                "year": min((y for y in (keep["year"], other["year"]) if y), default=None)}
        by_key[key] = keep
        log.append(f"duplicate {key}: kept {keep['id']}, dropped {other['id']}")
    return list(by_key.values())


def percentile(values: dict[str, float]) -> dict[str, float]:
    """Key → share of songs with a higher value (0 = most popular). Missing keys aren't ranked."""
    order = sorted(values, key=lambda k: -values[k])
    return {k: i / len(order) for i, k in enumerate(order)}


def assign_levels(songs: list[dict]) -> None:
    plays = {s["key"]: s["plays"] for s in songs if s["plays"]}
    radio = {s["key"]: s["radio_weeks"] for s in songs if s["radio_weeks"]}
    p_stream, p_radio = percentile(plays), percentile(radio)
    for s in songs:
        best = min(p_stream.get(s["key"], 1.0), p_radio.get(s["key"], 1.0))
        s["popularity"] = round(1 - best, 4)
        s["channel"] = "radio" if p_radio.get(s["key"], 1.0) < p_stream.get(s["key"], 1.0) else "streaming"
    ranked = sorted(songs, key=lambda s: -s["popularity"])
    i = 0
    for name, share in LEVELS:
        n = round(share * len(ranked))
        for s in ranked[i:i + n]:
            s["level"] = name
        i += n
    for s in ranked[i:]:
        s["level"] = LEVELS[-1][0]


def main() -> None:
    games = json.loads(MATCHED.read_text(encoding="utf-8"))
    with open(OUT_REVIEW, encoding="utf-8") as f:
        review = {r["itunes_id"]: r for r in csv.DictReader(f) if r["status"] == "ok"}
    share, manual, members = ifpi_country_share(), manual_countries(), group_members()

    # manual exclusions apply here too, so they take effect without re-running the matching
    with open(EXCLUDED_SONGS, encoding="utf-8") as f:
        excluded_songs = {song_key(r["artist"], r["title"]) for r in csv.DictReader(f)}
    songs, seen, dropped = [], {}, []
    for g in merge_duplicates(games, dropped):
        r = review.get(str(g["id"]), {})
        key = song_key(g["artist"], g["title"])
        if key in excluded_songs or excluded(g["artist"], g["title"]):
            dropped.append(f"{g['artist']} – {g['title']}")
            continue
        if key in seen:
            raise SystemExit(f"duplicate song key {key}: ids {seen[key]} and {g['id']} — resolve by hand")
        seen[key] = g["id"]
        country, country_source = country_of(g["artist"], share, manual)
        songs.append({**g, "key": key, "plays": g.get("plays") or 0,
                      "radio_weeks": int(r.get("radio_weeks") or 0), "digital_weeks": int(r.get("digital_weeks") or 0),
                      "country": country, "country_source": country_source,
                      "genre_group": GENRES.get(g.get("genre", ""), "other"), "era": f"{g['year'] // 10 * 10}s" if g["year"] else "unknown",
                      "groups": groups_of(g["artist"], g["title"], members)})
    assign_levels(songs)
    for s in songs:
        s["tags"] = [
            {"tag": f"level:{s['level']}", "source": f"rule ({s['channel']} percentile)"},
            {"tag": f"country:{s['country']}", "source": s["country_source"]},
            {"tag": f"genre:{s['genre_group']}", "source": f"itunes ({s.get('genre')})"},
            {"tag": f"era:{s['era']}", "source": "itunes release year"},
        ] + [{"tag": f"group:{grp}", "source": "catalog/groups.csv"} for grp in s["groups"]]

    CATALOG.mkdir(exist_ok=True)
    songs.sort(key=lambda s: s["key"])
    (CATALOG / "catalog.json").write_text(json.dumps(songs, ensure_ascii=False, indent=1), encoding="utf-8")
    site = [{k: s[k] for k in ("key", "id", "deezer", "artist", "title", "year", "store", "cover", "url",
                               "level", "country", "genre_group", "popularity")}
            | ({"groups": s["groups"]} if s["groups"] else {}) for s in songs]
    SITE_JSON.write_text(json.dumps(site, ensure_ascii=False, indent=0), encoding="utf-8")

    print(f"excluded here: {len(dropped)}", dropped)
    print(f"{len(songs)} songs → catalog/catalog.json, {SITE_JSON.relative_to(ROOT)}")
    print("levels:", Counter(s["level"] for s in songs))
    print("country:", Counter(s["country"] for s in songs))
    print("genre:", Counter(s["genre_group"] for s in songs))
    print("groups:", Counter(grp for s in songs for grp in s["groups"]))
    print("level by channel:", Counter((s["level"], s["channel"]) for s in songs if s["channel"] == "radio"))


if __name__ == "__main__":
    main()
