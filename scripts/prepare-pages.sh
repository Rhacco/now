set -euo pipefail

rm -rf _site
mkdir -p _site
: > _site/.nojekyll

copy_state_page() {
  local branch="$1"
  local source="$2"
  local target="$3"
  local label="$4"

  local attempt
  for attempt in 1 2 3; do
    if git fetch --force --no-tags origin "${branch}:refs/remotes/origin/${branch}"; then
      break
    fi
    if [ "${attempt}" -eq 3 ]; then
      echo "${label}: unable to fetch ${branch}." >&2
      return 1
    fi
    sleep "${attempt}"
  done

  if ! git cat-file -e "origin/${branch}:${source}" 2>/dev/null; then
    echo "${label}: ${branch}:${source} is missing." >&2
    return 1
  fi

  mkdir -p "$(dirname "${target}")"
  if ! git show "origin/${branch}:${source}" > "${target}" || ! test -s "${target}"; then
    rm -f "${target}"
    echo "${label}: missing or empty page in ${branch}; refusing to publish it." >&2
    return 1
  fi

  if ! grep -Fq "<title>${label}</title>" "${target}" ||
     ! grep -Eq '<meta name="page-generated-at-ms" content="[0-9]+">' "${target}" ||
     ! grep -Fq '</html>' "${target}"; then
    rm -f "${target}"
    echo "${label}: incomplete state page." >&2
    return 1
  fi

  local state_time
  state_time="$(git log -1 --format='%cI' "origin/${branch}" 2>/dev/null || true)"
  echo "Added ${label}: ${branch}:${source} -> ${target} (state ${state_time:-unknown})"
}

copy_state_page "${GW2_STATE_BRANCH}" "worker-gw2a/index.html" "_site/gw/index.html" "GW2 Action"
if ! copy_state_page "${RN_STATE_BRANCH}" "worker-rnews/index.html" "_site/rn/index.html" "Rhacco News"; then
  echo "::warning::No Rhacco News snapshot was available."
fi

python3 - <<'PYTHON'
import re
import time
from pathlib import Path

path = Path("_site/gw/index.html")
match = re.search(rb'<meta name="page-generated-at-ms" content="(\d+)">', path.read_bytes())
if not match:
    raise SystemExit(f"{path}: missing generation time")
age_ms = int(time.time() * 1000) - int(match.group(1))
if age_ms < -120000:
    raise SystemExit(f"{path}: state page has a future timestamp")
if age_ms > 900000:
    raise SystemExit(f"{path}: GW2 Action state page is too old")
PYTHON

jmac_ref="refs/remotes/origin/${JMAC_STATE_BRANCH}"
jmac_snapshot="$(mktemp)"
if git fetch --quiet --force --no-tags origin "refs/heads/${JMAC_STATE_BRANCH}:${jmac_ref}" &&
   git show "${jmac_ref}:worker-jmac/data/web-state.json" > "${jmac_snapshot}"; then
  export JMAC_SNAPSHOT_FILE="${jmac_snapshot}"
else
  echo "::warning::No Joke Machine snapshot was available."
fi
python3 templates/pages/build.py

echo "Pages artifact:"
find _site -maxdepth 2 -type f -print | sort

build_version="$(git rev-parse "origin/${GW2_STATE_BRANCH}")"
git cat-file -e "${build_version}^{commit}"
echo "build_version=${build_version}" >> "${GITHUB_OUTPUT}"
echo "Pages build version: ${build_version} (${GW2_STATE_BRANCH})"
