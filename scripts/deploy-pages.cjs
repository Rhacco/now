module.exports = async ({ github, context, core }) => {
  const artifactId = Number(process.env.ARTIFACT_ID);
  const buildVersion = process.env.BUILD_VERSION;
  if (!Number.isSafeInteger(artifactId) || !/^[a-f0-9]{40}$/.test(buildVersion)) {
    throw new Error('Missing Pages artifact or valid state commit');
  }
  const {owner, repo} = context.repo;
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const temporary = error => [429, 500, 502, 503, 504].includes(error?.status);
  // Same GitHub API and job OIDC token used by actions/deploy-pages.
  const token = await core.getIDToken();
  const create = version => github.request('POST /repos/{owner}/{repo}/pages/deployments', {
    owner, repo, artifact_id: artifactId, pages_build_version: version, oidc_token: token
  });
  const status = id => github.request('GET /repos/{owner}/{repo}/pages/deployments/{id}', {
    owner, repo, id
  });

  // A broken POST response does not prove that GitHub rejected the deployment.
  // Query by its build commit before sending the artifact again.
  async function findAccepted(version) {
    let uncertain = false;
    for (let attempt = 1; attempt <= 3; attempt++) {
      await pause(attempt * 2000);
      try {
        const {data} = await status(version);
        if (data?.status) return true;
        throw new Error('Pages returned an incomplete deployment status');
      } catch (error) {
        if (error.status === 404) continue;
        if (temporary(error)) { uncertain = true; continue; }
        throw error;
      }
    }
    if (uncertain) throw new Error('Could not verify whether Pages accepted the deployment');
    return false;
  }

  async function createOrRecover(version) {
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        return (await create(version)).data;
      } catch (error) {
        if (!temporary(error) && !(error.status === 422 && attempt > 1)) throw error;
        core.warning('Pages creation response failed (HTTP ' + (error.status || '?') + '); checking the deployment status.');
        if (await findAccepted(version)) return {id: version};
        if (!temporary(error) || attempt === 3) throw error;
        await pause(attempt * 3000);
      }
    }
  }

  let version = buildVersion;
  let deployment;
  try {
    deployment = await createOrRecover(version);
  } catch (error) {
    if (error.status !== 404 || version === context.sha) throw error;
    // Some Pages installations accept only the workflow commit.
    // The public freshness check below will still reject a stuck site.
    core.warning('Pages rejected the state commit; retrying with the workflow commit');
    version = context.sha;
    deployment = await createOrRecover(version);
  }
  const id = deployment?.id || deployment?.status_url?.split('/').pop();
  if (!id) throw new Error('Pages returned an incomplete deployment');
  let pageUrl = deployment.page_url;
  if (!pageUrl) {
    // Recovered a deployment whose POST response was lost.
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const {data} = await github.request('GET /repos/{owner}/{repo}/pages', {owner, repo});
        pageUrl = data.html_url;
        break;
      } catch (error) {
        if (!temporary(error) || attempt === 3) throw error;
        await pause(attempt * 2000);
      }
    }
  }
  if (!pageUrl) throw new Error('Pages did not return a public URL');
  core.info('Pages deployment ID: ' + id + ', build commit: ' + version);
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    await pause(5000);
    let data;
    try {
      ({data} = await status(id));
    } catch (error) {
      if (!temporary(error) && error.status !== 404) throw error;
      core.warning('Pages status temporarily unavailable (HTTP ' + (error.status || '?') + ').');
      continue;
    }
    if (data.status === 'succeed') {
      core.setOutput('page_url', pageUrl);
      core.info('Pages reports success: ' + pageUrl);
      return;
    }
    if (['deployment_failed', 'deployment_content_failed', 'deployment_cancelled', 'deployment_lost'].includes(data.status)) {
      throw new Error('Pages deployment failed: ' + data.status);
    }
    core.info('Pages status: ' + (data.status || 'pending'));
  }
  throw new Error('Pages deployment did not finish within 3 minutes');
};
