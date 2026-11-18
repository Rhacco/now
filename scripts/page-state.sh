set -euo pipefail

worker="${2:-}"
case "${worker}:${STATE_BRANCH:-}" in
  worker-gw2a:gw2-action-state|worker-rnews:rhacco-news-state) ;;
  *) echo "Invalid worker or state branch" >&2; exit 1 ;;
esac

restore() {
  mkdir -p "${worker}/data"

  if git ls-remote --exit-code --heads origin "${STATE_BRANCH}" >/dev/null 2>&1; then
    git fetch --force origin "${STATE_BRANCH}:refs/remotes/origin/${STATE_BRANCH}"

    if git cat-file -e "origin/${STATE_BRANCH}:${worker}/data/cache.json" 2>/dev/null; then
      git show "origin/${STATE_BRANCH}:${worker}/data/cache.json" > "${RUNNER_TEMP}/${STATE_BRANCH}-cache.json"
      python3 - "${RUNNER_TEMP}/${STATE_BRANCH}-cache.json" <<'PY'
import json
import sys
from pathlib import Path

cache = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert isinstance(cache, dict) and cache.get("version") in (1, 2)
assert isinstance(cache.get("sources"), dict)
PY
      mv "${RUNNER_TEMP}/${STATE_BRANCH}-cache.json" "${worker}/data/cache.json"
      echo "Persistent cache restored from ${STATE_BRANCH}."
    else
      echo "${STATE_BRANCH} exists without a cache; refusing to replace saved state." >&2
      exit 1
    fi
  else
    rc=$?
    if [ "${rc}" -eq 2 ]; then
      echo "${STATE_BRANCH} does not exist yet; the first successful run will create it."
    else
      echo "Unable to inspect ${STATE_BRANCH}." >&2
      exit "${rc}"
    fi
  fi
}

publish() {
  test -s "${worker}/index.html"
  python3 - "${worker}/data/cache.json" <<'PY'
import json
import sys
from pathlib import Path
state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if state.get("version") != 2 or not isinstance(state.get("sources"), dict) or not any(
    isinstance(item, dict) and item.get("data") for item in state["sources"].values()
):
    raise SystemExit("Refusing to replace state with empty or invalid data")
PY

  STATE_DIR="$(mktemp -d "${RUNNER_TEMP}/page-state.XXXXXX")"
  trap 'git worktree remove --force "${STATE_DIR}" 2>/dev/null || rmdir "${STATE_DIR}" 2>/dev/null || true' EXIT

  git config user.name "github-actions[bot]"
  git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

  if git show-ref --verify --quiet "refs/remotes/origin/${STATE_BRANCH}"; then
    git worktree add --detach "${STATE_DIR}" "origin/${STATE_BRANCH}"
    STATE_EXISTS=1
  else
    git worktree add --detach "${STATE_DIR}" HEAD
    git -C "${STATE_DIR}" switch --orphan "${STATE_BRANCH}"
    STATE_EXISTS=0
  fi

  if [ -L "${STATE_DIR}/${worker}" ] || [ -L "${STATE_DIR}/${worker}/data" ] ||
     [ -L "${STATE_DIR}/${worker}/index.html" ] || [ -L "${STATE_DIR}/${worker}/data/cache.json" ]; then
    echo "Unexpected symbolic link at a state file path." >&2
    exit 1
  fi
  mkdir -p "${STATE_DIR}/${worker}/data"

  cp "${worker}/index.html" "${STATE_DIR}/${worker}/index.html"
  cp "${worker}/data/cache.json" "${STATE_DIR}/${worker}/data/cache.json"

  git -C "${STATE_DIR}" add -- "${worker}/index.html" "${worker}/data/cache.json"

  if git -C "${STATE_DIR}" diff --cached --quiet; then
    echo "No state/page update required."
    exit 0
  fi

  if [ "${STATE_EXISTS}" -eq 1 ]; then
    git -C "${STATE_DIR}" commit --amend --no-edit
    expected_remote="$(git rev-parse "refs/remotes/origin/${STATE_BRANCH}")"
  else
    git -C "${STATE_DIR}" commit -m "Initialize ${STATE_BRANCH}"
    expected_remote=""
  fi

  new_head="$(git -C "${STATE_DIR}" rev-parse HEAD)"
  for attempt in 1 2 3; do
    if git -C "${STATE_DIR}" push "--force-with-lease=refs/heads/${STATE_BRANCH}:${expected_remote}" \
      origin "HEAD:refs/heads/${STATE_BRANCH}"; then
      break
    fi

    # The remote may have accepted the push despite a broken response.
    for check in 1 2 3; do
      if remote_line="$(git ls-remote --heads origin "refs/heads/${STATE_BRANCH}")"; then
        break
      fi
      if [ "${check}" -eq 3 ]; then
        echo "Unable to verify the remote state after a failed push." >&2
        exit 1
      fi
      sleep "${check}"
    done
    remote_head="${remote_line%%$'\t'*}"
    if [ "${remote_head}" = "${new_head}" ]; then
      echo "State push was accepted despite a broken response."
      break
    fi
    if [ "${remote_head}" != "${expected_remote}" ]; then
      echo "State branch changed remotely; refusing to overwrite it." >&2
      exit 1
    fi
    if [ "${attempt}" -eq 3 ]; then
      echo "State push still failed after three attempts." >&2
      exit 1
    fi
    echo "State push failed; retrying after a short pause."
    sleep "$(( attempt * 2 ))"
  done

}

case "${1:-}" in
  restore) restore ;;
  publish) publish ;;
  *) echo "Usage: page-state.sh restore|publish WORKER" >&2; exit 1 ;;
esac
