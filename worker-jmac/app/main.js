const fs = require("fs");
const path = require("path");

const CONFIG_FILE = path.join(__dirname, "..", "config", "settings.json");
const DATA_DIR = path.join(__dirname, "..", "data");
const HISTORY_FILE = path.join(DATA_DIR, "history.json");
const LATEST_FILE = path.join(DATA_DIR, "latest-results.json");
const RESERVE_FILE = path.join(DATA_DIR, "reserve.json");

const HISTORY_LIMIT = 50;
const MAX_LINES = 5;
const TIMEOUT_MS = 30000;
const OPENROUTER_MAX_MODELS = 3;
const RESERVE_TARGET = 8;
const RESERVE_REFILL_PER_RUN = 2;

const GROQ_API_KEY = String(process.env.GROQ_API_KEY || "").trim();
const OPENROUTER_API_KEY = String(process.env.OPENROUTER_API_KEY || "").trim();

function loadSettings() {
  const data = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
  const webhook = data?.webhook || {};
  const sender = data?.sender || {};
  if (!webhook.secret_env || !sender.name || !sender.avatar_url)
    throw new Error("config/settings.json is incomplete");
  return {
    webhookSecretEnv: String(webhook.secret_env),
    webhookTimeoutMs: Math.max(1000, Number(webhook.timeout_seconds || 15) * 1000),
    senderName: String(sender.name),
    avatarUrl: String(sender.avatar_url)
  };
}

const SETTINGS = loadSettings();
fs.mkdirSync(DATA_DIR, { recursive: true });

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    throw new Error(`Invalid JSON state in ${path.basename(file)}: ${err.message}`);
  }
}

function writeJson(file, value) {
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(temp, file);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function cleanText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/^\s*["'`]+|["'`]+\s*$/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function knownJokesFromHistory(history) {
  return (Array.isArray(history) ? history : [])
    .filter(x =>
      x &&
      ["ok", "blocked"].includes(x.status) &&
      typeof x.text === "string" &&
      x.text.trim()
    )
    .map(x => cleanText(x.text))
    .filter(Boolean);
}

function tokenize(text) {
  return new Set(
    cleanText(text)
      .toLocaleLowerCase("de-DE")
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .split(/\s+/)
      .filter(w => w.length >= 3)
  );
}

function jaccard(a, b) {
  const A = tokenize(a);
  const B = tokenize(b);
  if (!A.size || !B.size) return 0;
  let same = 0;
  for (const x of A) if (B.has(x)) same++;
  return same / (A.size + B.size - same);
}

function validateJoke(raw, knownJokes) {
  const text = cleanText(raw);
  if (!text) return { ok: false, reason: "leer" };

  const lines = text.split("\n").filter(x => x.trim());
  if (lines.length > MAX_LINES) return { ok: false, reason: `>${MAX_LINES} Zeilen` };
  if (text.length < 25) return { ok: false, reason: "zu kurz" };
  if (text.length > 650) return { ok: false, reason: "zu lang" };

  if (/^(hier ist|gerne|natürlich|witz:|mein witz|neuer witz|antwort:|okay[,!:])/i.test(text))
    return { ok: false, reason: "Meta-Text" };

  if (/<\/?think>|reasoning:|analyse:|analysis:|bewertung:/i.test(text))
    return { ok: false, reason: "Reasoning/Meta" };

  const low = text.toLocaleLowerCase("de-DE");
  if (knownJokes.some(j => cleanText(j).toLocaleLowerCase("de-DE") === low))
    return { ok: false, reason: "exakte Wiederholung" };

  let maxSimilarity = 0;
  for (const old of knownJokes) maxSimilarity = Math.max(maxSimilarity, jaccard(text, old));
  if (maxSimilarity >= 0.76)
    return { ok: false, reason: `zu ähnlich ${maxSimilarity.toFixed(2)}` };

  return {
    ok: true,
    text,
    lines: lines.length,
    similarity: maxSimilarity
  };
}

function buildMessages(contextJokes, edgeMode, reserveMode = false) {
  const historyText = contextJokes.length
    ? contextJokes.map((j, i) => `${i + 1}. ${j}`).join("\n---\n")
    : "(noch keine)";

  const edgeInstruction = edgeMode
    ? "Heute darf die Pointe leicht frech/keck sein, aber nur dezent: nicht beleidigend, nicht derb, nicht düster."
    : "Heute klar leichter, sympathischer und harmloser Alltagshumor; nicht edgy erzwingen.";

  return [
    {
      role: "system",
      content:
`Du bist ein deutscher Comedy-Autor für LEICHTEN, natürlichen Alltagshumor.
Die Witze werden ORIGINAL AUF DEUTSCH erdacht. Übersetzte englische Wortspiele sind verboten.

Qualitätsmaßstab:
- sofort verständlich
- natürliches Muttersprachler-Deutsch
- einfache, klare Situation
- nachvollziehbare Pointe mit echtem kleinen Überraschungsmoment
- lieber schlicht und wirklich lustig als besonders clever oder kompliziert
- keine erfundenen Wörter, keine kaputte Grammatik, keine sinnlose Absurdität
- Wortspiele nur, wenn sie im Deutschen ganz selbstverständlich funktionieren
- "Warum ...? Weil ..." und Kalauer nur gelegentlich, nicht als Standard`
    },
    {
      role: "user",
      content:
`Erzeuge genau EINEN neuen Witz.

ZWINGEND:
- maximal ${MAX_LINES} sichtbare Zeilen
- direkt auf Deutsch denken, NICHT aus einer anderen Sprache übersetzen
- bevorzuge leichte Alltagssituationen, kurze Dialoge oder eine einfache Beobachtung
- besonders gern kleine, liebevolle Alltagsszenen zwischen Ehefrau und Ehemann; nicht jedes Mal, aber deutlich häufiger als andere Themen
- beide Partner auf Augenhöhe; keine abwertenden Eheklischees und keine Demütigung als Pointe
- die Pointe muss logisch aus dem Aufbau folgen und für deutsche Muttersprachler sofort Sinn ergeben
- keine erzwungenen Wortspiele, keine seltsamen Fantasiewörter, keine Übersetzungslogik
- ${edgeInstruction}
- denke vor der Ausgabe still an mehrere verschiedene Möglichkeiten und gib nur die natürlichste/lustigste aus
- prüfe vor Ausgabe still: gutes Deutsch? leicht verständlich? Pointe wirklich logisch? nicht ähnlich zur History? <= ${MAX_LINES} Zeilen?
- ausschließlich den fertigen Witz ausgeben; keine Überschrift, Erklärung, Bewertung oder Meta-Kommentare
${reserveMode ? "- Dieser Witz ist für eine unveröffentlichte Ausfallreserve; er muss genauso gut und vollständig neu sein." : ""}

Bisherige/gesperrte Witze (nur letzte ${HISTORY_LIMIT}):
${historyText}`
    }
  ];
}

function extractText(data) {
  const content = data?.choices?.[0]?.message?.content;

  if (typeof content === "string" && content.trim())
    return content.trim();

  if (Array.isArray(content)) {
    const joined = content.map(part => {
      if (typeof part === "string") return part;
      if (typeof part?.text === "string") return part.text;
      if (typeof part?.content === "string") return part.content;
      return "";
    }).join("").trim();
    if (joined) return joined;
  }

  if (typeof data?.choices?.[0]?.text === "string" && data.choices[0].text.trim())
    return data.choices[0].text.trim();

  return "";
}

async function fetchJson(url, options = {}, timeoutMs = TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const raw = await response.text();

    let data;
    try { data = JSON.parse(raw); }
    catch { data = { raw }; }

    return { response, data, raw };
  } finally {
    clearTimeout(timer);
  }
}

function errorMessage(response, data, raw) {
  return String(
    data?.error?.message ||
    data?.error ||
    data?.message ||
    raw ||
    `HTTP ${response?.status ?? "?"}`
  );
}

function retryable(status) {
  return [408, 425, 429, 500, 502, 503, 504].includes(status);
}

async function postRetry(url, headers, body, attempts = 2) {
  let last = null;

  for (let i = 0; i < attempts; i++) {
    try {
      const { response, data, raw } = await fetchJson(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body)
      });

      if (response.ok) return { ok: true, response, data, attempt: i + 1 };

      last = {
        status: response.status,
        message: errorMessage(response, data, raw)
      };

      if (!retryable(response.status) || i === attempts - 1)
        break;

      const retryAfter = Number(response.headers.get("retry-after"));
      const wait = Number.isFinite(retryAfter) && retryAfter > 0
        ? Math.min(retryAfter * 1000, 25000)
        : [2500, 7000, 14000][Math.min(i, 2)];

      await sleep(wait);
    } catch (err) {
      last = {
        status: null,
        message: err.name === "AbortError" ? "Timeout" : String(err.message || err)
      };
      if (i < attempts - 1) await sleep([2500, 7000][Math.min(i, 1)]);
    }
  }

  return { ok: false, error: last || { status: null, message: "unbekannter Fehler" } };
}

async function listGroqModels() {
  const { response, data, raw } = await fetchJson("https://api.groq.com/openai/v1/models", {
    headers: {
      "Authorization": `Bearer ${GROQ_API_KEY}`,
      "Content-Type": "application/json"
    }
  });
  if (!response.ok) throw new Error(errorMessage(response, data, raw));

  const ids = new Set((data?.data || []).map(x => x?.id).filter(Boolean));
  const preferred = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b"
  ];
  return preferred.filter(id => ids.has(id));
}

function groqBody(model, messages) {
  const body = {
    model,
    messages,
    temperature: 0.84,
    top_p: 0.92,
    stream: false
  };

  if (model.startsWith("qwen/")) {
    body.reasoning_effort = "none";
    body.max_completion_tokens = 360;
  } else {
    body.reasoning_effort = "low";
    body.include_reasoning = false;
    body.max_completion_tokens = 900;
  }

  return body;
}

async function runGroq(messages, knownJokes) {
  const diagnostics = [];
  if (!GROQ_API_KEY)
    return { provider: "Groq", status: "error", error: "GROQ_API_KEY fehlt", diagnostics };
  let models;

  try {
    models = await listGroqModels();
  } catch (err) {
    return { provider: "Groq", status: "error", error: `Modellliste: ${err.message}`, diagnostics };
  }

  for (const model of models.slice(0, 3)) {
    const result = await postRetry(
      "https://api.groq.com/openai/v1/chat/completions",
      {
        "Authorization": `Bearer ${GROQ_API_KEY}`,
        "Content-Type": "application/json"
      },
      groqBody(model, messages),
      2
    );

    if (!result.ok) {
      diagnostics.push({
        model,
        http_status: result.error.status,
        error: result.error.message
      });
      continue;
    }

    const check = validateJoke(extractText(result.data), knownJokes);
    diagnostics.push({
      model,
      returned_model: result.data?.model || model,
      http_status: result.response.status,
      valid: check.ok,
      validation: check.ok ? "ok" : check.reason,
      usage: result.data?.usage ?? null
    });

    if (check.ok) {
      return {
        timestamp: new Date().toISOString(),
        provider: "Groq",
        requested_model: model,
        returned_model: result.data?.model || model,
        status: "ok",
        text: check.text,
        lines: check.lines,
        local_similarity: check.similarity,
        diagnostics,
        usage: result.data?.usage ?? null
      };
    }
  }

  return {
    timestamp: new Date().toISOString(),
    provider: "Groq",
    status: "error",
    error: "kein gültiger Witz nach Groq-Modell-Fallback",
    diagnostics
  };
}

function isZero(v) {
  const n = Number(v);
  return Number.isFinite(n) && n === 0;
}

function scoreOpenRouter(m) {
  const s = `${m.id} ${m.name || ""} ${m.description || ""}`.toLowerCase();
  let score = 0;
  const boosts = [
    ["multilingual", 100],
    ["instruct", 70],
    ["instruction", 70],
    ["chat", 50],
    ["general", 35],
    ["qwen", 30],
    ["gemma", 25],
    ["llama", 22],
    ["mistral", 22],
    ["glm", 18]
  ];
  for (const [term, points] of boosts) if (s.includes(term)) score += points;
  return score;
}

async function listOpenRouterModels() {
  const { response, data, raw } = await fetchJson(
    "https://openrouter.ai/api/v1/models?sort=throughput-high-to-low",
    {
      headers: {
        "Authorization": `Bearer ${OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      }
    }
  );

  if (!response.ok) throw new Error(errorMessage(response, data, raw));

  const blocked = [
    "safety", "guard", "moderation", "classifier", "embedding", "rerank",
    "speech", "vision", "omni", "reasoning-only", "reasoning only",
    "thinking-only", "thinking only", "agentic", "coding agent",
    "r1-", "/r1", "deepseek-r1"
  ];

  return (data?.data || [])
    .filter(m => typeof m?.id === "string" && m.id.endsWith(":free"))
    .filter(m => isZero(m?.pricing?.prompt) && isZero(m?.pricing?.completion))
    .filter(m => m?.architecture?.input_modalities?.includes("text"))
    .filter(m => m?.architecture?.output_modalities?.includes("text"))
    .filter(m => m?.supported_parameters?.includes("max_tokens"))
    .filter(m => m?.supported_parameters?.includes("temperature"))
    .filter(m => m?.reasoning?.mandatory !== true)
    .filter(m => {
      const s = ` ${m.id} ${m.name || ""} ${m.description || ""} `.toLowerCase();
      return !blocked.some(term => s.includes(term));
    })
    .sort((a, b) => scoreOpenRouter(b) - scoreOpenRouter(a))
    .slice(0, OPENROUTER_MAX_MODELS);
}

function openRouterBody(model, messages) {
  const supports = new Set(model.supported_parameters || []);

  const body = {
    model: model.id,
    messages,
    temperature: 0.82,
    top_p: 0.92,
    max_tokens: 360,
    stream: false,
    provider: {
      allow_fallbacks: true,
      sort: "throughput",
      require_parameters: true
    }
  };

  // Force reasoning off whenever the model advertises that parameter.
  if (supports.has("reasoning_effort")) {
    body.reasoning_effort = "none";
  } else if (supports.has("reasoning")) {
    body.reasoning = { effort: "none" };
  }

  return body;
}

async function runOpenRouter(messages, knownJokes) {
  const diagnostics = [];
  if (!OPENROUTER_API_KEY)
    return { provider: "OpenRouter", status: "error", error: "OPENROUTER_API_KEY fehlt", diagnostics };
  let models;

  try {
    models = await listOpenRouterModels();
  } catch (err) {
    return { provider: "OpenRouter", status: "error", error: `Modellliste: ${err.message}`, diagnostics };
  }

  if (!models.length) {
    return { provider: "OpenRouter", status: "error", error: "kein geeignetes aktuelles $0-Textmodell", diagnostics };
  }

  // Deliberately one model at a time:
  // provider fallback happens inside each model; invalid/empty content then moves to the next model.
  for (const model of models) {
    const result = await postRetry(
      "https://openrouter.ai/api/v1/chat/completions",
      {
        "Authorization": `Bearer ${OPENROUTER_API_KEY}`,
        "Content-Type": "application/json",
        "X-OpenRouter-Metadata": "enabled"
      },
      openRouterBody(model, messages),
      2
    );

    if (!result.ok) {
      diagnostics.push({
        model: model.id,
        http_status: result.error.status,
        error: result.error.message
      });
      continue;
    }

    const rawText = extractText(result.data);
    const reasoningTokens = Number(
      result.data?.usage?.completion_tokens_details?.reasoning_tokens ?? 0
    );

    const check = validateJoke(rawText, knownJokes);

    diagnostics.push({
      model: model.id,
      returned_model: result.data?.model || model.id,
      http_status: result.response.status,
      reasoning_tokens: reasoningTokens,
      valid: check.ok,
      validation: check.ok ? "ok" : check.reason,
      usage: result.data?.usage ?? null,
      routing: result.data?.openrouter_metadata ?? null
    });

    if (check.ok) {
      return {
        timestamp: new Date().toISOString(),
        provider: "OpenRouter",
        requested_model: model.id,
        returned_model: result.data?.model || model.id,
        status: "ok",
        text: check.text,
        lines: check.lines,
        local_similarity: check.similarity,
        diagnostics,
        usage: result.data?.usage ?? null
      };
    }
  }

  return {
    timestamp: new Date().toISOString(),
    provider: "OpenRouter",
    status: "error",
    error: "kein gültiger Witz nach 3 kontrollierten Free-Modellen",
    diagnostics
  };
}

async function providerChain(messages, knownJokes) {
  const attempts = [];

  const groq = await runGroq(messages, knownJokes);
  attempts.push(groq);
  if (groq.status === "ok") return { final: groq, attempts };

  const openrouter = await runOpenRouter(messages, knownJokes);
  attempts.push(openrouter);
  if (openrouter.status === "ok") return { final: openrouter, attempts };

  // One complete retry after a short pause if both providers failed.
  await sleep(10000);

  const groqRetry = await runGroq(messages, knownJokes);
  groqRetry.retry_round = 2;
  attempts.push(groqRetry);
  if (groqRetry.status === "ok") return { final: groqRetry, attempts };

  const openrouterRetry = await runOpenRouter(messages, knownJokes);
  openrouterRetry.retry_round = 2;
  attempts.push(openrouterRetry);
  if (openrouterRetry.status === "ok") return { final: openrouterRetry, attempts };

  return { final: null, attempts };
}

async function createReserveJoke(contextJokes, knownJokes, edgeMode) {
  const messages = buildMessages(contextJokes, edgeMode, true);

  // Reserve generation stays on Groq first; OpenRouter only if Groq fails.
  const groq = await runGroq(messages, knownJokes);
  if (groq.status === "ok") return groq;

  return await runOpenRouter(messages, knownJokes);
}

function requireWebhookUrl() {
  const raw = String(process.env[SETTINGS.webhookSecretEnv] || "").trim();
  if (!raw) throw new Error(`${SETTINGS.webhookSecretEnv} fehlt`);
  const url = new URL(raw);
  if (url.protocol !== "https:") throw new Error("Webhook URL muss HTTPS verwenden");
  url.searchParams.set("wait", "true");
  return url.toString();
}

async function sendDiscord(text) {
  const payload = {
    content: text,
    username: SETTINGS.senderName,
    avatar_url: SETTINGS.avatarUrl,
    allowed_mentions: { parse: [] }
  };

  const { response, data, raw } = await fetchJson(
    requireWebhookUrl(),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "joke-machine"
      },
      body: JSON.stringify(payload)
    },
    SETTINGS.webhookTimeoutMs
  );

  if (!response.ok) throw new Error(`Discord HTTP ${response.status}: ${errorMessage(response, data, raw)}`);
  return response.status;
}

function attemptSummary(attempts) {
  return attempts.map(a => ({
    provider: a.provider,
    status: a.status,
    returned_model: a.returned_model || null,
    error: a.error || null,
    diagnostics: a.diagnostics || []
  }));
}

(async () => {
  const history = readJson(HISTORY_FILE, []);
  let reserve = readJson(RESERVE_FILE, []);
  if (!Array.isArray(history)) throw new Error("history.json muss ein Array enthalten");
  if (!Array.isArray(reserve)) throw new Error("reserve.json muss ein Array enthalten");

  const historyJokes = knownJokesFromHistory(history);
  const reserveJokes = reserve.map(x => cleanText(x?.text)).filter(Boolean);
  const knownJokes = [...historyJokes, ...reserveJokes];
  const contextJokes = knownJokes.slice(-HISTORY_LIMIT);

  const edgeMode = Math.random() < 0.125;
  const messages = buildMessages(contextJokes, edgeMode, false);

  console.log(`History: ${history.length} | Kontext: ${contextJokes.length} | Reserve: ${reserve.length}/${RESERVE_TARGET}`);
  console.log(`Humor: ${edgeMode ? "leicht frech" : "leicht/alltäglich"}`);

  const chain = await providerChain(messages, knownJokes);
  let final = chain.final;
  let reserveCandidate = null;

  for (const attempt of chain.attempts) {
    console.log(`${attempt.provider}: ${attempt.status === "ok" ? "OK" : "FEHLER"}`);
    if (attempt.status === "ok") break;
  }

  if (!final && reserve.length) {
    const item = reserve[0];
    reserveCandidate = item;
    final = {
      timestamp: new Date().toISOString(),
      provider: "Reserve",
      source_provider: item.source_provider,
      source_model: item.source_model,
      status: "ok",
      text: item.text,
      note: "Externe APIs ausgefallen; zuvor erzeugter, noch nie veröffentlichter KI-Witz."
    };
    console.log("Reserve: BEREIT");
  }

  if (!final) {
    const runRecord = {
      timestamp: new Date().toISOString(),
      provider: "none",
      status: "error",
      text: null,
      edge_mode: edgeMode,
      attempts: attemptSummary(chain.attempts),
      reserve_used: null,
      reserve_added: 0,
      reserve_after: reserve.length,
      error: "Provider + Retry fehlgeschlagen und Reserve leer."
    };
    history.push(runRecord);
    writeJson(HISTORY_FILE, history);
    writeJson(LATEST_FILE, { ...runRecord, context_jokes_sent: contextJokes.length, reserve_count: reserve.length });
    console.error("KEIN ERGEBNIS: Provider + Retry fehlgeschlagen und Reserve leer.");
    process.exitCode = 1;
    return;
  }

  // Refill before publishing, and only on a healthy live-provider run.
  const reserveAdded = [];
  if (!reserveCandidate && reserve.length < RESERVE_TARGET) {
    for (let i = 0; i < RESERVE_REFILL_PER_RUN && reserve.length + reserveAdded.length < RESERVE_TARGET; i++) {
      const nowKnown = [...knownJokes, final.text, ...reserveAdded.map(x => x.text)];
      const r = await createReserveJoke(nowKnown.slice(-HISTORY_LIMIT), nowKnown, Math.random() < 0.10);
      if (r.status !== "ok") break;
      reserveAdded.push({
        created_at: new Date().toISOString(),
        source_provider: r.provider,
        source_model: r.returned_model || r.requested_model || null,
        text: r.text
      });
    }
  }

  let webhookStatus;
  try {
    webhookStatus = await sendDiscord(final.text);
  } catch (err) {
    const runRecord = {
      timestamp: new Date().toISOString(),
      provider: final.provider,
      status: "send_error",
      text: final.text,
      edge_mode: edgeMode,
      attempts: attemptSummary(chain.attempts),
      reserve_used: null,
      reserve_added: 0,
      reserve_after: reserve.length,
      error: err.message
    };
    history.push(runRecord);
    writeJson(HISTORY_FILE, history);
    writeJson(LATEST_FILE, { ...runRecord, context_jokes_sent: contextJokes.length, reserve_count: reserve.length });
    console.error(`Discord: FEHLER (${err.message})`);
    process.exitCode = 1;
    return;
  }

  if (reserveCandidate) reserve.shift();
  reserve.push(...reserveAdded);

  const runRecord = {
    timestamp: new Date().toISOString(),
    provider: final.provider,
    status: "ok",
    text: final.text,
    edge_mode: edgeMode,
    attempts: attemptSummary(chain.attempts),
    reserve_used: reserveCandidate ? {
      source_provider: reserveCandidate.source_provider,
      source_model: reserveCandidate.source_model
    } : null,
    reserve_added: reserveAdded.length,
    reserve_after: reserve.length,
    webhook_status: webhookStatus
  };

  history.push(runRecord);
  const latest = { ...runRecord, context_jokes_sent: contextJokes.length, reserve_count: reserve.length };
  writeJson(HISTORY_FILE, history);
  writeJson(RESERVE_FILE, reserve);
  writeJson(LATEST_FILE, latest);

  console.log("\n--- ERGEBNIS ---");
  console.log(`\n${final.provider}${final.returned_model ? ` (${final.returned_model})` : ""}:`);
  console.log(final.text);
  console.log(`\nDiscord: OK (HTTP ${webhookStatus})`);
  console.log(`Reserve: ${reserve.length}/${RESERVE_TARGET}`);
})().catch(err => {
  console.error("\nFATALER FEHLER:", err?.message || String(err));
  process.exitCode = 1;
});
