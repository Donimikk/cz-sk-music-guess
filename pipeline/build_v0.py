"""Build the v0 song list for the prototype: domestic CZ/SK songs from 2020 on.

1. Candidates from the IFPI domestic Top 50 charts (radio + digital, CZ + SK) — being on one of
   these charts is the evidence that the song is Czech/Slovak, so no name-based artist
   classification is needed. Only songs that first charted in 2020 or later; the top N by radio
   weeks plus the top N by digital weeks, so pop (radio) and rap (digital) are both in.
   Plus Spotify: songs of verified domestic artists (pipeline/spotify.py) released 2020+ with at least
   --min-plays plays — fan-base artists and rap that never chart (Dušan Vlk). Artists listed in
   data/excluded_artists.csv are dropped from both sources.
2. Resolve each to an iTunes track (store cz, then sk): title must match, artist must match,
   versions (live/remix/acoustic/…) are skipped, the earliest release wins, release year >= 2020.
3. Write site/songs.json (only what the game needs) and data/v0/review.csv (all candidates with
   the reason when a song was dropped, for manual review).

Usage: python pipeline/build_v0.py [--per-chart-type 200] [--min-plays 1000000]
"""

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

from pilot import ROOT, fetch_json, norm

IFPI = ROOT / "data" / "ifpi" / "charts.csv"
OUT_JSON = ROOT / "data" / "v0" / "matched.json"  # full match result; pipeline/catalog.py makes site/songs.json from it
OUT_REVIEW = ROOT / "data" / "v0" / "review.csv"
SPOTIFY_TRACKS = ROOT / "data" / "spotify" / "tracks.csv"
SPOTIFY_ARTISTS = ROOT / "data" / "spotify" / "artists.csv"
EXCLUDED = ROOT / "data" / "excluded_artists.csv"

DOMESTIC = {"cz_radio_top50_cz": "cz", "sk_radio_top50_sk": "sk",
            "cz_digital_top50_cz": "cz", "sk_digital_top50_sk": "sk"}
MIN_YEAR = 2020

# IFPI joins artists with "/", "&", ",", " a ", " x ", "feat."
ARTIST_SPLIT = re.compile(r"\s*(?:/|&|,|\ba\b|\bx\b(?!\S)|\bfeat\.?|\bft\.?)\s*", re.IGNORECASE)
BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
VERSION = re.compile(r"\b(live|remix|remaster\w*|acoustic|akustick\w*|unplugged|instrumental|karaoke|"
                     r"sped up|slowed|radio edit|extended|demo|version|verzia|verze|rmx)\b", re.IGNORECASE)
NOT_A_SONG = re.compile(r"\b(intro|outro|skit|interlude)\b", re.IGNORECASE)
# Strict on Christmas (user decision): a match in the song title, the album title or the iTunes genre drops the song,
# even when Christmas is only a figure of speech ("Každej den mám Vánoce").
CHRISTMAS = re.compile(r"vianoc|v[aá]noc|christmas|xmas|santa claus|je[zž][ií][sš]|j[ie]zul|[sš]t[eě]dr|betl[eé]|p[uů]lno[cč]n|"
                       r"polno[cč]n|koled|vlo[cč]k|sn[eě]hul|stromek|strome[cč]ek|stromček|silent night|jingle bell|"
                       r"let it snow|advent", re.IGNORECASE)


def christmas(*texts: str) -> bool:
    return any(CHRISTMAS.search(t or "") for t in texts)


# iTunes genres of DJ / club tracks, and of songs for children (user: both out).
ELECTRONIC = {"Dance", "House", "Electronic", "Techno", "Trance", "Drum & Bass", "Dubstep", "Electronica", "Hardcore",
              "Hard Dance", "Breakbeat", "Deep House", "Downtempo", "Bass", "Electro"}
CHILDREN = {"Children’s Music", "Children's Music"}
ITUNES_ARTIST_SPLIT = re.compile(r",\s*|\s+&\s+")


def drop_dj_and_children(games: list[dict], review: list[dict]) -> list[dict]:
    """Second pass, once every song has its iTunes genre. An electronic track stays only when a credited artist is a
    singer/rapper, i.e. has 2+ songs in the list and most of them aren't electronic (Tribbs & Ben Cristovao stays,
    Mairee's DJ tracks go). Songs for children go."""
    songs_of: dict[str, list[dict]] = {}
    for g in games:
        for a in ITUNES_ARTIST_SPLIT.split(g["artist"]):
            songs_of.setdefault(norm(a), []).append(g)

    def vocalist(name: str) -> bool:
        """A verified domestic singer/rapper (a Polish guest doesn't save a Tribbs track)."""
        own = songs_of.get(norm(name), [])
        return (norm(name) in spotify_artists()["domestic_names"] and len(own) >= 2
                and sum(s["genre"] in ELECTRONIC for s in own) / len(own) < 0.5)

    keep, why = [], {}
    for g in games:
        if g["genre"] in CHILDREN:
            why[g["id"]] = "children's song"
        elif g["genre"] in ELECTRONIC and not any(vocalist(a) for a in ITUNES_ARTIST_SPLIT.split(g["artist"])):
            why[g["id"]] = f"electronic / DJ track ({g['genre']})"
        else:
            keep.append(g)
    for r in review:
        if r["itunes_id"] in why:
            r["status"] = why[r["itunes_id"]]
    return keep


def clean_title(title: str) -> str:
    """Title without (feat. …), [Remix] etc. — used for matching and for the guess list."""
    return BRACKETS.sub("", title).split(" - ")[0].strip()


def same_title(candidate: str, want: str) -> bool:
    """Store title equals the wanted normalized title. Apple censors words ("P***y Power"):
    each * stands for one letter."""
    cand = clean_title(candidate)
    if norm(cand) == want:  # also when both sides are censored the same way (IFPI "S***S")
        return True
    if "*" not in cand:
        return False
    pattern = "[a-z0-9]".join(re.escape(norm(part)) for part in cand.split("*"))
    return re.fullmatch(pattern, want) is not None


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
    recent = [s for s in songs.values()
              if s["first_year"] >= MIN_YEAR and not excluded(s["artist"]) and not christmas(s["title"])
              and not foreign_ifpi_song(s["artist"])]
    by_radio = sorted(recent, key=lambda s: -s["radio_weeks"])[:per_type]
    by_digital = sorted(recent, key=lambda s: -s["digital_weeks"])[:per_type]
    picked = {id(s): s for s in by_radio + by_digital}
    return [{**s, "source": "ifpi", "plays": ""} for s in picked.values()]


_excluded: set[str] | None = None


def excluded(artist: str) -> bool:
    """Any credited artist is on the manual exclusion list (ADÉLA, B-complex, …)."""
    global _excluded
    if _excluded is None:
        with open(EXCLUDED, encoding="utf-8") as f:
            _excluded = {norm(r["artist"]) for r in csv.DictReader(f)}
    return bool(_excluded & set(artists_of(artist)))


def song_key(artist: str, title: str) -> tuple[str, str]:
    names = artists_of(artist)
    return (names[0] if names else "", norm(clean_title(title)))


_spotify_artists: dict[str, set[str]] | None = None  # filled on first use


def spotify_artists() -> dict[str, set[str]]:
    """From pipeline/spotify.py: normalized names of verified domestic artists and of artists judged foreign.
    Names, not ids: tracks.csv has no artist ids (Spotify album tracks carry artist names only)."""
    global _spotify_artists
    if _spotify_artists is None:
        _spotify_artists = {"domestic_names": set(), "foreign_names": set()}
        with open(SPOTIFY_ARTISTS, encoding="utf-8") as f:
            for a in csv.DictReader(f):
                if a["status"].startswith("ok"):
                    _spotify_artists["domestic_names"].add(norm(a["spotify_name"]))
                elif a["status"].startswith("not domestic"):
                    _spotify_artists["foreign_names"].add(a["name"])
    return _spotify_artists


def foreign_ifpi_song(artist: str) -> bool:
    """IFPI domestic charts list a few foreign songs (The Weeknd & Madonna "Popular"): every artist foreign by Spotify cities."""
    names = artists_of(artist)
    return bool(names) and all(n in spotify_artists()["foreign_names"] for n in names)


def spotify_candidates(min_plays: int) -> list[dict]:
    """Songs from verified domestic artists with enough Spotify plays (one row per song, max plays).
    A domestic artist must be credited on the track: discographies also hold others' releases
    (Karel Gott's lists German Stereoact remixes that don't credit him)."""
    best: dict[tuple, dict] = {}
    domestic = spotify_artists()["domestic_names"]
    with open(SPOTIFY_TRACKS, encoding="utf-8") as f:
        for t in csv.DictReader(f):
            plays = int(t["play_count"] or 0)
            year = int(t["release_date"][:4]) if t["release_date"][:4].isdigit() else 0
            if (plays < min_plays or year < MIN_YEAR or VERSION.search(t["name"]) or NOT_A_SONG.search(t["name"])
                    or christmas(t["name"], t["album"]) or not domestic & {norm(a) for a in t["artists"].split(" & ")}):
                continue
            if excluded(t["artists"]):
                continue
            key = song_key(t["artists"], t["name"])
            if key not in best or plays > best[key]["plays"]:
                best[key] = {"artist": t["artists"], "title": t["name"], "first_year": year, "radio_weeks": 0,
                             "digital_weeks": 0, "source": "spotify", "plays": plays}
    return list(best.values())


def merge(ifpi: list[dict], spotify: list[dict]) -> list[dict]:
    """IFPI candidates first; a Spotify song already among them only adds its play count."""
    out = {song_key(s["artist"], s["title"]): s for s in ifpi}
    for s in spotify:
        key = song_key(s["artist"], s["title"])
        if key in out:
            out[key].update(source="ifpi+spotify", plays=s["plays"])
        else:
            out[key] = s
    return list(out.values())


def artist_tracks(name: str, store: str) -> list[dict]:
    """Songs of the iTunes artist(s) named exactly `name` (lookup returns at most 200 per artist).
    Both calls go through the disk cache, so each artist costs two requests once, for all songs and runs."""
    data = fetch_json("https://itunes.apple.com/search",
                      {"term": name, "country": store, "entity": "musicArtist", "limit": 10})
    tracks = []
    for a in data.get("results", []):
        if norm(a.get("artistName", "")) != name:
            continue
        songs = fetch_json("https://itunes.apple.com/lookup",
                           {"id": a["artistId"], "country": store, "entity": "song", "limit": 200})
        tracks += [t for t in songs.get("results", []) if t.get("wrapperType") == "track"]
    return tracks


def resolve(song: dict) -> tuple[dict | None, str]:
    """Best iTunes track for an IFPI song, or (None, reason)."""
    want_title = norm(clean_title(song["title"]))
    want_artists = artists_of(song["artist"])
    reason = "no search results"
    first_artist = want_artists[0] if want_artists else ""
    # The artist's catalogue first: two cached calls serve every song of that artist, and iTunes search
    # misses explicit tracks (e.g. Viktor Sheen "Stíny") that the lookup returns. Searches cover songs
    # beyond the 200-track lookup cap; the title-only search catches "artist title" returning nothing.
    attempts = [("cz", None)]
    attempts += [(store, f"{first_artist} {clean_title(song['title'])}") for store in ("cz", "sk")]
    attempts.append(("cz", clean_title(song["title"])))
    attempts.append(("sk", None))
    for store, term in attempts:
        if term is None:
            results = artist_tracks(first_artist, store) if first_artist else []
        else:
            results = fetch_json("https://itunes.apple.com/search",
                                 {"term": term, "country": store, "entity": "song", "limit": 50}).get("results", [])
        matches = []
        for t in results:
            if not same_title(t.get("trackName", ""), want_title):
                continue
            artist = norm(t.get("artistName", ""))
            # norm() keeps only Latin letters: a Cyrillic or Korean name becomes "" and "" is a substring of
            # everything — that matched STEIN27 "STFU" to the Russian artist "Фрай".
            if not artist or not any(a in artist or artist in a for a in want_artists):
                continue
            if VERSION.search(t.get("trackName", "")) or not t.get("previewUrl"):
                continue
            matches.append(t)
        if not matches:
            reason = f"no title+artist match ({len(results)} results)"
            continue
        first = min(t.get("releaseDate", "9999") for t in matches)
        if int(first[:4]) < MIN_YEAR:
            return None, f"released {first[:4]} (before {MIN_YEAR})"
        # Prefer the explicit original over a clean edit (bleeped preview); the year stays the earliest release.
        best = min(matches, key=lambda t: (t.get("trackExplicitness") != "explicit", t.get("releaseDate", "9999")))
        best["releaseDate"] = first
        best["_store"] = store
        return best, "ok"
    return None, reason


def resolve_deezer(song: dict) -> dict | None:
    """Deezer track for an IFPI song (id, ISRC, rank) — a second match source; no audio is stored.
    Deezer search, unlike iTunes search, also returns explicit tracks."""
    want_title = norm(clean_title(song["title"]))
    want_artists = artists_of(song["artist"])
    first_artist = want_artists[0] if want_artists else ""
    title = clean_title(song["title"])
    for q in (f'artist:"{first_artist}" track:"{title}"', f"{first_artist} {title}"):
        matches = []
        for t in fetch_json("https://api.deezer.com/search", {"q": q, "limit": 50}).get("data", []):
            if norm(clean_title(t.get("title_short", ""))) != want_title:
                continue
            # Deezer gives one main artist per track, so require an exact name: a substring
            # would let "Rico" pass for "Hard Rico".
            if norm(t.get("artist", {}).get("name", "")) not in want_artists:
                continue
            # "(Album Version)" is the original, not a live/remix version.
            if VERSION.search(re.sub(r"(?i)\(album version\)", "", t.get("title", ""))):
                continue
            matches.append(t)
        if matches:
            # Several uploads of the same song (single, album, compilation): the original is the most played.
            best = max(matches, key=lambda t: t.get("rank", 0))
            return fetch_json(f"https://api.deezer.com/track/{best['id']}", {})
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-chart-type", type=int, default=200)
    parser.add_argument("--min-plays", type=int, default=1_000_000, help="Spotify plays for a non-IFPI song; 0 = IFPI only")
    args = parser.parse_args()

    ifpi = candidates(args.per_chart_type)
    spotify = spotify_candidates(args.min_plays) if args.min_plays else []
    cands = merge(ifpi, spotify)
    print(f"{len(cands)} candidates ({len(ifpi)} IFPI, {len(spotify)} Spotify >= {args.min_plays:,} plays, merged)", flush=True)
    games, review, seen_ids = [], [], set()
    for i, s in enumerate(cands, 1):
        track, reason = resolve(s)
        if track and christmas(track.get("trackName"), track.get("collectionName"), track.get("primaryGenreName")):
            track, reason = None, "Christmas song (title, album or genre)"
        # iTunes may credit more artists than Spotify (Spotify "Eclipse" = iTunes "ProdbyTrippy & Eclipse")
        if track and excluded(track.get("artistName", "")):
            track, reason = None, "excluded artist (iTunes credits)"
        if track and track["trackId"] in seen_ids:
            track, reason = None, "duplicate of another candidate"
        dz = resolve_deezer(s)
        if not track and dz:
            reason += " | on Deezer only"
        review.append({"source": s["source"], "plays": s["plays"],
                       "ifpi_artist": s["artist"], "ifpi_title": s["title"], "first_year": s["first_year"],
                       "radio_weeks": s["radio_weeks"], "digital_weeks": s["digital_weeks"],
                       "status": reason, "itunes_id": track["trackId"] if track else "",
                       "itunes_artist": track["artistName"] if track else "",
                       "itunes_title": track["trackName"] if track else "",
                       "album": track.get("collectionName", "") if track else "",
                       "release": track.get("releaseDate", "")[:10] if track else "",
                       "deezer_id": dz["id"] if dz else "",
                       "deezer_title": f"{dz['artist']['name']} - {dz['title']}" if dz else "",
                       "deezer_release": dz.get("release_date", "") if dz else "",
                       "deezer_rank": dz.get("rank", "") if dz else "",
                       "isrc": dz.get("isrc", "") if dz else "",
                       "genre": track.get("primaryGenreName", "") if track else ""})
        if track:
            seen_ids.add(track["trackId"])
            games.append({
                "id": track["trackId"],
                "deezer": dz["id"] if dz else None,
                "artist": track["artistName"],
                "title": clean_title(track["trackName"]),
                "year": int(track["releaseDate"][:4]),
                "store": track["_store"],
                "cover": track.get("artworkUrl100", "").replace("100x100bb", "300x300bb"),
                "url": track.get("trackViewUrl", ""),
                "genre": track.get("primaryGenreName", ""),
                "plays": s["plays"] or None,
                "source": s["source"],
            })
        if i % 25 == 0:
            print(f"  {i}/{len(cands)} resolved, {len(games)} playable", flush=True)

    games = drop_dj_and_children(games, review)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(games, ensure_ascii=False, indent=0), encoding="utf-8")
    OUT_REVIEW.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_REVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(review[0]))
        w.writeheader()
        w.writerows(review)
    print(f"{len(games)} playable songs → {OUT_JSON.relative_to(ROOT)}")
    print("dropped:", Counter(r["status"].split(" (")[0] for r in review if r["status"] != "ok"))
    print(f"Deezer match: {sum(bool(r['deezer_id']) for r in review)}/{len(review)} candidates, "
          f"{sum(bool(g['deezer']) for g in games)}/{len(games)} playable songs")


if __name__ == "__main__":
    main()
