// Hudle — daily + infinite song guessing from iTunes previews.
// The song list (songs.json) holds iTunes track IDs only; the preview URL is looked up at play time.
"use strict";

const STAGES = [1, 2, 4, 7, 11, 16]; // seconds unlocked per attempt
const MAX = STAGES[STAGES.length - 1];
const EPOCH = Date.UTC(2026, 9, 19); // day #1 of the daily puzzle
const STORE_KEY = "hudle:v0";

const $ = (id) => document.getElementById(id);
let songs = [];
let mode = "daily";
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

function dayNumber() {
  const now = new Date();
  const local = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.floor((local - EPOCH) / 86400000) + 1;
}

// Deterministic shuffle so every player gets the same song on the same day.
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function dailyOrder() {
  const order = [...songs].sort((a, b) => a.id - b.id);
  const rand = mulberry32(20261020);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

// ---------- audio ----------
async function loadPreview(song) {
  setStatus("Načítavam ukážku…");
  const res = await fetch(`https://itunes.apple.com/lookup?id=${song.id}&country=${song.store}`);
  if (!res.ok) throw new Error(`iTunes ${res.status}`);
  const url = (await res.json()).results?.[0]?.previewUrl;
  if (!url) throw new Error("no preview");
  const bytes = await (await fetch(url)).arrayBuffer();
  audio.ctx = audio.ctx || new (window.AudioContext || window.webkitAudioContext)();
  audio.buffer = await audio.ctx.decodeAudioData(bytes);
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
async function start(song) {
  stopAudio();
  audio.buffer = null;
  game = restore(song) || { song, guesses: [], done: false, won: false };
  render();
  try {
    await loadPreview(song);
  } catch (e) {
    console.error(e);
    setStatus("Ukážku sa nepodarilo načítať. Skús to o chvíľu znova.");
  }
  render();
}

function restore(song) {
  if (mode !== "daily") return null;
  const saved = load().daily?.[dayNumber()];
  return saved && saved.id === song.id ? { song, ...saved.game } : null;
}

function persist() {
  const state = load();
  if (mode === "daily") {
    state.daily = state.daily || {};
    const { song, ...rest } = game;
    state.daily[dayNumber()] = { id: song.id, game: rest };
  }
  save(state);
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
  persist();
  render();
}

function finish(won) {
  game.done = true;
  game.won = won;
  stopAudio();
  const state = load();
  const stats = state.stats || { played: 0, won: 0, streak: 0, best: 0, lastDay: null };
  if (mode === "daily" && stats.lastDay !== dayNumber()) {
    const day = dayNumber();
    stats.played += 1;
    if (won) {
      stats.won += 1;
      stats.streak = stats.lastWonDay === day - 1 ? stats.streak + 1 : 1;
      stats.best = Math.max(stats.best, stats.streak);
      stats.lastWonDay = day;
    } else {
      stats.streak = 0;
    }
    stats.lastDay = day;
    state.stats = stats;
  }
  if (mode === "infinite") {
    state.played = [...new Set([...(state.played || []), game.song.id])].slice(-5000);
  }
  save(state);
  persist();
  render();
}

function shareText() {
  const marks = { ok: "🟩", near: "🟨", bad: "🟥", skip: "⬛" };
  const squares = game.guesses.map((g) => marks[g.kind]).join("") + "⬜".repeat(STAGES.length - game.guesses.length);
  const head = mode === "daily" ? `Hudle #${dayNumber()}` : "Hudle (nekonečná)";
  const score = game.won ? `${game.guesses.length}/${STAGES.length}` : `X/${STAGES.length}`;
  return `${head} ${score}\n🔊${squares}\n${location.origin}${location.pathname}`;
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

  $("subtitle").textContent = mode === "daily"
    ? `Pesnička dňa č. ${dayNumber()}`
    : "Nekonečná hra";
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
    $("next").hidden = mode !== "infinite";
    $("share").textContent = "Zdieľať výsledok";
    const st = load().stats;
    $("stats").textContent = mode === "daily" && st
      ? `Odohrané ${st.played}, úspešnosť ${Math.round((st.won / st.played) * 100)} %, séria ${st.streak} (najdlhšia ${st.best})`
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

function randomUnplayed() {
  const played = new Set(load().played || []);
  const pool = songs.filter((s) => !played.has(s.id));
  const from = pool.length ? pool : songs;
  return from[Math.floor(Math.random() * from.length)];
}

function setMode(m) {
  mode = m;
  document.querySelectorAll(".mode").forEach((t) => t.setAttribute("aria-pressed", t.dataset.mode === m));
  const order = dailyOrder();
  start(m === "daily" ? order[(dayNumber() - 1 + order.length * 1000) % order.length] : randomUnplayed());
}

async function init() {
  songs = await (await fetch("songs.json")).json();
  $("play").addEventListener("click", play);
  $("skip").addEventListener("click", skip);
  $("submit").addEventListener("click", submit);
  $("next").addEventListener("click", () => start(randomUnplayed()));
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
  document.querySelectorAll(".mode").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  setMode("daily");
}

init();
