"""
Original release year of older songs from the artists' discographies on Czech / Slovak Wikipedia.

Why: the catalogue year is the earliest release date on iTunes or Spotify, and compilations spread one wrong date over
many songs (Helena Vondráčková: "Léto je léto" and ten more songs came out as 1964). Wikipedia discographies list
albums with their years, and album pages list their tracks. A song gets the year of the earliest album it is on;
compilations come later than the original album, so the minimum is the original release.

Steps per lead artist: find the artist page (search), take its discography (the "Diskografie …" page it links to, or
its own Diskografie section), read "year – ''album''" lines, fetch the album pages (50 per request; an unlinked album
is tried as "X (album, Artist)", "X (album)" and "X"), take the year from the album infobox and the track list, and
match our titles (normalized, exact).

Output: catalog/song_year_wiki.csv — key, our year, Wikipedia year, album and page, for every song found where the
years differ. catalog.py applies it below the hand-checked catalog/song_year.csv.

    python pipeline/wiki_years.py [--before 2010]
"""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict

from catalog import CATALOG, ITUNES_ARTIST_SPLIT, ROOT, clean_title, song_key
from pilot import fetch_json, norm

# the match result, not site/songs.json: that one already carries the years this script decided (a feedback loop
# that let a 2010 live album "fix" a year it had set to unknown)
SONGS = ROOT / "data" / "v0" / "matched.json"
SPOTIFY_TRACKS = ROOT / "data" / "spotify" / "tracks.csv"
REVIEW = ROOT / "data" / "v0" / "review.csv"
OUT = CATALOG / "song_year_wiki.csv"
WIKIS = ("cs", "sk")

LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
ITALIC = re.compile(r"''(.+?)''")
YEAR = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")
QUALIFIER = re.compile(r"\s*\(.*\)$")
DISAMBIGUATION = ("{{Rozcestník", "{{rozcestník", "{{Rozlišovacia", "{{rozlišovacia")
# track list lines: "# Title", "1. Title", "| název3 = Title", "| title3 = Title" (track list templates)
TRACK = re.compile(r"^(?:#+\s*|\d{1,2}\.\s+|\|\s*(?:title|název|nazev)\d+\s*=\s*)(.+)$", re.I | re.M)
SUBPAGE = re.compile(r"\{\{\s*(?:Podrobně|Podrobne|Hlavný článok|Hlavní článek)\s*\|\s*(Diskografi[ea][^}|]*)")
SECTION = re.compile(r"==\s*Diskografi[ea]\s*==(.*?)(?:\n==[^=]|\Z)", re.S)
RELEASED = re.compile(r"\|\s*(?:vydáno|vydané|released)\s*=([^\n]*)")
# a discography line for a release that doesn't hold first recordings: live, best-of, reissue (Recitál 2010 holds
# "A ty se ptáš, co já" from 1981)
NOT_ORIGINAL = re.compile(r"živ|live|výběr|výber|vyber|kompil|best|hity|hits|reedic|komplet|collection|gold|zlat|"
                          r"recitál|koncert|concert|platinum|singly|singles", re.I)


def api(wiki: str, **params) -> dict:
    return fetch_json(f"https://{wiki}.wikipedia.org/w/api.php", {"format": "json", "formatversion": 2, **params})


def unlink(text: str) -> str:
    return LINK.sub(lambda m: m.group(2) or m.group(1), text)


def is_disambiguation(text: str) -> bool:
    return any(d in text for d in DISAMBIGUATION)


def is_album(text: str) -> bool:
    return bool(re.search(r"\{\{\s*(?:Infobox\s*-?\s*album|Infobox\s+Album|Seznam skladeb|Tracklist|Zoznam skladieb)",
                          text, re.I)) and not is_disambiguation(text)


def wikitext(wiki: str, title: str) -> str:
    return api(wiki, action="parse", page=title, prop="wikitext", redirects=1).get("parse", {}).get("wikitext", "")


def wikitexts(wiki: str, titles: list[str]) -> dict[str, str]:
    """Page title → wikitext for the pages that exist, 50 per request."""
    out = {}
    for i in range(0, len(titles), 50):
        d = api(wiki, action="query", prop="revisions", rvprop="content", rvslots="main", redirects=1,
                titles="|".join(titles[i:i + 50]))
        q = d.get("query", {})
        for p in q.get("pages", []):
            if p.get("revisions"):
                out[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
        for r in q.get("normalized", []) + q.get("redirects", []):  # keep the title we asked for
            if r["to"] in out:
                out[r["from"]] = out[r["to"]]
    return out


def artist_pages(wiki: str, artist: str) -> list[str]:
    """Search hits whose title is the artist's name, with or without a qualifier ("Kabát (hudební skupina)")."""
    hits = api(wiki, action="query", list="search", srsearch=artist, srlimit=10).get("query", {}).get("search", [])
    return [h["title"] for h in hits if norm(QUALIFIER.sub("", h["title"])) == norm(artist)]


def discography(wiki: str, page: str) -> list[tuple[int, list[str]]]:
    """(year, candidate album page titles) from the artist's discography page or the Diskografie section."""
    text = wikitext(wiki, page)
    if is_disambiguation(text):
        return []
    sub = SUBPAGE.search(text)
    if sub:
        text = wikitext(wiki, sub.group(1).strip()) or text
    else:
        m = SECTION.search(text)
        text = m.group(1) if m else ""
    artist = QUALIFIER.sub("", page)
    albums = []
    for line in text.splitlines():
        italic = ITALIC.search(line)
        # the release year stands before the title; a year inside the title is not it ("Singles 2002–2016")
        year = (YEAR.search(line[:italic.start()]) or YEAR.search(line[italic.end():])) if italic else None
        if not line.lstrip().startswith("*") or not year:
            continue
        inner = italic.group(1)
        link = LINK.search(inner)
        name = unlink(inner).strip(" '")
        cands = ([link.group(1).strip()] if link else []) + [f"{name} (album, {artist})", f"{name} (album)", name]
        albums.append((int(year.group(1)), cands, not NOT_ORIGINAL.search(line)))
    return albums


def album_year(text: str, fallback: int) -> int:
    m = RELEASED.search(text)
    y = YEAR.search(unlink(m.group(1))) if m else None
    return int(y.group(1)) if y else fallback


def tracks(text: str) -> set[str]:
    out = set()
    for m in TRACK.finditer(text):
        t = re.sub(r"''+", "", unlink(m.group(1)))
        t = re.split(r"\s+[–—-]\s+|\s*\(\d+:\d\d\)|\s+\d+:\d\d", t)[0]  # drop authors and durations
        t = norm(clean_title(t))
        if t:
            out.add(t)
    return out


def years_for(artist: str) -> tuple[dict[str, list[tuple]], dict[str, tuple[int, bool]], str]:
    """For one artist: normalized track title → [(year, album, page, original)] from album pages' track lists, and
    normalized album name → (year, original) from the discography lines; the last value says what was found."""
    for wiki in WIKIS:
        for page in artist_pages(wiki, artist):
            albums = discography(wiki, page)
            if not albums:
                continue
            album_years: dict[str, tuple[int, bool]] = {}
            for line_year, cands, original in albums:
                name = norm(QUALIFIER.sub("", cands[-1]))
                if name not in album_years or line_year < album_years[name][0]:
                    album_years[name] = (line_year, original)
            texts = wikitexts(wiki, sorted({c for _, cands, _ in albums for c in cands}))
            found: dict[str, list[tuple]] = defaultdict(list)
            pages = 0
            for line_year, cands, original in albums:
                # the first candidate that is a real album page: a link in the line can also be a person
                # ("Michal Tučný zpívá písně [[Zdeněk Rytíř|Zdeňka Rytíře]]"), whose page lists songs of all years
                title = next((c for c in cands if is_album(texts.get(c, ""))), None)
                if not title:
                    continue
                pages += 1
                year = album_year(texts[title], line_year)
                for t in tracks(texts[title]):
                    found[t].append((year, title, f"{wiki}:{page}", original))
            return found, album_years, (f"{wiki}:{page}, {len(albums)} albums, {pages} album pages, "
                                        f"{len(found)} titles")
    return {}, {}, "no page/discography"


def albums_of_songs() -> dict[tuple[str, str], set[str]]:
    """(normalized lead artist, normalized title) → normalized names of the albums the song is on, from every Spotify
    version (data/spotify/tracks.csv) and the matched iTunes release (data/v0/review.csv)."""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    with open(SPOTIFY_TRACKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lead = norm(r["artists"].split(" & ")[0])
            out[(lead, norm(clean_title(r["name"])))].add(norm(clean_title(r["album"])))
    with open(REVIEW, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["itunes_id"]:
                lead = norm(ITUNES_ARTIST_SPLIT.split(r["itunes_artist"])[0])
                out[(lead, norm(clean_title(r["itunes_title"])))].add(norm(clean_title(r["album"])))
    return out


def suspicious_ids() -> set[int]:
    """iTunes ids whose year is a placeholder: a 1 January or 31 December date shared by 3+ matched songs of one
    artist (Vondráčková "Best Of The Best" 1964-01-01 ×13; Karel Gott '78, Hity '71, '76 all 1969-01-01;
    Dalibor Janda "Velký flám (Zlaté album)" 1982-12-31 holds "Hurikán 99")."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    with open(REVIEW, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            # before 2000 only: later albums carry real 1 January dates too (Mandrage, Nightwork)
            if r["itunes_id"] and r["release"][:4] < "2000" and r["release"].endswith(("-01-01", "-12-31")):
                groups[(ITUNES_ARTIST_SPLIT.split(r["itunes_artist"])[0], r["release"])].append(int(r["itunes_id"]))
    return {i for ids in groups.values() if len(ids) >= 3 for i in ids}


MAX_GAP = 5  # years between a compilation's placeholder date and the album that replaces it


def decide(year: int | None, cands: list[tuple], suspicious: bool) -> tuple[int | None, str, tuple | None] | None:
    """New year (None = unknown), rule and the album it comes from; None when our year stays.
    An album that holds the song and is older than our year always wins (the song existed by then). A suspicious
    year is replaced by the earliest original album (not live, best-of, reissue), or dropped when there is none,
    because a later live or best-of album would only trade one wrong year for another."""
    if cands:
        first = min(cands)
        if year is None or first[0] < year:
            return first[0], "album older than our year", first
    if suspicious:
        # the placeholder is usually the start of the artist's career (a lower bound) and the album an upper bound:
        # only when they are close is the album year trusted ("Jožin z bažin" 1976 vs a 2007 album → unknown)
        originals = [c for c in cands if c[3]]
        if originals and min(originals)[0] - (year or 0) <= MAX_GAP:
            first = min(originals)
            return first[0], "compilation date replaced by original album", first
        if originals:
            return None, f"compilation date, earliest album {min(originals)[0]} too far", min(originals)
        return None, "compilation date, no original album found", None
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=int, default=2010)
    args = ap.parse_args()
    every = [{**s, "key": song_key(s["artist"], s["title"]), "year": s["year"] if s["year"] < 9999 else None}
             for s in json.loads(SONGS.read_text(encoding="utf-8"))]
    lead = lambda s: ITUNES_ARTIST_SPLIT.split(s["artist"])[0]
    # every song of an artist with an older song: a reissue makes a year too late as well (Tučný "Snídaně v trávě" 2015)
    old_artists = {lead(s) for s in every if (s["year"] or 0) < args.before}
    songs = [s for s in every if lead(s) in old_artists]
    by_artist = defaultdict(list)
    for s in songs:
        by_artist[lead(s)].append(s)
    song_albums, suspicious = albums_of_songs(), suspicious_ids()
    rows, matched = [], 0
    for artist, items in sorted(by_artist.items()):
        found, album_years, note = years_for(artist)
        hits = 0
        for s in items:
            title = norm(clean_title(s["title"]))
            cands = list(found.get(title, []))
            # the albums this song is on (Spotify versions, iTunes), with their year from the discography lines
            for album in song_albums.get((norm(artist), title), ()):
                if album in album_years:
                    cands.append((album_years[album][0], album, "discography line", album_years[album][1]))
            hits += bool(cands)
            d = decide(s["year"], cands, s["id"] in suspicious)
            if d:
                year, rule, src = d
                rows.append({"key": s["key"], "artist": s["artist"], "title": s["title"], "our_year": s["year"] or "",
                             "year": year or "", "rule": rule, "album": src[1] if src else "",
                             "source": src[2] if src else ""})
        matched += hits
        print(f"{artist:32} {hits:3}/{len(items):<3} {note}", flush=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "artist", "title", "our_year", "year", "rule", "album", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(songs)} songs of artists with a song before {args.before}: {matched} found in Wikipedia discographies, "
          f"{len(suspicious)} suspicious compilation dates, {len(rows)} changes → {OUT.relative_to(ROOT)}")
    print(Counter(r["rule"] for r in rows))


if __name__ == "__main__":
    main()
