const { randomInt } = require("node:crypto");

const WINDOW_SIZE = 50;
const JOKES_PER_TOPIC = 10;
const STATE_VERSION = 2;
const MODELS = Object.freeze({
  gpt: "openai/gpt-oss-120b",
  qwen: "qwen/qwen3.8-27b",
});

function identity(text) {
  return text.normalize("NFKC").toLocaleLowerCase("de-DE")
    .replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function isJoke(text) {
  return typeof text === "string" && text.trim() === text
    && text.length >= 15 && text.length <= 650 && Boolean(identity(text))
    && text.split("\n").length <= 4
    && !/[\u0000-\u0008\u000B-\u001F\u007F]/u.test(text);
}

function checkState(state) {
  // The old layout kept one generated joke per row and cannot fill both model cards.
  if (state === undefined || (state?.version === 1 && Array.isArray(state.rows))) {
    return { version: STATE_VERSION, rows: [] };
  }
  if (state?.version !== STATE_VERSION || !Array.isArray(state.rows) || state.rows.length > WINDOW_SIZE) {
    throw new Error("Ungültiger Joke-Machine-Webzustand.");
  }
  const archiveIds = new Set();
  for (const row of state.rows) {
    if (!row || typeof row.timestamp !== "string" || !Number.isFinite(Date.parse(row.timestamp))
        || ![row.gpt, row.qwen, row.archive].every(isJoke)) {
      throw new Error("Ungültige Zeile im Joke-Machine-Webzustand.");
    }
    const id = identity(row.archive);
    if (archiveIds.has(id)) throw new Error("Doppelter Archivwitz im Joke-Machine-Webzustand.");
    archiveIds.add(id);
  }
  return state;
}

function readArchive(curated) {
  if (curated?.version !== 2 || !curated.topics || typeof curated.topics !== "object" || Array.isArray(curated.topics)) {
    throw new Error("jokes.json erwartet Version 2 mit nach Themen sortierten Witzen.");
  }
  const topics = Object.entries(curated.topics);
  if (topics.some(([name, jokes]) => !name.trim() || !Array.isArray(jokes)
      || jokes.length !== JOKES_PER_TOPIC || !jokes.every(isJoke))) {
    throw new Error("Jedes Archivthema benötigt genau zehn gültige Witze.");
  }
  const jokes = topics.flatMap(([, entries]) => entries);
  if (new Set(jokes.map(identity)).size !== jokes.length) {
    throw new Error("jokes.json darf auch über Themen hinweg keine Duplikate enthalten.");
  }
  if (topics.length * (JOKES_PER_TOPIC - 2) < WINDOW_SIZE) {
    throw new Error("jokes.json benötigt mindestens sieben Themen für das 50-Zeilen-Fenster.");
  }
  return topics;
}

function pickTopic(state, topics) {
  const used = new Set(state.rows.slice(1 - WINDOW_SIZE).map(row => identity(row.archive)));
  // Keep one candidate even if both live responses match different archive jokes.
  const eligible = topics.filter(([, jokes]) => jokes.filter(text => !used.has(identity(text))).length >= 3);
  return eligible[randomInt(eligible.length)];
}

function appendRow(state, archive, result, timestamp) {
  const rows = state.rows.slice(1 - WINDOW_SIZE);
  const used = new Set(rows.map(row => identity(row.archive)));
  used.add(identity(result.gpt));
  used.add(identity(result.qwen));
  const remaining = archive.filter(text => !used.has(identity(text)));
  if (!remaining.length) throw new Error("Kein unverbrauchter Witz im Archiv verfügbar.");
  const row = { timestamp, gpt: result.gpt, qwen: result.qwen, archive: remaining[randomInt(remaining.length)] };
  return { version: STATE_VERSION, rows: [...rows, row] };
}

module.exports = { MODELS, isJoke, checkState, readArchive, pickTopic, appendRow };
