const { readFileSync, writeFileSync, renameSync, mkdirSync } = require("node:fs");
const path = require("node:path");
const { randomInt } = require("node:crypto");
const { MODELS, isJoke, checkState, readArchive, appendRow } = require("./web-state.js");

const DATA = path.join(__dirname, "..", "data");
const STATE_FILE = process.env.JMAC_STATE_FILE || path.join(DATA, "web-state.json");
const GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions";

const SETTINGS = [
  "Paketabholung am Kiosk", "Bücherrückgabe in der Bibliothek", "beim Bäcker",
  "in der Warteschlange im Supermarkt", "im Familienchat",
  "beim Nachbarn im Treppenhaus", "im Wartezimmer", "in der Bahn",
  "am Schalter im Bürgerbüro", "beim Ausmisten zu Hause",
];

const SYSTEM = [
  "Du schreibst einen kurzen Alltagswitz auf natürlichem, gesprochenem Deutsch.",
  "Baue eine kleine, konkrete Situation mit einer klaren Erwartung auf.",
  "Die letzte Zeile kippt genau diese Erwartung mit einer überraschenden, aber sofort nachvollziehbaren Pointe.",
  "Die Figuren reagieren menschlich. Vermeide erfundene Wörter, Zufall als Pointe und verkrampfte Wortspiele.",
  "Verwende höchstens vier kurze Zeilen. Gib nur den Witz aus, ohne Titel oder Erklärung.",
].join(" ");

function readJson(file, optional = false) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    if (optional && error.code === "ENOENT") return undefined;
    throw new Error(`JSON-Datei ${path.basename(file)} konnte nicht gelesen werden.`);
  }
}

async function generate(model, messages, apiKey) {
  const options = {
    model,
    messages,
    temperature: 0.8,
    max_completion_tokens: 900,
    reasoning_effort: model === MODELS.gpt ? "low" : "none",
  };
  if (model === MODELS.gpt) options.include_reasoning = false;

  let response;
  try {
    response = await fetch(GROQ_ENDPOINT, {
      method: "POST",
      redirect: "error",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify(options),
      signal: AbortSignal.timeout(25_000),
    });
  } catch {
    throw new Error(`Groq-Netzwerkfehler für ${model}.`);
  }
  if (!response.ok) throw new Error(`Groq HTTP ${response.status} für ${model}.`);

  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`Ungültige Groq-Antwort für ${model}.`);
  }
  const choice = data.choices?.[0];
  const text = typeof choice?.message?.content === "string"
    ? choice.message.content.trim().replace(/\r\n/g, "\n") : "";
  if (data.model !== model || choice?.finish_reason !== "stop" || !isJoke(text)) {
    throw new Error(`Unvollständiger Witz oder abweichendes Modell für ${model}.`);
  }
  return text;
}

async function main() {
  const state = checkState(readJson(STATE_FILE, true));
  const archive = readArchive(readJson(path.join(DATA, "jokes.json")));
  const archiveJokes = new Set(archive);
  const rows = state.rows.filter(row => archiveJokes.has(row.archive));
  const apiKey = process.env.GROQ_API_KEY?.trim();
  if (!apiKey) throw new Error("GROQ_API_KEY fehlt.");

  const setting = SETTINGS[randomInt(SETTINGS.length)];
  const messages = [
    { role: "system", content: SYSTEM },
    { role: "user", content: `Schreibe genau einen Witz zum Thema „${setting}“. Entscheide dich für eine einzige Pointe.` },
  ];
  const [gpt, qwen] = await Promise.all([
    generate(MODELS.gpt, messages, apiKey),
    generate(MODELS.qwen, messages, apiKey),
  ]);
  const updated = appendRow({ ...state, rows }, archive, { gpt, qwen }, new Date().toISOString());
  mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  const temporary = STATE_FILE + ".tmp";
  writeFileSync(temporary, JSON.stringify(updated, null, 2) + "\n");
  renameSync(temporary, STATE_FILE);
  console.log(`Witztrio gespeichert. GPT-OSS 120B, Qwen 3.8 27B und Archiv. ${updated.rows.length}/50.`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
