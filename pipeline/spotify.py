"""Spotify play counts for domestic artists — used only to pick songs for the catalogue.

1. Artists = everyone on the IFPI domestic Top 50 charts (radio + digital, CZ + SK, features
   included) who charted since --since, plus data/domestic_artists.csv.
2. Each name is searched on Spotify (exact normalized name). A hit counts only if most of its
   top-5 listener cities are in CZ/SK (foreign namesakes and foreign guests on domestic songs are
   dropped), and only if its discography contains one of the artist's IFPI songs.
3. For accepted artists: monthly listeners and every track of every own release with its play
   count. The same recording on a single and an album is merged (max plays, earliest date).

Output: data/spotify/artists.csv (one row per artist, with the reason when not found) and
data/spotify/tracks.csv. Responses are cached in data/cache/spotify/ for 30 days.

Usage: .venv/Scripts/python pipeline/spotify.py [--since 2020] [--limit 20]
"""

import argparse
import csv
import time

from spotify_scraper import CacheConfig, FileCache, SpotifyClient, SpotifyScraperError

from build_v0 import DOMESTIC, IFPI, artists_of, clean_title
from pilot import ROOT, norm

OUT = ROOT / "data" / "spotify"
CACHE = ROOT / "data" / "cache" / "spotify"
MAX_NAMESAKES = 3  # exact-name search hits checked per artist
DOMESTIC_SHARE = 0.3  # min share of top-5 city listeners in CZ/SK (B-complex 0.44, Jean Luc 0.31)
MIN_IFPI_SONGS = 2  # artists with fewer IFPI songs must pass on listener cities (The Weeknd has 1)


def seed_artists(since: int) -> tuple[dict[str, set[str]], set[str]]:
    """(normalized artist name → normalized titles of their IFPI domestic chart songs (all years),
    names listed first on at least one of those songs — a lead artist, not only a guest)."""
    titles: dict[str, set[str]] = {}
    recent: set[str] = set()
    lead: set[str] = set()
    with open(IFPI, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["chart"] not in DOMESTIC or not r["pos"].isdigit():
                continue
            year = int(r["year"]) if r["year"].isdigit() else 0
            names = artists_of(r["artist"])
            if names:
                lead.add(names[0])
            for a in names:
                titles.setdefault(a, set()).add(norm(clean_title(r["title"])))
                if since <= year <= 2026:
                    recent.add(a)
    seeds = {a: titles[a] for a in recent}
    with open(ROOT / "data" / "domestic_artists.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            seeds.setdefault(norm(r["artist"]), set())
            lead.add(norm(r["artist"]))
    return seeds, lead


def album_tracks(client: SpotifyClient, artist_id: str) -> list[dict]:
    rows = []
    for ref in client.get_discography(artist_id):
        album = client.get_album(ref.id)
        for t in album.tracks:
            rows.append({
                "track_id": t.id, "name": t.name,
                "artists": " & ".join(a.name for a in t.artists),
                "artist_ids": " ".join(a.id for a in t.artists),
                "album": album.name, "album_type": album.album_type,
                "release_date": str(album.release_date or "")[:10],
                "play_count": t.play_count or 0, "explicit": t.explicit,
            })
    return rows


def overview(client: SpotifyClient, artist_id: str) -> dict:
    """Monthly listeners, followers and the CZ/SK share of the top-5 listener cities.
    Uses the library's raw pathfinder call (`_anon_union`): its Artist model has no top cities."""
    stats = client._anon_union("artist", artist_id, "artistUnion").get("stats") or {}
    cities = (stats.get("topCities") or {}).get("items") or []
    total = sum(c.get("numberOfListeners") or 0 for c in cities)
    domestic = sum(c.get("numberOfListeners") or 0 for c in cities if c.get("country") in ("CZ", "SK"))
    return {"monthly_listeners": stats.get("monthlyListeners") or "", "followers": stats.get("followers") or "",
            "cz_sk_share": round(domestic / total, 2) if total else "",
            "top_cities": ", ".join(f"{c.get('city')} {c.get('country')}" for c in cities)}


def on_ifpi_song(client: SpotifyClient, artist_id: str, name: str, ifpi_titles: set[str]) -> bool:
    """The artist is credited on one of their IFPI songs released by someone else
    (Dušan Vlk is a guest on Yzomandias' "Limbo", which is not in his own discography)."""
    for title in sorted(ifpi_titles)[:3]:
        for t in client.search(f"{name} {title}", types=("track",), limit=10).tracks:
            # Search results carry artist names only; the full track has the ids (3 artists are named "Dusan Vlk").
            if norm(clean_title(t.name)) == title and any(norm(a.name) == name for a in t.artists):
                if any(a.id == artist_id for a in client.get_track(t.id).artists):
                    return True
    return False


def resolve(client: SpotifyClient, name: str, ifpi_titles: set[str]) -> tuple[object, dict, list[dict], str]:
    """(artist, overview, tracks, status). An exact-name hit is kept when ≥30 % of its top-city
    listeners are in CZ/SK (Dušan Vlk), or Spotify shows no cities (small artists), or the name has
    ≥2 IFPI domestic songs (ADÉLA: global hit, foreign cities). Foreign artists with one song on a
    domestic chart are dropped (Adam Port, The Weeknd). Then the hit with an IFPI song in its own
    discography — or credited on one released by someone else — is accepted."""
    hits = [a for a in client.search(name, types=("artist",), limit=10).artists if norm(a.name) == name]
    if not hits:
        return None, {}, [], "no exact name on Spotify"
    domestic = []
    for a in hits[:MAX_NAMESAKES]:
        ov = overview(client, a.id)
        share = ov["cz_sk_share"]
        if share == "" or share >= DOMESTIC_SHARE or len(ifpi_titles) >= MIN_IFPI_SONGS:
            domestic.append((a, ov))
    if not domestic:
        return None, ov, [], f"not domestic by listener cities ({ov['top_cities'][:60]})"
    if not ifpi_titles:  # manual list (no IFPI songs): only safe when one domestic artist has the name
        if len(domestic) > 1:
            return None, {}, [], f"{len(domestic)} domestic namesakes, nothing to verify against"
        a, ov = domestic[0]
        return a, ov, album_tracks(client, a.id), "ok (manual list)"
    for a, ov in domestic:
        tracks = album_tracks(client, a.id)
        if any(norm(clean_title(t["name"])) in ifpi_titles for t in tracks):
            return a, ov, tracks, "ok"
    for a, ov in domestic:
        if on_ifpi_song(client, a.id, name, ifpi_titles):
            return a, ov, album_tracks(client, a.id), "ok (guest on IFPI song)"
    return None, {}, [], f"no IFPI song in discography ({len(domestic)} domestic exact-name hits)"


def merge_versions(tracks: list[dict]) -> list[dict]:
    """Same recording on single + album: keep the max play count and the earliest release."""
    best: dict[tuple, dict] = {}
    for t in tracks:
        key = (norm(t["name"]), t["artist_ids"])
        b = best.get(key)
        if b is None:
            best[key] = dict(t)
            continue
        b["release_date"] = min(b["release_date"] or "9999", t["release_date"] or "9999")
        if t["play_count"] > b["play_count"]:
            b.update({k: t[k] for k in ("track_id", "album", "album_type", "play_count")})
    return list(best.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=int, default=2020, help="artists charting in or after this year")
    parser.add_argument("--limit", type=int, help="only the first N artists (test run)")
    args = parser.parse_args()

    seeds, _ = seed_artists(args.since)
    names = sorted(seeds)[: args.limit] if args.limit else sorted(seeds)
    print(f"{len(names)} artists", flush=True)
    cache = CacheConfig(store=FileCache(CACHE), ttl_seconds=30 * 86400)
    artists, tracks = [], []
    with SpotifyClient(cache=cache) as client:
        for i, name in enumerate(names, 1):
            row = {"name": name, "ifpi_songs": len(seeds[name]), "status": "", "spotify_id": "",
                   "spotify_name": "", "monthly_listeners": "", "followers": "", "cz_sk_share": "",
                   "top_cities": "", "tracks": 0}
            for attempt in range(2):  # Spotify sometimes reports a live entity as unavailable; retry once
                try:
                    hit, ov, rows, row["status"] = resolve(client, name, seeds[name])
                    row.update(ov)
                    if hit:
                        rows = merge_versions(rows)
                        row.update(spotify_id=hit.id, spotify_name=hit.name, tracks=len(rows))
                        tracks += [{"seed_artist": hit.name, **t} for t in rows]
                    break
                except SpotifyScraperError as e:
                    row["status"] = f"error: {type(e).__name__}: {e}"[:200]
                    time.sleep(10)
            artists.append(row)
            if i % 10 == 0 or i == len(names):
                print(f"  {i}/{len(names)} artists, {len(tracks)} tracks", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    for path, rows in ((OUT / "artists.csv", artists), (OUT / "tracks.csv", tracks)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    found = sum(r["status"].startswith("ok") for r in artists)
    print(f"{found}/{len(artists)} artists found, {len(tracks)} tracks → {OUT.relative_to(ROOT)}")
    statuses: dict[str, int] = {}
    for r in artists:
        if not r["status"].startswith("ok"):
            key = r["status"].split(" (")[0].split(":")[0]
            statuses[key] = statuses.get(key, 0) + 1
    print("not found:", statuses)


if __name__ == "__main__":
    main()
