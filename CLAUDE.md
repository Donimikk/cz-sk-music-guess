# cz-sk-music-guess — working rules

Rules learned while building the song pipeline. They live here until the project is mostly done;
only then are conclusions moved to the hub. Full history: `WORKLOG.md` (gitignored).

## Running the pipeline
- Test small first: `python pipeline/build_v0.py --per-chart-type 20`, full run only after that looks right.
- Every response is cached on disk by URL (`data/cache/`), so reruns are seconds. To try a matching
  fix, rerun from cache instead of calling the APIs again.
- After any change to matching, diff the new `site/songs.json` against the last committed one
  (`git show HEAD:site/songs.json`) and explain every song that was added or dropped. A fix for
  one song has already broken another (censored titles, P T K).
- Keep a reason for every dropped song in `data/v0/review.csv`. It answers "why is X missing" without re-running.
- Async/parallel calls don't help: the iTunes limit is per IP. Make fewer calls instead.

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

## Data selection
- Ranking by total weeks on IFPI charts misses new songs and fan-base artists with short release
  peaks (Dušan Vlk: 47 songs in Spotify SK Top 200 for 1–4 days each, only one IFPI entry).
  Streaming numbers (kworb) must be part of candidate selection.

## Cloud sessions
- The container is lost at the end: before finishing, pack gitignored `data/` and `WORKLOG.md`
  and hand them to the user (zip). This session survived only thanks to a hub copy and the user's zip.
