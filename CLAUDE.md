# cz-sk-music-guess — working rules

Rules learned while building the song pipeline. They live here until the project is mostly done;
only then are conclusions moved to the hub. Full history: `WORKLOG.md` (gitignored).

## Running the pipeline
- `pipeline/spotify.py` needs the project venv: `.venv/Scripts/python -m pip install -r requirements.txt`,
  then `.venv/Scripts/python pipeline/spotify.py`. The other scripts use only the standard library.
- Spotify data (`spotifyscraper`) is used only to pick songs for the catalogue, never in the public game.
- Pipeline order: `build_v0.py` (matching → data/v0/matched.json + review.csv) → `catalog.py` (stable keys, tags →
  catalog/catalog.json + site/songs.json). catalog.py never reads site/songs.json (a first version did and would
  have lost plays/genres on a second run). Manual decisions live in catalog/*.csv and data/excluded_artists.csv.
- Test small first: `python pipeline/build_v0.py --per-chart-type 20`, full run only after that looks right.
- Every response is cached on disk by URL (`data/cache/`), so reruns are seconds. To try a matching
  fix, rerun from cache instead of calling the APIs again.
- Before rerunning a build, copy `site/songs.json` and `data/v0/review.csv` aside: review.csv is overwritten and
  without it an added/dropped song can't be explained (Radikal Chef "Euro Boy").
- After any change to matching, diff the new `site/songs.json` against the last committed one
  (`git show HEAD:site/songs.json`) and explain every song that was added or dropped. A fix for
  one song has already broken another (censored titles, P T K).
- Keep a reason for every dropped song in `data/v0/review.csv`. It answers "why is X missing" without re-running.
- Shell gotcha: Python code with backslashes (``, line continuation) inside a bash heredoc gets mangled → use the Edit tool.
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
- `norm()` keeps only Latin letters: Cyrillic/Korean names become "" and "" is a substring of everything — six
  domestic songs were matched to Russian/Korean tracks (STEIN27 "STFU" → "Фрай"). Never accept an empty name.
- A Spotify discography also lists others' releases (Karel Gott's holds German Stereoact remixes that don't credit him):
  a candidate needs a verified domestic artist credited by name (tracks.csv has no artist ids).
- IFPI domestic charts hold a few foreign songs; drop an IFPI song when all its artists are foreign by Spotify cities.
- iTunes genres are noisy (Lucie Bílá "Christian", Calin "Soundtrack"): don't build categories on them alone.
- DJ/electronic and children's songs out (user): electronic iTunes genre is dropped unless a verified domestic
  singer/rapper is credited (`drop_dj_and_children`); children's artists go to `data/excluded_artists.csv`
  (Miro Jaroš's song with Rytmus is tagged Pop, so a genre filter alone misses it).
- Christmas: strict — title, album or genre (regex `CHRISTMAS` in build_v0.py); keep it narrow ("Santa Maria" is fine).

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

## Language (CZ / SK)
- The "country" tag means the language the lead artist sings/raps in (user 2026-10-22), shown as filter "Jazyk".
  Derived from IFPI domestic-chart rows (SK share <0.4 CZ, >0.6 SK); borderline artists are set by the user in
  catalog/artist_country.csv (e.g. Milion Plus 36/37 rows → CZ, Pavol Habera 93/80 → SK — the ratio alone is wrong there).
  Artists with no IFPI rows: majority language of their Spotify release titles (`pipeline/title_language.py --write`).

## Data selection
- Ranking by total weeks on IFPI charts misses new songs and fan-base artists with short release
  peaks (Dušan Vlk: 47 songs in Spotify SK Top 200 for 1–4 days each, only one IFPI entry).
  Streaming numbers (kworb) must be part of candidate selection.

## Site design
- Don't build UI blind. Two redesigns described in words and built without a single preview were rejected
  ("looks like 2022", "odporné"). Show options visually first (mockup artifact), let the user pick, then build.
- Ground styling in real references: pull actual CSS values from comparable games with `curl` (WebFetch drops CSS).
  Spotle and Songless values are in WORKLOG (2026-10-21).
- Check every site change with headless Playwright yourself (user's standing OK): `python -m http.server 8765` in
  `site/`, screenshots at 320 / 390 / 1280 into `.playwright-mcp/` (git-excluded). Chromium has no AAC → test with the
  real previews converted to WAV and route `itunes.apple.com/lookup` to them. The cloud session used system ffmpeg; this
  PC has none → `pip install av` (PyAV bundles ffmpeg) in a scratch venv. Don't fall back to a synthetic tone. The first screenshot found a crash on load that two versions had shipped with.
- Stale cache: the browser kept an old songs.json and app.js. songs.json is fetched with `cache: "no-cache"`; in
  Playwright clear the cache via CDP `Network.clearBrowserCache` before checking a new build.
- CSS gotcha: % padding resolves against the containing block's width (the record label's text vanished under the bar).
- Palette: the navy #0F0F23 / #181836 stays (user 2026-10-25: generic enough); contrast = hot pink #FF5A8A (variant B).
- iOS: after an app switch the sound is dead. The context may still say "running" (audioSession "playback" keeps it
  running in the background, WebKit 261554), so checking the state is not enough (first fix failed on the iPhone):
  after visibilitychange/pagehide the context is replaced in the next play tap (`freshContext()`); AudioBuffers survive.
  Not verifiable in Playwright, test on an iPhone.
- User's taste so far: classic centred Heardle layout, dark blue background with depth, buttons that look like
  buttons (raised, shadow, press state), the half record only as a small element above the bar.

## Hub
- When updating the hub project file, also refresh the top `Status:` line and outdated decisions — appending new
  sections below left the status saying "pilot" while the game was already live.

## Cloud sessions
- The container is lost at the end: before finishing, pack gitignored `data/` and `WORKLOG.md`
  and hand them to the user (zip). This session survived only thanks to a hub copy and the user's zip.
