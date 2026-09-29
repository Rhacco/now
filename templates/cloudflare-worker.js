// Run Cloudflare Cron every minute; A1 dispatches every second minute.

// Necessary variables/secrets: GH_OWNER, GH_PAT, GH_REPO, GH_REPO_BRANCH
// Configure/add: Matching <ACTION>_ENABLED and <ACTION>_WORKFLOW variables in Cloudflare

const ACTIONS = [
  { key: "A1", everyMinutes: 2 },
  { key: "A2", minutes: [19, 49] },
];

const PREVIEW = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#10131d">
  <link rel="icon" href="data:,">
  <title>Rhacco Scheduler</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
    *{box-sizing:border-box}
    body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 50% 0,#242b47,#10131d 55%);color:#f7f8fc}
    main{width:min(540px,100%)}
    .eyebrow{color:#9ba8cb;font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}
    h1{margin:12px 0 8px;font-size:clamp(34px,7vw,52px);letter-spacing:-.055em;line-height:1.05}
    .intro{margin:0;color:#bbc3d6;line-height:1.6}
    .status{display:inline-flex;align-items:center;gap:9px;margin:26px 0 18px;padding:8px 12px;border:1px solid #3b5c52;border-radius:999px;background:#17332c;color:#bdf2d2;font-size:13px;font-weight:650}
    .dot{width:8px;height:8px;border-radius:50%;background:#62e6a1;box-shadow:0 0 12px #62e6a1}
    .cards{display:grid;gap:12px}
    article{padding:20px;border:1px solid #343b53;border-radius:16px;background:#1b2030;box-shadow:0 16px 40px #070a1530}
    article h2{margin:0 0 6px;font-size:18px}
    article strong{display:block;color:#c9bbff;font-size:23px;letter-spacing:-.025em}
    article p{margin:8px 0 0;color:#aeb8cd;font-size:14px;line-height:1.5}
    footer{margin-top:22px;color:#929cb2;font-size:13px;line-height:1.55}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Rhacco · Automation</div>
    <h1>Scheduler</h1>
    <p class="intro">A small control point for the scheduled GitHub workflows.</p>
    <div class="status"><span class="dot"></span>HTTP preview ready</div>
    <div class="cards">
      <article><h2>Update &amp; Deploy</h2><strong>Every 2 minutes</strong><p>Runs one rotating data worker, then publishes fresh pages.</p></article>
      <article><h2>Joke Machine</h2><strong>At :19 and :49</strong><p>Independent joke check, with no Pages deployment.</p></article>
    </div>
    <footer>Opening this page does not start a workflow. The Cloudflare Cron trigger runs the schedule.</footer>
  </main>
</body>
</html>`;

export default {
  fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    return new Response(request.method === "HEAD" ? null : PREVIEW, {
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
  },

  async scheduled(controller, env, ctx) {
    const minute = new Date(controller.scheduledTime).getUTCMinutes();

    const jobs = ACTIONS
      .filter(({ key, minutes, everyMinutes }) =>
        String(env[`${key}_ENABLED`] ?? "").trim() !== "0" &&
        (!minutes || minutes.includes(minute)) &&
        (!everyMinutes || minute % everyMinutes === 0),
      )
      .map(({ key }) => triggerWorkflow(env, required(env, `${key}_WORKFLOW`)));

    ctx.waitUntil(Promise.all(jobs));
  },
};

async function triggerWorkflow(env, workflow) {
  const owner = encodeURIComponent(required(env, "GH_OWNER"));
  const repo = encodeURIComponent(required(env, "GH_REPO"));
  const repo_branch = required(env, "GH_REPO_BRANCH");
  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;

  // Only the central external trigger rotates its data workers.
  const inputs = workflow === "update-and-deploy.yml" ? { mode: "auto" } : undefined;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${required(env, "GH_PAT")}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "cloudflare-github-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(inputs ? { ref: repo_branch, inputs } : { ref: repo_branch }),
  });

  if (!response.ok) {
    throw new Error(`GitHub dispatch failed: ${response.status} ${await response.text()}`);
  }
}

function required(env, name) {
  const value = String(env[name] ?? "").trim();
  if (!value) throw new Error(`Missing variable: ${name}`);
  return value;
}
