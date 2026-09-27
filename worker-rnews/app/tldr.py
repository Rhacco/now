"""Small, bounded enrichment of news with official, matching evidence."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
CVE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.I)
GHSA = re.compile(r"^GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$", re.I)
MODEL = "@cf/meta/llama-3.1-8b-instruct"


def compact(value: str, maximum: int = 450) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    if len(value) <= maximum:
        return value
    return value[:maximum].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def kev_items(raw: str, maximum: int) -> list[dict[str, str]]:
    payload = json.loads(raw)
    records = payload.get("vulnerabilities") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("empty or invalid KEV catalog")
    result = []
    for item in sorted((v for v in records if isinstance(v, dict)), key=lambda v: str(v.get("dateAdded", "")), reverse=True):
        cve = str(item.get("cveID", "")).upper()
        day = str(item.get("dateAdded", ""))
        if not CVE.fullmatch(cve) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        vendor = compact(str(item.get("vendorProject") or ""), 90)
        product = compact(str(item.get("product") or ""), 90)
        if not (vendor and product):
            continue
        label = f"{vendor} {product}".strip()
        action = compact(str(item.get("requiredAction") or ""), 190)
        brief = f"CISA added {cve} for {label} to its catalog of vulnerabilities exploited in the wild."
        if action:
            brief += " " + action
        result.append({
            "title": f"{label}: {cve} added to CISA's exploited vulnerabilities list",
            "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext=" + cve,
            "published": day + "T00:00:00Z",
            "summary": brief[:500],
            "tldr": compact(brief, 380),
            "tldr_label": "TLDR · CISA KEV",
        })
        if len(result) >= maximum:
            break
    if not result:
        raise ValueError("KEV catalog has no valid entries")
    return result


def gov_content_url(article_url: str) -> str:
    parts = urllib.parse.urlsplit(article_url)
    if parts.scheme != "https" or parts.netloc.lower() not in {"www.gov.uk", "gov.uk"}:
        return ""
    if not re.fullmatch(r"/[a-z0-9/-]{1,230}", parts.path) or "//" in parts.path:
        return ""
    return "https://www.gov.uk/api/content" + parts.path


def gov_brief(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict) or payload.get("locale", "en") != "en":
        return "", ""
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    body = compact(str(details.get("body") or ""), 3500)
    intro = compact(str(details.get("intro") or payload.get("description") or ""), 500)
    text = body or intro
    if len(text) < 90:
        return "", ""
    lead = intro if len(intro) >= 75 else text
    sentences = re.split(r"(?<=[.!?])\s+", lead)
    short = " ".join(sentences[:2])
    return text, compact(short, 360)


def ghsa_brief(payload: Any, cve: str) -> dict[str, str] | None:
    if not isinstance(payload, list):
        return None
    for row in payload:
        if not isinstance(row, dict) or str(row.get("cve_id") or "").upper() != cve:
            continue
        if row.get("withdrawn_at") or row.get("type", "reviewed") != "reviewed":
            continue
        ghsa = str(row.get("ghsa_id") or "")
        if not GHSA.fullmatch(ghsa):
            continue
        vulnerabilities = row.get("vulnerabilities") or []
        packages = []
        for vul in vulnerabilities[:3]:
            package = vul.get("package") if isinstance(vul, dict) else {}
            if not isinstance(package, dict):
                continue
            name = compact(str(package.get("name") or ""), 60)
            fixed = compact(str(vul.get("first_patched_version") or ""), 40)
            if name:
                packages.append((name, fixed))
        message = f"GitHub's reviewed advisory {ghsa} also lists {cve}."
        if packages:
            name, fixed = packages[0]
            message += f" The listed package is {name}."
            if fixed:
                message += f" The first patched version listed is {fixed}."
        return {
            "title": compact(str(row.get("summary") or f"GitHub advisory for {cve}"), 150),
            "url": "https://github.com/advisories/" + ghsa,
            "published": str(row.get("published_at") or ""),
            "summary": compact(message, 360),
            "tldr": compact(message, 360),
            "tldr_label": "TLDR · GitHub Advisory",
        }
    return None


def first_doi(text: str) -> str:
    found = DOI.search(text)
    return found.group(0).rstrip(".,;)").lower() if found else ""


def epmc_study(payload: Any, doi: str) -> dict[str, str] | None:
    rows = payload.get("resultList", {}).get("result", []) if isinstance(payload, dict) else []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or str(row.get("doi") or "").lower() != doi:
            continue
        language = str(row.get("language") or row.get("lang") or "").casefold()
        if language and language not in {"en", "eng", "english", "de", "deu", "ger", "german"}:
            continue
        pmid = str(row.get("id") or "")
        if not pmid.isdigit():
            continue
        title = compact(str(row.get("title") or ""), 170)
        if not title:
            continue
        return {
            "title": title,
            "url": "https://europepmc.org/article/MED/" + pmid,
            "published": str(row.get("firstPublicationDate") or ""),
            "summary": f"Linked study indexed in Europe PMC · DOI {doi}.",
            "tldr": "",
            "tldr_label": "",
        }
    return None


def fetch_json(url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "RhaccoNews/0.2.0", "Accept": "application/json", **(headers or {})}, data=data)
    with urllib.request.urlopen(request, timeout=7) as response:
        raw = response.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError("enrichment response too large")
    return json.loads(raw)


def cached_lookup(
    state: dict[str, Any], key: str, url: str, transform: Callable[[Any], dict[str, str] | None],
    now: datetime, no_network: bool, *, ttl_hours: int = 12,
) -> tuple[dict[str, str] | None, bool]:
    entries = state.setdefault("enrichment", {})
    previous = entries.get(key) if isinstance(entries.get(key), dict) else {}
    cached = previous.get("data") if isinstance(previous.get("data"), dict) else None
    checked = previous.get("checked_at")
    try:
        fresh = checked and now - datetime.fromisoformat(checked) < timedelta(hours=ttl_hours)
    except (ValueError, TypeError):
        fresh = False
    try:
        waiting = previous.get("retry_at") and now < datetime.fromisoformat(previous["retry_at"])
    except (ValueError, TypeError):
        waiting = False
    if no_network or fresh or waiting:
        return cached, False
    try:
        data = transform(fetch_json(url))
        entries[key] = {"checked_at": now.isoformat(), "data": data}
        if len(entries) > 90:
            for old in list(entries)[:-90]:
                del entries[old]
        return data, True
    except (OSError, ValueError, KeyError, TypeError):
        # Never let an enrichment error remove a known good story or its cache.
        entries[key] = {**previous, "data": cached, "retry_at": (now + timedelta(minutes=30)).isoformat()}
        return cached, True


def model_summary(title: str, evidence: str, state: dict[str, Any], budget: list[int], no_network: bool) -> tuple[str, bool]:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token or not re.fullmatch(r"[a-f0-9]{32}", account, re.I):
        return "", False
    digest = hashlib.sha256((MODEL + "\n" + title + "\n" + evidence).encode()).hexdigest()
    entries = state.setdefault("tldr_cache", {})
    cached = entries.get(digest)
    if isinstance(cached, str) and cached:
        return cached, False
    retry = state.setdefault("tldr_retry", {})
    try:
        waiting = datetime.now(timezone.utc) < datetime.fromisoformat(retry[digest]) if digest in retry else False
    except (ValueError, TypeError):
        waiting = False
    if no_network or budget[0] <= 0 or waiting:
        return "", False
    budget[0] -= 1
    prompt = (
        "Summarize ONLY this official UK government article in one or two short, plain-English "
        "sentences (max 65 words). State who did what and why it matters if stated. "
        "Keep uncertainty and attribution. Never invent facts, numbers, dates, approval, "
        "medical benefit or treatment advice. Output only the summary.\n"
        f"TITLE: {title[:220]}\nARTICLE: {evidence[:3000]}"
    )
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
    try:
        result = fetch_json(endpoint, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                            data=json.dumps({"prompt": prompt, "max_tokens": 130}).encode())
        answer = result.get("result") if isinstance(result, dict) else None
        value = compact(str(answer.get("response") or ""), 450) if isinstance(answer, dict) else ""
        supplied = title + " " + evidence
        numbers = re.findall(r"\b\d+(?:[.,/-]\d+)*%?", value)
        if (not isinstance(result, dict) or not result.get("success") or len(value) < 55 or len(value) > 420
                or any(n not in supplied for n in numbers) or "as an ai" in value.lower()):
            retry[digest] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            return "", True
        entries[digest] = value
        if len(entries) > 90:
            for old in list(entries)[:-90]:
                del entries[old]
        retry.pop(digest, None)
        return value, True
    except (OSError, ValueError, TypeError, KeyError):
        retry[digest] = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        if len(retry) > 90:
            for old in list(retry)[:-90]:
                del retry[old]
        return "", True
