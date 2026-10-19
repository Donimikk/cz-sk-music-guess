# How existing music guessing games work (reverse engineering, 2026-10-19)

Method: downloaded each site's HTML + JS bundles, grepped for fetch()/API paths and audio hosts,
made 1–2 sample calls per endpoint. Nothing bulk-downloaded.

## Songspot (songspot.net, Next.js on Vercel, part of allspot.net game network)
- **Round selection is server-side:** `POST /api/round?difficulty=easy&songStart=preview|random&era=any&genre=any`
  with body `{"exclude":[...], "guessed":[...]}` (IDs already played, up to 8,000, kept in localStorage).
  Response: `track` + `song:{difficulty, era}` + `seed:{title, artist}` + `historyId: "spotify:<id>"`.
  → The catalog is a curated seed list keyed by **Spotify track IDs**, tagged with difficulty and era.
- **Audio, mode `preview`:** the seed is resolved to a **Deezer** track; returns Deezer's preview
  `cdnt-preview.dzcdn.net/...mp3?hdnea=exp=...` — the signed URL is valid only ~15 min, so it is
  resolved per round. Fallback `/api/track?...&failedSource=deezer` and an audio proxy
  `/api/audio?u=<url>&deezerId=<id>` that re-signs expired Deezer URLs.
- **Audio, mode `random` start:** self-hosted 15 s clips `audio.songspot.net/songs/<spotify_id>.m4a`
  (378 kB, Cloudflare, `immutable` cache), `fullTrack: true, clipSeconds: 15`. I.e. they cut and host
  audio themselves (legally riskier than Apple/Deezer previews).
- **Playback:** fetch → `decodeAudioData` (Web Audio), strips an ID3 header first, 4 ms gain fade-in
  against clicks, slices by stage (game starts at 0.1 s). Timeouts 6–18 s, proxy retry.
- **Guess autocomplete:** iTunes Search directly from the browser (`media=music&entity=song&limit=16`),
  dedupe versions (regex for remaster/live/remix/sped up/…), on failure `/api/search?provider=deezer`.
  Answer matching normalises NFKD, strips (…)/[…], " - …" and feat. artists.

## Songless (lessgames.com/songless, Next.js)
- Daily puzzle server-rendered into the page: 3 categories, each `https://cdn.lessgames.com/songs/A116.aac`,
  plus `"date":"2026-10-19"`. Opaque file names → the answer is not visible in the URL.
- Self-hosted AAC clips, 22 s, 192 kbps (Cloudflare). SoundCloud widget/API also present.

## Spotle (spotle.io, SvelteKit + Supabase)
- ~1,000 artists embedded in a JS chunk as static JSON:
  `{"listener_rank":992,"artist":"Boston","country":"us","genre":"Rock","gender":"m","group_size":5,
  "debut_album_year":1976,"image_uri":"https://i.scdn.co/image/..."}` — Spotify top-listener list + hand data.
- Supabase only for accounts/stats (`profiles`, `game_history`, `user_stats`, rpc `spotle_record_game`).
- Audio in side modes: stores only the **iTunes track ID**, resolves the preview at play time via own
  proxy `/api/apple-music-preview?id=<itunesId>` (sample call returned 502).

## Preview quality (same song, Žbirka – Atlantida)
| | iTunes | Deezer |
|---|---|---|
| Length | 30.0 s | 30.0 s |
| Codec | AAC ~300 kbps | MP3 128 kbps |
| URL | stable | signed, ~15 min |
| Version matched | 3:44 track | 4:06 track (different release) |
Neither lets us choose which 30 s of the song we get; the preview segment is picked by the store.

## Takeaways for our game
1. Store **IDs** (iTunes trackId, Deezer id) in the static puzzle JSON, not preview URLs.
   Resolve the preview at play time (iTunes lookup from the browser, or a tiny Worker proxy).
2. Primary audio iTunes (better quality, stable URL), fallback Deezer (resolve fresh, URL expires).
3. Web Audio + decodeAudioData for precise 0.1/0.5/1/2/4/8/16 s stages, short fade-in.
4. Version dedupe + normalised answer matching like Songspot.
5. Self-hosting cut clips (Songspot random mode, Songless) gives control over the segment but means
   storing copyrighted audio — not for us.
6. Catalog = curated seed list with difficulty + era tags (Songspot) → matches our per-decade plan.
