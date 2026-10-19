# Data sources for a decades-wide CZ/SK song ranking

Research 2026-10-19 by three web-research agents plus own API tests. Constraint at the time: the cloud
environment's network blocked most hosts (Wikipedia, IFPI, kworb, radio sites, Last.fm, ListenBrainz),
and page fetching failed, so many items rest on search-result snippets. **UNVERIFIED = not confirmed
on the source itself.** Re-check those once the network is open.

## Verdict
No single source covers 1960s–today across genres. Planned combination:
1. **Candidates (the canon):** Deezer playlist consensus + Deezer artist top tracks + curated polls.
2. **Popularity score per song:** Deezer `rank` (verified) + Last.fm listeners (if key/terms allow),
   compared **within a decade** (percentiles), plus playlist co-occurrence.
3. **Chart history for ~2006+:** IFPI ČR Radio Top 100 / domestic Top 50 archive — only after reading
   robots.txt/terms; otherwise ask IFPI. Spotify history (kworb, ~2017+) for current music.

## Sources

| Source | Gives | Years | Status | Notes |
|---|---|---|---|---|
| Deezer user playlists (`/search/playlist`) | track lists by decade/genre | 1960s–now | **verified** | e.g. 902950355 "Česko-slovenské hity" (471), 777222775 (376), 15140794523 "České hity 60. a 70. let" (201), 14894996663 "České Hity 60'70'80" (202), 11140943524 "Slovenské hity ´80" (55). User-made, not editorial. Co-occurrence across many playlists = "everyone knows it". |
| Deezer track `rank` | recency-weighted stream score | all | **verified** | Classics have real values: Gott "Lady Carneval" 223k, Žbirka "Atlantida" 172k, Olympic "Jasná zpráva" 178k, Kubišová "Modlitba pro Martu" 124k. Compressed band → separates known/obscure better than hit vs hit. Exact meaning UNVERIFIED. |
| Deezer `/artist/{id}/top` | artist's top tracks by rank | all | **verified** | Remixes/covers mixed in → dedupe. |
| Deezer Charts playlists Top Slovakia / Top Czech Republic | current top 100 | now | **verified** | Used in `pipeline/charts.py`. |
| Deezer `nb_fan` | artist fans | – | verified | Under-represents old artists (Gott 24k). Small weight only. |
| Apple Music RSS most-played | current top 100 sk/cz | now | **verified** | No history. |
| iTunes Search/Lookup | previews, release date | all | **verified** | No popularity field. Rate limit → 403 (see WORKLOG). |
| MusicBrainz | MBID, ISRC, dates, area | all | verified (API up) | Identity/joins, no popularity. |
| Last.fm (track.getInfo, tag/geo top tracks) | lifetime playcount/listeners | all | UNVERIFIED | Probably best lifetime signal for old music. ToS: non-commercial; other use → ask. Needs API key. |
| ListenBrainz popularity API | listen/user count per recording (by artist MBID) | all | UNVERIFIED | Sparse for CZ/SK; corroborating only. Data usable commercially (README). |
| YouTube Data API | views | all | UNVERIFIED | 10k units/day, search = 100 units → ~100 searches/day; title→video matching is the hard part. |
| IFPI ČR Radio Top 100 CZ / SK + Top 50 domestic | weekly airplay chart | CZ ~2006+, SK from IFPI ČR since 2010 | UNVERIFIED | hitparada.ifpicr.cz / ifpicr.cz/hitparada, archive by year+week (snippet). Hub note: robots.txt disallows automated access → don't scrape; ask IFPI. |
| Wikipedia number-one lists (en/de) | weekly #1 only | 2000s–now | UNVERIFIED | CC BY-SA. Thin (only #1), mostly foreign. |
| kworb.net Spotify SK/CZ | daily positions + streams | ~2017/2020+ | UNVERIFIED | robots/terms not read. Current music only. |
| Soundcharts airplay SK/CZ | weekly top 42 spins | recent | UNVERIFIED | Commercial site, shallow. |
| Radio Prague "Hit století" (2018) | 100 Czech songs 1918–2018 | historic | UNVERIFIED | english.radio.cz/node/8156919. High-confidence seed for old eras. |
| TASR "Československo 100 rokov – 100 hitov" | 100 hits | historic | UNVERIFIED | teraz.sk/kultura/ceskoslovensko-100-rokov-100-hitov/357293-clanok.html |
| Zlatý slavík (1962–1991), Český slavík, Slovenský slávik | artist rankings | 1962–now | UNVERIFIED | Artists, not songs; 1969–89 results reportedly manipulated. Artist prior only. |
| Radio station playlist archives | – | – | not found | Nothing confirmed for Expres, Fun, Vlna, Melody, Evropa 2, Impuls, F1, Dvojka, Blaník, Kiss. |
| Kaggle Spotify dumps | Spotify popularity | 2000–2023 | UNVERIFIED | No evidence of CZ/SK coverage; license unclear. Skip. |
| hc.sk, OSA, SOZA, musicbase.cz | origin/catalog | – | unreachable | Possibly for SK vs CZ tagging. |

## Score idea (from research, not final)
Per decade bucket, percentiles:
`score = 0.40·Deezer rank + 0.30·Last.fm listeners + 0.20·min(1, playlist hits / 3) + 0.10·YouTube views (+ 0.05·artist fans)`;
drop missing components and re-normalise. Difficulty: easy = top 15 % of the decade, medium next 35 %.
Decade quota for the daily pick so recent rap doesn't dominate.

## How Heardle-like games pick songs (research, partial)
Original Heardle: semi-random from most-streamed songs of the last decade, SoundCloud audio; Spotify shut
it down in April 2023. "Heardle Decades" = per-decade lists. No public write-ups of clone pipelines found
→ next step: reverse engineering of existing games.

## To verify once the network is open
- ifpicr.cz robots.txt, terms, archive structure; contact for a data request.
- kworb.net robots.txt; Wikipedia year-end pages (sk/cs).
- Deezer API guidelines (game/quiz use, preview caching — preview URLs are signed with expiry).
- Last.fm key + 20 test songs; terms.
- Full lists from Radio Prague and TASR.
