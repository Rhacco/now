(() => {
  const board = document.querySelector(".rhacco-joke-board");
  const container = document.querySelector("#jmac-rows");
  const rowTemplate = container.firstElementChild;
  const models = ["gpt", "qwen", "archive"];
  const visibleRows = Number(board.dataset.visibleRows);
  const timeFormat = new Intl.DateTimeFormat("de-DE", { dateStyle: "short", timeStyle: "short" });
  const pollInterval = 20_000;
  const pagesMaxAge = 3 * 60_000;
  const statePollInterval = 60_000;
  let lastTimestamp = 0;
  let lastPagesAtMs = 0;
  let lastStateCheckAt = 0;
  let updateInFlight = false;

  function renderRow(row, index) {
    const view = rowTemplate.cloneNode(true);
    const header = view.querySelector(".rhacco-joke-row-header");
    const time = header.querySelector("time");
    time.dateTime = row.timestamp;
    time.textContent = timeFormat.format(new Date(row.timestamp));
    header.hidden = false;
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

  function readSnapshot(page) {
    const snapshot = page.querySelector("#jmac-snapshot")?.textContent;
    return snapshot ? JSON.parse(snapshot) : null;
  }

  async function loadSnapshot(path, fromState = false) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12_000);
    try {
      const response = await fetch(url.toString(), { cache: "no-store", signal: controller.signal });
      if (!response.ok) return null;
      if (fromState) return await response.json();
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      return readSnapshot(page);
    } finally {
      clearTimeout(timeout);
    }
  }

  function applyUpdate(state, fromState = false) {
    if (state?.version !== 2 || !Array.isArray(state.rows)) return;
    const rows = state.rows.slice(-visibleRows).reverse();
    const timestamp = Date.parse(rows[0]?.timestamp);
    const latestAllowed = Date.now() + 5 * 60_000;
    if (!rows.length || timestamp > latestAllowed
        || !rows.every((row, index) => {
          const rowTime = Date.parse(row?.timestamp);
          return Number.isFinite(rowTime) && (index === 0 || rowTime <= Date.parse(rows[index - 1].timestamp))
            && models.every(model => typeof row[model] === "string" && row[model].trim());
        })) return;
    if (!fromState) lastPagesAtMs = Math.max(lastPagesAtMs, timestamp);
    if (timestamp <= lastTimestamp) return;
    container.replaceChildren(...rows.map(renderRow));
    lastTimestamp = timestamp;
  }

  async function checkForUpdate() {
    if (updateInFlight || document.hidden) return;
    updateInFlight = true;
    try {
      applyUpdate(await loadSnapshot(window.location.pathname));
    } catch {
      // Keep the latest complete list if Pages is temporarily unavailable.
    }
    try {
      const now = Date.now();
      if (now > lastPagesAtMs + pagesMaxAge && now > lastStateCheckAt + statePollInterval) {
        lastStateCheckAt = now;
        applyUpdate(await loadSnapshot(board.dataset.stateUrl, true), true);
      }
    } catch {
      // Retry on the next eligible check.
    } finally {
      updateInFlight = false;
    }
  }

  try {
    applyUpdate(readSnapshot(document));
  } catch {
    // The next check can recover a missing or unreadable snapshot.
  }
  setInterval(checkForUpdate, pollInterval);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkForUpdate();
  });
  if (Date.now() > lastPagesAtMs + pagesMaxAge) checkForUpdate();
})();
