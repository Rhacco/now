(() => {
  const board = document.querySelector(".rhacco-joke-board");
  const updated = document.querySelector("#jmac-updated");
  const cards = Object.fromEntries(
    ["gpt", "qwen", "archive"].map(name => [name, document.querySelector(`#jmac-${name}`)]),
  );
  let lastTimestamp = 0;

  async function refresh() {
    if (document.hidden) return;
    try {
      const response = await fetch(board.dataset.stateUrl, {
        cache: "no-cache",
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok) return;
      const state = await response.json();
      if (state?.version !== 2 || !Array.isArray(state.rows)) return;
      const row = state.rows.at(-1);
      const timestamp = Date.parse(row?.timestamp);
      if (!Number.isFinite(timestamp) || timestamp <= lastTimestamp
          || !Object.keys(cards).every(name => typeof row[name] === "string" && row[name].trim())) return;
      for (const name of Object.keys(cards)) cards[name].textContent = row[name];
      updated.dateTime = row.timestamp;
      updated.textContent = new Date(timestamp).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
      updated.hidden = false;
      lastTimestamp = timestamp;
    } catch {
      // Keep the last complete row during a temporary fetch failure.
    }
  }

  refresh();
  setInterval(refresh, 60_000);
  document.addEventListener("visibilitychange", refresh);
})();
