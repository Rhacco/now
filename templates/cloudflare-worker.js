// Cloudflare Cron itself can run up to every minute.

// Necessary variables/secrets: GH_OWNER, GH_PAT, GH_REPO, GH_REPO_BRANCH
// Configure/add: Matching <ACTION>_ENABLED and <ACTION>_WORKFLOW variables in Cloudflare

const ACTIONS = [
  { key: "A1" },
  { key: "A2", minutes: [19, 49] },
];

export default {
  async scheduled(controller, env, ctx) {
    const minute = new Date(controller.scheduledTime).getUTCMinutes();

    const jobs = ACTIONS
      .filter(({ key, minutes }) =>
        String(env[`${key}_ENABLED`] ?? "").trim() !== "0" &&
        (!minutes || minutes.includes(minute)),
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

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${required(env, "GH_PAT")}`,
      "X-GitHub-Api-Version": "2026-03-10",
      "User-Agent": "cloudflare-github-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: repo_branch }),
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
