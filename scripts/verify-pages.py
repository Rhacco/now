import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

base = os.environ["PAGE_URL"].rstrip("/") + "/"
paths = ("gw/index.html",)
static_pages = {path: (Path("_site") / path).read_bytes()
                for path in ("index.html", "jmac/index.html")}
expected_times = {}
expected_layouts = {}
layout_pattern = re.compile(rb'<meta name="rhacco-layout" content="([0-9a-f]{16})">')
for path in paths:
    local = Path("_site") / path
    if not local.is_file():
        raise SystemExit(f"{path}: missing from the Pages artifact")
    content = local.read_bytes()
    layout = layout_pattern.search(content)
    if not layout:
        raise SystemExit(f"{path}: artifact lacks the shared page layout")
    expected_layouts[path] = layout[1]
    stamp = re.search(rb'<meta name="page-generated-at-ms" content="(\d+)">', content)
    if not stamp:
        raise SystemExit(f"{path}: artifact lacks a generation time")
    expected_times[path] = int(stamp.group(1))

for attempt in range(7):
    failures = []
    now_ms = int(time.time() * 1000)
    for path in (*static_pages, *expected_times):
        url = urllib.parse.urljoin(base, path) + "?check=" + os.environ["RUN_ID"] + "-" + str(attempt)
        try:
            request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=12) as response:
                content = response.read(1000000)
            if path in static_pages:
                if content != static_pages[path]:
                    failures.append(f"{path}: published page differs from the Pages artifact")
                continue
            layout = layout_pattern.search(content)
            if not layout or layout[1] != expected_layouts[path]:
                raise ValueError("published page lacks the current shared layout")
            expected = expected_times[path]
            actual = re.search(rb'<meta name="page-generated-at-ms" content="(\d+)">', content)
            if not actual:
                raise ValueError("published page lacks a generation time")
            published = int(actual.group(1))
            age_seconds = max(0, (now_ms - published) // 1000)
            lag_seconds = max(0, (expected - published) // 1000)
            print(f"{path}: public age {age_seconds}s, behind artifact {lag_seconds}s")
            if age_seconds > 900 or lag_seconds > 120:
                failures.append(f"{path}: Pages is serving an old page")
        except Exception as error:
            failures.append(f"{path}: public page could not be checked: {error}")
    if not failures:
        break
    if attempt == 6:
        raise SystemExit("\n".join(failures))
    print("Waiting 10s for Pages to serve the new version...")
    time.sleep(10)
