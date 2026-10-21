# cz-sk-music-guess — working rules

Rules learned while building the song pipeline. They live here until the project is mostly done;
only then are conclusions moved to the hub. Full history: `WORKLOG.md` (gitignored).

## Running the pipeline
- `pipeline/spotify.py` needs the project venv: `.venv/Scripts/python -m pip install -r requirements.txt`,
  then `.venv/Scripts/python pipeline/spotify.py`. The other scripts use only the standard library.
- Spotify data (`spotifyscraper`) is used only to pick songs for the catalogue, never in the public game.
- Test small first: `python pipeline/build_v0.py --per-chart-type 20`, full run only after that looks right.
- Every response is cached on disk by URL (`data/cache/`), so reruns are seconds. To try a matching
  fix, rerun from cache instead of calling the APIs again.
- After any change to matching, diff the new `site/songs.json` against the last committed one
  (`git show HEAD:site/songs.json`) and explain every song that was added or dropped. A fix for
  one song has already broken another (censored titles, P T K).
- Keep a reason for every dropped song in `data/v0/review.csv`. It answers "why is X missing" without re-running.
- Async/parallel calls don't help: the iTunes limit is per IP. Make fewer calls instead.
- spotifyscraper's rate limiter also waits on cache hits (2 req/s → a full rerun from cache took hours).
  Rerun from cache with `--rate 25`; keep the default 2 for runs that hit Spotify.
- `spotify.py` writes its CSVs only at the end: an interrupted run saves nothing (the cache survives).

## External APIs
- Before caching an API's responses, check how it reports errors. Deezer returns quota errors as
  HTTP 200 + `{"error": {"code": 4}}`; caching that would poison the cache.
- iTunes Search does not return explicit tracks (Apple bug since ~Sept 2025, `explicit=Yes` ignored).
  Explicit tracks only come from `lookup?id=<artistId>&entity=song` (max 200 per artist).
- iTunes rate limit: 403 (not 429). The cloud environment got 403 at 3.1 s spacing; locally 4 s gave
  zero 403s over ~1 h. Measure in the environment you run in before tuning `MIN_INTERVAL`.
- Read a service's terms before integrating it (Deezer: non-commercial only, no stored audio, logo required).
- When an API behaves strangely, search the web early — the explicit-tracks bug was a known forum thread.

## Matching songs
- Test a hypothesis on live data in a minute before changing code (diacritics was ruled out with five queries).
- Artist names: exact match where the service gives one artist per field (Deezer). A substring match
  lets foreign or wrong namesakes in ("Rico" for "Hard Rico").
- Known title traps: `(feat. …)` on either side, `(Album Version)` is the original, Apple censors
  words with `*` ("P***y Power"), IFPI censors too ("S***S"), several artists share a name ("Smack").
- Prefer the explicit original over a clean edit (bleeped preview); year = earliest release.

## Who is a domestic artist
- Being on a domestic IFPI chart is not enough: foreign guests appear there too (Adam Port, 3rd on
  "Positions"), and a name-only Spotify search finds foreign namesakes (US rapper "67").
- Spotify top-5 listener cities are the best signal (Dušan Vlk: Prague, Bratislava, Nitra…), but they
  fail for domestic stars with a global hit (ADÉLA: Quezon City, London) and small artists have none.
  Rule in `pipeline/spotify.py`: CZ/SK cities ≥ 30 % OR no city data OR ≥ 2 songs on IFPI domestic
  charts — and always an IFPI song in the own discography or a credit on it (searched, then checked by id).
- IFPI domestic charts contain a few foreign songs (The Weeknd "Popular", Central Cee "BAND4BAND"):
  "lead artist on a domestic song" let those in. Foreign ones have exactly 1 IFPI song; domestic stars with
  foreign listeners have more (ADÉLA 7, MIKOLAS 13).
- Don't drop guest-only artists: Dušan Vlk is only a guest in IFPI ("Limbo" is on Yzomandias' release).
- Spotify search results carry artist names, not ids; three artists are named "Dusan Vlk" → confirm with `get_track`.

## Data selection
- Ranking by total weeks on IFPI charts misses new songs and fan-base artists with short release
  peaks (Dušan Vlk: 47 songs in Spotify SK Top 200 for 1–4 days each, only one IFPI entry).
  Streaming numbers (kworb) must be part of candidate selection.

## Cloud sessions
- The container is lost at the end: before finishing, pack gitignored `data/` and `WORKLOG.md`
  and hand them to the user (zip). This session survived only thanks to a hub copy and the user's zip.
