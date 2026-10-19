"""Spotify chart totals per country from kworb.net (robots.txt: Allow /).

kworb's `<country>_daily_totals.html` lists every song that has been in that country's Spotify
daily top 200: days on chart, top-10 days, peak, and total streams counted while on the chart
(not lifetime streams). One request per country → data/kworb/spotify_totals.csv.

Usage: python pipeline/kworb.py
"""

import csv
import html
import re
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "kworb" / "spotify_totals.csv"
USER_AGENT = "hudle-pilot/0.1 (+https://github.com/Donimikk/cz-sk-music-guess)"


def num(s: str) -> int | str:
    s = s.replace(",", "").strip("()x")
    return int(s) if s.isdigit() else ""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["country", "artist", "title", "spotify_track_id", "days", "top10_days", "peak",
                    "peak_count", "peak_streams", "total_streams"])
        for country in ("sk", "cz"):
            req = urllib.request.Request(f"https://kworb.net/spotify/country/{country}_daily_totals.html",
                                         headers={"User-Agent": USER_AGENT})
            page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            n = 0
            for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S)[1:]:
                tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(tds) < 7:
                    continue
                track_id = re.search(r"track/([A-Za-z0-9]{22})", tds[0])
                text = html.unescape(re.sub(r"<[^>]+>", "", tds[0])).strip()
                artist, _, title = text.partition(" - ")
                cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in tds[1:7]]
                w.writerow([country, artist, title, track_id.group(1) if track_id else ""] + [num(c) for c in cells])
                n += 1
            print(f"{country}: {n} songs")


if __name__ == "__main__":
    main()
