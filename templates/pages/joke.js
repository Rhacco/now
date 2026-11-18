(() => {
  const board = document.querySelector(".rhacco-joke-board");
  const updated = document.querySelector("#jmac-updated");
  const container = document.querySelector("#jmac-rows");
  const rowTemplate = container.firstElementChild;
  const models = ["gpt", "qwen", "archive"];
  const visibleRows = 10;
  let lastTimestamp = 0;

  function renderRow(row, index) {
    const view = rowTemplate.cloneNode(true);
    for (const model of models) {
      const id = `jmac-${model}`;
      const text = view.querySelector(`#${id}`);
      text.textContent = row[model];
      if (index === 0) continue;

      const heading = view.querySelector(`#${id}-label`);
      heading.id += `-${index}`;
      heading.parentElement.setAttribute("aria-labelledby", heading.id);
      text.id += `-${index}`;
    }
    return view;
  }

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
      const rows = state.rows.slice(-visibleRows).reverse();
      const row = rows[0];
      const timestamp = Date.parse(row?.timestamp);
      if (!Number.isFinite(timestamp) || timestamp <= lastTimestamp
          || !rows.every(entry => models.every(model => typeof entry?.[model] === "string" && entry[model].trim()))) return;
      container.replaceChildren(...rows.map(renderRow));
      updated.dateTime = row.timestamp;
      updated.textContent = new Date(timestamp).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
      updated.hidden = false;
      lastTimestamp = timestamp;
    } catch {
      // Keep the last complete list during a temporary fetch failure.
    }
  }

  refresh();
  setInterval(refresh, 60_000);
  document.addEventListener("visibilitychange", refresh);
})();
