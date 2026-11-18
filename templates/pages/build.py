import html
import json
import os
import re
from hashlib import sha256
from pathlib import Path
from string import Template


TEMPLATES = Path(__file__).resolve().parent
ROOT = TEMPLATES.parents[1]
SITE = ROOT / "_site"
ARCHIVE = ROOT / "worker-jmac/data/jokes.json"
BASE = Template((TEMPLATES / "base.html").read_text(encoding="utf-8"))
LAYOUT_VERSION = sha256(BASE.template.encode("utf-8")).hexdigest()[:16]

# Workers keep producing standalone pages for their existing state consumers.
# Preserve their page-specific head and body when reusing them for Pages.
WORKER_DOCUMENT = re.compile(
    r'<!doctype html>\s*<html lang="(?P<lang>[a-z-]+)">\s*<head>\s*'
    r'<meta charset="utf-8">\s*<meta name="viewport" content="width=device-width,initial-scale=1">'
    r'(?P<head>.*?)</head>\s*<body>(?P<body>.*)</body>\s*</html>\s*',
    re.DOTALL | re.IGNORECASE,
)


def render_page(head, body, *, lang="en", body_class="", home=False):
    classes = "rhacco-home" if home else f"rhacco-subpage {body_class}".strip()
    return BASE.substitute(
        layout_version=LAYOUT_VERSION,
        lang=lang,
        head=head,
        body=body,
        body_attributes=f' class="{classes}"',
        home_label="Zur Startseite" if lang == "de" else "Back to the home page",
        home_href="./" if home else "../",
        nav_hidden=" hidden" if home else "",
    )


def read_featured_joke():
    try:
        archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
        if isinstance(archive, dict) and archive.get("version") == 1:
            jokes = archive.get("jokes")
            if isinstance(jokes, list) and jokes and isinstance(jokes[0], str) and jokes[0].strip():
                return html.escape(jokes[0].strip())
    except (OSError, ValueError):
        pass
    print("::warning::The featured Joke Machine entry is unavailable.")
    return "Gerade ist kein Witz verfügbar."


def write_page(relative_path, content):
    path = SITE / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main():
    workers = {}
    for path, body_class in (("gw/index.html", "rhacco-gw"), ("rn/index.html", "rhacco-news")):
        source = SITE / path
        if path == "rn/index.html" and not source.is_file():
            continue
        page = WORKER_DOCUMENT.fullmatch(source.read_text(encoding="utf-8"))
        if page is None:
            if path == "rn/index.html":
                source.unlink()
                print(f"::warning::{path}: unsupported worker HTML structure")
                continue
            raise SystemExit(f"{path}: unsupported worker HTML structure")
        workers[path] = render_page(
            page["head"].strip(), page["body"].strip("\n"), lang=page["lang"], body_class=body_class,
        )

    for path, content in workers.items():
        write_page(path, content)

    home_body = (TEMPLATES / "home.html").read_text(encoding="utf-8").rstrip("\n")
    write_page("index.html", render_page(
        '<meta name="color-scheme" content="dark">\n<meta name="theme-color" content="#101114">\n<title>Rhacco</title>',
        home_body, home=True,
    ))

    joke_body = Template((TEMPLATES / "joke.html").read_text(encoding="utf-8").rstrip("\n"))
    repo = os.environ.get("GITHUB_REPOSITORY", "Rhacco/now")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SystemExit("Invalid GITHUB_REPOSITORY for Joke Machine")
    state_url = f"https://raw.githubusercontent.com/{repo}/joke-machine-state/worker-jmac/data/web-state.json"
    write_page("jmac/index.html", render_page(
        '<meta name="color-scheme" content="dark">\n<title>Joke Machine</title>',
        joke_body.substitute(
            joke=read_featured_joke(),
            state_url=html.escape(state_url),
            script=(TEMPLATES / "joke.js").read_text(encoding="utf-8").rstrip("\n"),
        ), lang="de", body_class="rhacco-joke",
    ))


if __name__ == "__main__":
    main()
