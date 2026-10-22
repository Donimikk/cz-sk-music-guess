// Hudle — song guessing from iTunes previews, one song after another (no daily song: guessing a song tests taste,
// not reasoning, so one shared song a day doesn't work like Wordle — see WORKLOG 2026-10-22).
// The song list (songs.json) holds iTunes track IDs only; the preview URL is looked up at play time.
"use strict";

const STAGES = [1, 2, 4, 7, 11, 16]; // seconds unlocked per attempt
const MAX = STAGES[STAGES.length - 1];
const STORE_KEY = "hudle:v0";

const $ = (id) => document.getElementById(id);
let songs = [];
let game = null;   // {song, guesses: [{kind, text}], done, won}
let audio = { ctx: null, buffer: null, source: null, startedAt: 0, from: 0, raf: 0 };

// ---------- helpers ----------
const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
  .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
const label = (s) => `${s.artist} – ${s.title}`;
const mainArtist = (s) => norm(s.artist.split(/,|&| x | feat\.? /i)[0]);
const clock = (sec) => `0:${String(Math.floor(sec)).padStart(2, "0")}`;
const unlockedSeconds = () => game.done ? MAX : STAGES[Math.min(game.guesses.length, STAGES.length - 1)];
// The bar shows 16 s while guessing and the whole preview once the game is over.
const barLength = () => game?.done && audio.buffer ? audio.buffer.duration : MAX;

function load() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
}
function save(state) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch { /* private mode */ }
}

// ---------- audio ----------
// Look up the preview URL, download and decode it. Used for the current song and to prepare the next one.
async function fetchPreview(song) {
  const res = await fetch(`https://itunes.apple.com/lookup?id=${song.id}&country=${song.store}`);
  if (!res.ok) throw new Error(`iTunes ${res.status}`);
  const url = (await res.json()).results?.[0]?.previewUrl;
  if (!url) throw new Error("no preview");
  const bytes = await (await fetch(url)).arrayBuffer();
  audio.ctx = audio.ctx || new (window.AudioContext || window.webkitAudioContext)();
  return audio.ctx.decodeAudioData(bytes);
}

async function loadPreview(song, prepared) {
  setStatus("Načítavam ukážku…");
  let buffer = null;
  if (prepared) {
    try { buffer = await prepared; } catch { /* preparing failed: load it now */ }
  }
  audio.buffer = buffer || await fetchPreview(song);
  setStatus("");
}

function setPlaying(on) {
  $("disc").classList.toggle("playing", on);
  $("playShape").setAttribute("d", on ? "M7 5h4v14H7zM13 5h4v14h-4z" : "M8 5v14l11-7z");
  $("play").setAttribute("aria-label", on ? "Zastaviť" : game?.done ? "Prehrať celú ukážku" : "Prehrať ukážku");
}

function showProgress(sec) {
  $("progress").style.width = `${Math.min(100, (sec / barLength()) * 100)}%`;
  $("elapsed").textContent = clock(sec);
}

function stopAudio() {
  if (audio.source) { try { audio.source.stop(); } catch { /* already stopped */ } }
  audio.source = null;
  cancelAnimationFrame(audio.raf);
  showProgress(0);
  setPlaying(false);
}

// Play [from, to) seconds of the preview.
async function playSegment(from, to) {
  stopAudio();
  if (!audio.buffer) return;
  await audio.ctx.resume();
  const src = audio.ctx.createBufferSource();
  const gain = audio.ctx.createGain();
  src.buffer = audio.buffer;
  src.connect(gain).connect(audio.ctx.destination);
  const t = audio.ctx.currentTime;
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(1, t + 0.005); // short fade-in against clicks
  src.start(t, from, to - from);
  src.onended = () => { if (audio.source === src) stopAudio(); };
  audio.source = src;
  audio.startedAt = t;
  audio.from = from;
  setPlaying(true);
  const tick = () => {
    showProgress(audio.from + (audio.ctx.currentTime - audio.startedAt));
    audio.raf = requestAnimationFrame(tick);
  };
  tick();
}

function play() {
  if (audio.source) return stopAudio();
  if (!audio.buffer) return;
  playSegment(0, game.done ? audio.buffer.duration : unlockedSeconds());
}

// ---------- game ----------
// Fetch the cover while the player is still guessing, so the answer shows it without a wait.
const coverPreloads = new Map(); // song id → Image, kept so the browser doesn't drop it
function preloadCover(song) {
  if (coverPreloads.has(song.id)) return;
  const img = new Image();
  img.src = song.cover;
  coverPreloads.set(song.id, img);
  if (coverPreloads.size > 4) coverPreloads.delete(coverPreloads.keys().next().value);
}

// The next song is picked and its preview and cover are loaded while the current one is guessed,
// so "Ďalšia pesnička" starts without a wait.
let upcoming = null; // {song, preview: Promise<AudioBuffer>}
function prepareNext() {
  const song = randomUnplayed(game.song.id);
  const preview = fetchPreview(song);
  preview.catch(() => { /* loadPreview retries when the song starts */ });
  preloadCover(song);
  upcoming = { song, preview };
}

async function start(song, prepared) {
  stopAudio();
  audio.buffer = null;
  upcoming = null;
  preloadCover(song);
  game = { song, guesses: [], done: false, won: false };
  render();
  try {
    $("retry").hidden = true;
    await loadPreview(song, prepared);
  } catch (e) {
    console.error(e);
    setStatus("Ukážku sa nepodarilo načítať.");
    $("retry").hidden = false;
  }
  render();
  if (game.song === song) prepareNext();
}

function startNext() {
  if (upcoming) start(upcoming.song, upcoming.preview);
  else start(randomUnplayed(game?.song.id));
}

function guess(song) {
  if (game.done) return;
  if (song.id === game.song.id) {
    game.guesses.push({ kind: "ok", text: label(song) });
    return finish(true);
  }
  const near = mainArtist(song) === mainArtist(game.song);
  game.guesses.push({ kind: near ? "near" : "bad", text: label(song) });
  next();
}

// Skipping plays only the newly unlocked part, from where the previous attempt ended.
function skip() {
  if (game.done) return;
  const step = nextStep();
  game.guesses.push({ kind: "skip", text: step ? `Preskočené (+${step} s)` : "Vzdané" });
  next();
  if (!game.done && audio.buffer) {
    const i = game.guesses.length;
    playSegment(STAGES[i - 1], STAGES[i]);
  }
}

function nextStep() {
  const i = game.guesses.length;
  return i + 1 < STAGES.length ? STAGES[i + 1] - STAGES[i] : 0;
}

function next() {
  if (game.guesses.length >= STAGES.length) return finish(false);
  render();
}

function finish(won) {
  game.done = true;
  game.won = won;
  stopAudio();
  const state = load();
  // streak = songs guessed in a row
  const stats = state.songStats || { played: 0, won: 0, streak: 0, best: 0 };
  stats.played += 1;
  if (won) {
    stats.won += 1;
    stats.streak += 1;
    stats.best = Math.max(stats.best, stats.streak);
  } else {
    stats.streak = 0;
  }
  state.songStats = stats;
  state.played = [...new Set([...(state.played || []), game.song.id])].slice(-5000);
  save(state);
  render();
}

function shareText() {
  const marks = { ok: "🟩", near: "🟨", bad: "🟥", skip: "⬛" };
  const squares = game.guesses.map((g) => marks[g.kind]).join("") + "⬜".repeat(STAGES.length - game.guesses.length);
  const score = game.won ? `${game.guesses.length}/${STAGES.length}` : `X/${STAGES.length}`;
  return `Hudle ${score}\n🔊${squares}\n${location.origin}${location.pathname}`;
}

// ---------- UI ----------
function setStatus(text) { $("status").textContent = text; }

function render() {
  const board = $("board");
  board.innerHTML = "";
  STAGES.forEach((_, i) => {
    const g = game.guesses[i];
    const li = document.createElement("li");
    li.className = g ? g.kind : !game.done && i === game.guesses.length ? "current" : "";
    li.innerHTML = `<span class="mark" aria-hidden="true"></span><span class="text"></span>`;
    li.querySelector(".mark").textContent = { ok: "✓", near: "~", bad: "✗", skip: "–" }[g?.kind] || "";
    li.querySelector(".text").textContent = g ? g.text : "";
    board.appendChild(li);
  });

  // bar: attempt boundaries while guessing, unlocked part, total length
  const len = barLength();
  const bar = $("bar");
  bar.querySelectorAll(".bar-tick").forEach((t) => t.remove());
  if (!game.done) {
    STAGES.slice(0, -1).forEach((s) => {
      const tick = document.createElement("span");
      tick.className = "bar-tick";
      tick.style.left = `${(s / MAX) * 100}%`;
      bar.appendChild(tick);
    });
  }
  $("unlocked").style.width = game.done ? "100%" : `${(unlockedSeconds() / len) * 100}%`;
  $("total").textContent = clock(len);

  $("play").disabled = !audio.buffer;
  if (!audio.source) setPlaying(false);
  if (audio.buffer) setStatus(game.done ? "" : `Odomknuté ${unlockedSeconds()} s`);

  $("controls").hidden = game.done;
  const step = nextStep();
  $("skip").textContent = step ? `Preskočiť (+${step} s)` : "Vzdať sa";

  const s = game.song;
  $("result").hidden = !game.done;
  if (game.done) {
    $("cover").src = s.cover;
    $("resultCover").src = s.cover;
    $("verdict").textContent = game.won ? `Uhádnuté na ${game.guesses.length}. pokus` : "Tentoraz nie";
    $("answerArtist").textContent = s.artist;
    $("answerTitle").textContent = `${s.title} (${s.year})`;
    $("apple").href = s.url;
    $("share").textContent = "Zdieľať výsledok";
    const st = load().songStats;
    $("stats").textContent = st
      ? `Uhádnuté ${st.won} z ${st.played}, séria ${st.streak} (najdlhšia ${st.best})`
      : "";
  } else {
    $("cover").removeAttribute("src");
  }
}

let suggestionIndex = -1;
let suggestionList = [];
let picked = null;

function updateSuggestions() {
  const q = norm($("guess").value);
  picked = null;
  suggestionList = q.length < 2 ? [] : songs
    .filter((s) => norm(label(s)).includes(q))
    .filter((s) => !game.guesses.some((g) => g.text === label(s)))
    .slice(0, 8);
  suggestionIndex = suggestionList.length ? 0 : -1;
  drawSuggestions();
}

function drawSuggestions() {
  const ul = $("suggestions");
  ul.innerHTML = "";
  suggestionList.forEach((s, i) => {
    const li = document.createElement("li");
    li.role = "option";
    li.textContent = label(s);
    li.setAttribute("aria-selected", i === suggestionIndex);
    li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(s); });
    ul.appendChild(li);
  });
}

function choose(s) {
  picked = s;
  $("guess").value = label(s);
  suggestionList = [];
  drawSuggestions();
}

function submit() {
  const s = picked || suggestionList[suggestionIndex];
  if (!s) return;
  $("guess").value = "";
  picked = null;
  suggestionList = [];
  drawSuggestions();
  guess(s);
}

function randomUnplayed(currentId) {
  const played = new Set(load().played || []);
  if (currentId) played.add(currentId);
  const pool = songs.filter((s) => !played.has(s.id));
  const from = pool.length ? pool : songs;
  return from[Math.floor(Math.random() * from.length)];
}

async function init() {
  // iOS treats Web Audio as "ambient" sound, muted by the silent switch; "playback" plays like a music app
  // (Safari 16.4+, WebKit bugs 237322 / 251532). Must be set before the AudioContext starts.
  if ("audioSession" in navigator) navigator.audioSession.type = "playback";
  $("retry").addEventListener("click", () => start(game.song));
  // "no-cache" = revalidate with the server, so an updated song list reaches players right away.
  songs = await (await fetch("songs.json", { cache: "no-cache" })).json();
  $("play").addEventListener("click", play);
  $("skip").addEventListener("click", skip);
  $("submit").addEventListener("click", submit);
  $("next").addEventListener("click", startNext);
  $("guess").addEventListener("input", updateSuggestions);
  $("guess").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!suggestionList.length) return;
      suggestionIndex = (suggestionIndex + (e.key === "ArrowDown" ? 1 : -1) + suggestionList.length) % suggestionList.length;
      drawSuggestions();
    } else if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  });
  $("share").addEventListener("click", async () => {
    const text = shareText();
    try {
      if (navigator.share) await navigator.share({ text });
      else { await navigator.clipboard.writeText(text); $("share").textContent = "Skopírované"; }
    } catch { /* user cancelled */ }
  });
  start(randomUnplayed());
}

init();
