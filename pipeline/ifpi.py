"""Download the ČNS IFPI weekly chart archive (ifpicr.cz) and flatten it into one CSV.

The archive page lists every week (weekId) since 2006; each chart/week has an export
`/front.charts/download?chartId=..&weekId=..` that returns an HTML table. Charts start in
different years, so the first non-empty week of each chart is found by binary search first.
Requests run in a small thread pool (I/O bound, ~0.9 s server latency each) and are cached
in data/ifpi/raw/, so a re-run only fetches what is missing.

Usage: python pipeline/ifpi.py [--workers 6]
"""

import argparse
import csv
import html
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "ifpi" / "raw"
OUT = ROOT / "data" / "ifpi" / "charts.csv"
SONGS = ROOT / "data" / "ifpi" / "songs.csv"
BASE = "https://ifpicr.cz"
USER_AGENT = "hudle-pilot/0.1 (+https://github.com/Donimikk/cz-sk-music-guess)"

CHARTS = {
    2: "cz_radio_top100",
    7: "cz_radio_top50_cz",
    30: "cz_digital_top100",
    32: "cz_digital_top50_cz",
    40: "sk_radio_top100",
    42: "sk_radio_top50_sk",
    43: "sk_digital_top100",
    44: "sk_digital_top50_sk",
}
COLUMNS = ["pos", "pos_1w", "pos_2w", "artist", "title", "label", "peak", "weeks", "ph"]

_local = threading.local()


def get(url: str) -> str:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))


def list_weeks() -> list[tuple[int, str]]:
    """(weekId, label) oldest first; monthly entries ("Měsíc") are skipped."""
    page = get(f"{BASE}/hitparada/2")
    weeks = {}
    for value, label in re.findall(r'<option[^>]*value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>', page):
        if "týden" in label:
            weeks.setdefault(label, int(value))  # a few labels appear twice; keep the first ID
    return sorted(((v, l) for l, v in weeks.items()), key=lambda x: x[0])


def fetch_week(chart_id: int, week_id: int) -> str:
    path = RAW / str(chart_id) / f"{week_id}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = get(f"{BASE}/front.charts/download?chartId={chart_id}&weekId={week_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def parse(text: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", text, re.S)[1:]:  # first row is the header
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if cells:
            rows.append(cells)
    return rows


def first_week(chart_id: int, weeks: list[tuple[int, str]]) -> int:
    """Index of the first week with data (assumes a chart, once started, is not empty)."""
    lo, hi = 0, len(weeks) - 1
    if not parse(fetch_week(chart_id, weeks[hi][0])):
        return len(weeks)
    while lo < hi:
        mid = (lo + hi) // 2
        if parse(fetch_week(chart_id, weeks[mid][0])):
            hi = mid
        else:
            lo = mid + 1
    return lo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    weeks = list_weeks()
    print(f"{len(weeks)} weeks in archive: {weeks[0][1]} … {weeks[-1][1]}", flush=True)

    jobs = []
    for chart_id, name in CHARTS.items():
        start = first_week(chart_id, weeks)
        print(f"{name}: from {weeks[start][1] if start < len(weeks) else '–'} "
              f"({len(weeks) - start} weeks)", flush=True)
        jobs += [(chart_id, w, label) for w, label in weeks[start:]]

    started, done = time.monotonic(), 0
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_week, c, w): (c, w, label) for c, w, label in jobs}
        for f in as_completed(futures):
            results[futures[f]] = f.result()
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(jobs)} in {time.monotonic() - started:.0f} s", flush=True)

    empty, rows_out = 0, 0
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["chart", "week_id", "week", "year"] + COLUMNS)
        for (chart_id, week_id, label) in sorted(results, key=lambda k: (k[0], k[1])):
            rows = parse(results[(chart_id, week_id, label)])
            empty += not rows
            year = re.search(r"(\d{4})", label)  # a few labels have trailing text after the year
            year = year.group(1) if year else ""
            for r in rows:
                w.writerow([CHARTS[chart_id], week_id, label, year] + (r + [""] * len(COLUMNS))[:len(COLUMNS)])
                rows_out += 1
    print(f"{len(jobs)} chart-weeks ({empty} empty), {rows_out} rows → {OUT.relative_to(ROOT)} "
          f"in {time.monotonic() - started:.0f} s")
    summarize()


def summarize() -> None:
    """One row per chart + song: weeks on chart, best position, first and last week."""
    songs: dict[tuple, dict] = {}
    with open(OUT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r["pos"].isdigit():
                continue
            key = (r["chart"], r["artist"], r["title"])
            s = songs.setdefault(key, {"weeks": 0, "best": 999, "first_id": 10**9, "last_id": 0})
            wid = int(r["week_id"])
            s["weeks"] += 1
            s["best"] = min(s["best"], int(r["pos"]))
            if wid < s["first_id"]:
                s["first_id"], s["first_week"] = wid, r["week"]
            if wid > s["last_id"]:
                s["last_id"], s["last_week"] = wid, r["week"]
    with open(SONGS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["chart", "artist", "title", "weeks", "best_pos", "first_week", "last_week"])
        for (chart, artist, title), s in sorted(songs.items(), key=lambda kv: (kv[0][0], -kv[1]["weeks"])):
            w.writerow([chart, artist, title, s["weeks"], s["best"], s["first_week"], s["last_week"]])
    print(f"{len(songs)} chart/song rows → {SONGS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
