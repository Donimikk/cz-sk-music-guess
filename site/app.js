// Hudle prototype — daily + infinite song guessing from iTunes previews.
// The song list (songs.json) holds iTunes track IDs only; the preview URL is looked up at play time.
"use strict";

const STAGES = [1, 2, 4, 7, 11, 16]; // seconds unlocked per attempt
const EPOCH = Date.UTC(2026, 9, 19); // day #1 of the daily puzzle
const STORE_KEY = "hudle:v0";

const $ = (id) => document.getElementById(id);
let songs = [];
let mode = "daily";
let game = null;   // {song, guesses: [{kind, text}], done, won}
let audio = { ctx: null, buffer: null, source: null, startedAt: 0, raf: 0 };

// ---------- helpers ----------
const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
  .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
const label = (s) => `${s.artist} – ${s.title}`;
const mainArtist = (s) => norm(s.artist.split(/,|&| x | feat\.? /i)[0]);

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

function stopAudio() {
  if (audio.source) { try { audio.source.stop(); } catch { /* already stopped */ } }
  audio.source = null;
  cancelAnimationFrame(audio.raf);
  $("progress").style.width = "0";
  $("play").textContent = "▶";
}

async function play() {
  if (audio.source) return stopAudio();
  if (!audio.buffer) return;
  await audio.ctx.resume();
  const seconds = game.done ? audio.buffer.duration : STAGES[Math.min(game.guesses.length, STAGES.length - 1)];
  const src = audio.ctx.createBufferSource();
  const gain = audio.ctx.createGain();
  src.buffer = audio.buffer;
  src.connect(gain).connect(audio.ctx.destination);
  const t = audio.ctx.currentTime;
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(1, t + 0.005); // short fade-in against clicks
  src.start(t, 0, seconds);
  src.onended = () => { if (audio.source === src) stopAudio(); };
  audio.source = src;
  audio.startedAt = t;
  $("play").textContent = "■";
  const total = game.done ? audio.buffer.duration : STAGES[STAGES.length - 1];
  const tick = () => {
    const elapsed = audio.ctx.currentTime - audio.startedAt;
    $("progress").style.width = `${Math.min(100, (elapsed / total) * 100)}%`;
    audio.raf = requestAnimationFrame(tick);
  };
  tick();
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

function skip() {
  if (game.done) return;
  game.guesses.push({ kind: "skip", text: `Preskočené (+${nextStep()} s)` });
  next();
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
  for (let i = 0; i < STAGES.length; i++) {
    const g = game.guesses[i];
    const row = document.createElement("div");
    row.className = `row ${g ? g.kind : ""}`;
    const icon = { ok: "✓", near: "~", bad: "✗", skip: "–" }[g?.kind] || "";
    row.innerHTML = `<span class="mark">${icon}</span><span></span>`;
    row.lastChild.textContent = g ? g.text : "";
    board.appendChild(row);
  }

  const unlocked = game.done ? STAGES[STAGES.length - 1] : STAGES[Math.min(game.guesses.length, STAGES.length - 1)];
  $("unlocked").style.width = `${(unlocked / STAGES[STAGES.length - 1]) * 100}%`;
  $("ticks").innerHTML = STAGES.map((s) => `<span style="left:${(s / STAGES[STAGES.length - 1]) * 100}%">${s}s</span>`).join("");
  $("play").disabled = !audio.buffer;

  $("subtitle").textContent = mode === "daily"
    ? `Pesnička dňa #${dayNumber()} · ${songs.length} českých a slovenských hitov od roku 2020`
    : "Nekonečný režim · hraj, koľko chceš";
  $("controls").hidden = game.done;
  const step = nextStep();
  $("skip").textContent = step ? `Preskočiť (+${step} s)` : "Vzdať sa";

  $("result").hidden = !game.done;
  if (game.done) {
    const s = game.song;
    $("verdict").textContent = game.won ? `Uhádol si na ${game.guesses.length}. pokus!` : "Tentoraz nie.";
    $("answer").textContent = `${label(s)} (${s.year})`;
    $("cover").src = s.cover;
    $("apple").href = s.url;
    $("next").hidden = mode !== "infinite";
    const st = load().stats;
    $("stats").textContent = mode === "daily" && st
      ? `Odohrané: ${st.played} · Úspešnosť: ${Math.round((st.won / st.played) * 100)} % · Séria: ${st.streak} (najlepšia ${st.best})`
      : "";
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
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === m));
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
      else { await navigator.clipboard.writeText(text); $("share").textContent = "Skopírované!"; }
    } catch { /* user cancelled */ }
  });
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  setMode("daily");
}

init();
