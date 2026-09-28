#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo
import tldr

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parent
ENGINE_VERSION = "0.2.0"
CONFIG_PATH = WORKER_ROOT / "config" / "settings.json"
STATE_PATH = WORKER_ROOT / "data" / "cache.json"
INDEX_PATH = WORKER_ROOT / "index.html"
CACHE_SCHEMA_VERSION = 2
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
USER_AGENT = f"RhaccoNews/{ENGINE_VERSION} (+static GitHub Actions news aggregator)"
TOPICS = (
    ("bio", "Bio & Medicine"),
    ("cyber", "Cyber & Piracy"),
    ("dach", "Germany & Neighbors"),
    ("royal", "Royal Families"),
    ("world", "World Politics"),
)

STOPWORDS = {
    # German
    "aber","alle","als","auch","auf","aus","bei","bis","das","dass","dem","den","der","des","die","ein","eine","einer","eines","er","es","für","hat","im","in","ist","mit","nach","nicht","oder","sich","sie","über","und","vom","von","vor","wie","wir","wird","zu","zum","zur",
    # English
    "a","an","and","are","as","at","be","by","for","from","has","have","in","is","it","of","on","or","that","the","this","to","with",
    # French
    "au","aux","avec","ce","ces","dans","de","des","du","en","et","la","le","les","pour","sur","un","une",
}

@dataclass(frozen=True)
class Article:
    title: str
    url: str
    published: datetime
    source_id: str
    source_name: str
    publisher: str
    country: str
    language: str
    kind: str
    tab: str
    summary: str = ""
    via: str = ""
    tldr: str = ""
    tldr_label: str = ""


@dataclass(frozen=True)
class FetchResult:
    text: str | None
    status: int
    etag: str = ""
    last_modified: str = ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", raw):
        try:
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
    except Exception:
        return value.strip()
    if parts.scheme not in {"http", "https"}:
        return value.strip()
    keep = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        lk = k.lower()
        if lk.startswith("utm_") or lk in {"fbclid","gclid","mc_cid","mc_eid","output","ref","referrer"}:
            continue
        keep.append((k, v))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urlencode(keep), ""))


def host_label(value: str) -> str:
    try:
        host = urlsplit(value).netloc.lower().split(":", 1)[0]
    except Exception:
        return "unknown"
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown"


def fetch_text(
    url: str,
    timeout: int = 15,
    max_bytes: int = MAX_RESPONSE_BYTES,
    extra_headers: dict[str, str] | None = None,
    etag: str = "",
    last_modified: str = "",
) -> FetchResult:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*;q=0.5",
    }
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items() if v})
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError(f"response exceeds {max_bytes} bytes")
            charset = resp.headers.get_content_charset() or "utf-8"
            return FetchResult(
                text=raw.decode(charset, errors="replace"),
                status=int(getattr(resp, "status", 200) or 200),
                etag=str(resp.headers.get("ETag") or ""),
                last_modified=str(resp.headers.get("Last-Modified") or ""),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(text=None, status=304, etag=etag, last_modified=last_modified)
        raise


def source_request(src: dict[str, Any]) -> tuple[str, dict[str, str], str | None]:
    fmt = str(src.get("format") or "rss").lower()
    api_env = str(src.get("api_key_env") or "").strip()
    api_key = os.environ.get(api_env, "").strip() if api_env else ""
    if api_env and not api_key:
        return "", {}, api_env

    if fmt == "gdelt":
        params = {
            "query": src.get("query", ""),
            "mode": "artlist",
            "maxrecords": str(src.get("maxrecords", 100)),
            "format": "json",
            "timespan": src.get("timespan", "60min"),
            "sort": "datedesc",
        }
        return "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params), {}, None

    if fmt == "gnews":
        endpoint = str(src.get("endpoint") or "search").strip().lower()
        if endpoint not in {"search", "top-headlines"}:
            raise ValueError(f"unsupported GNews endpoint: {endpoint}")
        params: dict[str, str] = {"max": str(src.get("max_items", 10))}
        if src.get("query"):
            params["q"] = str(src["query"])
        if src.get("category"):
            params["category"] = str(src["category"])
        if src.get("language_filter"):
            params["lang"] = str(src["language_filter"])
        if src.get("country_filter"):
            params["country"] = str(src["country_filter"])
        # Header auth avoids putting secrets into request URLs/logs.
        return f"https://gnews.io/api/v4/{endpoint}?" + urllib.parse.urlencode(params), {"X-Api-Key": api_key}, None

    if fmt == "newsapi":
        endpoint = str(src.get("endpoint") or "everything").strip().lower()
        if endpoint not in {"everything", "top-headlines"}:
            raise ValueError(f"unsupported NewsAPI endpoint: {endpoint}")
        params = {}
        if src.get("query"):
            params["q"] = str(src["query"])
        if src.get("language_filter") and endpoint == "everything":
            params["language"] = str(src["language_filter"])
        if src.get("country_filter") and endpoint == "top-headlines":
            params["country"] = str(src["country_filter"])
        if src.get("category") and endpoint == "top-headlines":
            params["category"] = str(src["category"])
        if endpoint == "everything":
            params["sortBy"] = "publishedAt"
            params["pageSize"] = str(min(100, int(src.get("max_items", 50))))
        else:
            params["pageSize"] = str(min(100, int(src.get("max_items", 50))))
        return f"https://newsapi.org/v2/{endpoint}?" + urllib.parse.urlencode(params), {"X-Api-Key": api_key}, None

    return str(src.get("url", "")), {}, None


def node_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        tag = child.tag.split("}")[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def atom_link(node: ET.Element) -> str:
    for child in list(node):
        if child.tag.split("}")[-1].lower() != "link":
            continue
        href = child.attrib.get("href", "").strip()
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href
        if child.text and child.text.strip().startswith("http"):
            return child.text.strip()
    return ""


def parse_feed(raw: str, src: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    items: list[dict[str, Any]] = []
    candidates = [n for n in root.iter() if n.tag.split("}")[-1].lower() in {"item", "entry"}]
    for n in candidates[: int(src.get("max_items", 50))]:
        title = node_text(n, ("title",))
        link = node_text(n, ("link",)) or atom_link(n)
        if not link:
            guid = node_text(n, ("guid", "id"))
            if guid.startswith("http"):
                link = guid
        published = (
            node_text(n, ("pubdate", "published", "updated", "date"))
            or node_text(n, ("dc:date",))
        )
        summary = node_text(n, ("description", "summary", "content"))
        if not title or not link:
            continue
        absolute_link = urllib.parse.urljoin(str(src.get("url") or ""), link)
        items.append({
            "title": strip_tags(title),
            "url": canonical_url(absolute_link),
            "published": iso(parse_dt(published) or fetched_at),
            "summary": strip_tags(summary)[:500],
        })
    return items


def parse_gdelt(raw: str, src: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    data = json.loads(raw)
    out: list[dict[str, Any]] = []
    for item in (data.get("articles") or [])[: int(src.get("max_items", 100))]:
        title = strip_tags(str(item.get("title", "")))
        url = canonical_url(str(item.get("url", "")))
        if not title or not url:
            continue
        seen = parse_dt(str(item.get("seendate", ""))) or fetched_at
        out.append({
            "title": title,
            "url": url,
            "published": iso(seen),
            "summary": "",
            "source_name": host_label(url),
            "publisher": host_label(url),
            "country": str(item.get("sourcecountry") or "GLOBAL").upper(),
            "language": str(item.get("language") or src.get("language", "")).lower(),
            "via": "GDELT",
        })
    return out


def parse_gnews(raw: str, src: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("errors"):
        raise ValueError(f"GNews API error: {str(data.get('errors'))[:240]}")
    out: list[dict[str, Any]] = []
    for item in (data.get("articles") or [])[: int(src.get("max_items", 10))]:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        title = strip_tags(str(item.get("title") or ""))
        url = canonical_url(str(item.get("url") or ""))
        if not title or not url:
            continue
        published = parse_dt(str(item.get("publishedAt") or "")) or fetched_at
        source_name = strip_tags(str(source.get("name") or host_label(url)))
        out.append({
            "title": title,
            "url": url,
            "published": iso(published),
            "summary": strip_tags(str(item.get("description") or ""))[:500],
            "source_name": source_name,
            "publisher": source_name,
            "country": str(source.get("country") or src.get("country") or "GLOBAL").upper(),
            "language": str(item.get("lang") or src.get("language") or "").lower(),
            "via": "GNews",
        })
    return out


def parse_newsapi(raw: str, src: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if str(data.get("status") or "").lower() == "error":
        raise ValueError(f"NewsAPI error: {str(data.get('code') or '')} {str(data.get('message') or '')}".strip()[:240])
    out: list[dict[str, Any]] = []
    for item in (data.get("articles") or [])[: int(src.get("max_items", 50))]:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        title = strip_tags(str(item.get("title") or ""))
        url = canonical_url(str(item.get("url") or ""))
        if not title or not url:
            continue
        published = parse_dt(str(item.get("publishedAt") or "")) or fetched_at
        source_name = strip_tags(str(source.get("name") or host_label(url)))
        out.append({
            "title": title,
            "url": url,
            "published": iso(published),
            "summary": strip_tags(str(item.get("description") or ""))[:500],
            "source_name": source_name,
            "publisher": source_name,
            "country": str(src.get("country") or "GLOBAL").upper(),
            "language": str(src.get("language") or "").lower(),
            "via": "NewsAPI",
        })
    return out


def parse_kev(raw: str, src: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    return tldr.kev_items(raw, int(src.get("max_items", 60)))


def normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("version") not in {1, CACHE_SCHEMA_VERSION}:
        return {"version": CACHE_SCHEMA_VERSION, "sources": {}}
    raw["version"] = CACHE_SCHEMA_VERSION
    raw.setdefault("sources", {})
    return raw


def backoff_minutes(failures: int) -> int:
    return min(180, 5 * (2 ** max(0, min(failures - 1, 6))))


def refresh_source(state: dict[str, Any], src: dict[str, Any], now: datetime, no_network: bool) -> tuple[list[dict[str, Any]], str | None, bool]:
    sid = src["id"]
    entry = state["sources"].setdefault(sid, {})
    cached = entry.get("data") if isinstance(entry.get("data"), list) else []
    ttl = int(src.get("ttl_minutes", 30))
    fetched_at = parse_dt(entry.get("fetched_at"))
    fresh = fetched_at is not None and now - fetched_at < timedelta(minutes=ttl)
    retry_at = parse_dt(entry.get("retry_at"))

    try:
        url, auth_headers, missing_env = source_request(src)
    except Exception as exc:
        return cached, f"{type(exc).__name__}: {exc}"[:400], False

    if missing_env:
        reason = f"missing GitHub secret/environment variable: {missing_env}"
        changed = entry.get("disabled_reason") != reason
        entry["disabled_reason"] = reason
        entry["last_checked"] = iso(now)
        return cached, None, changed

    changed = False
    if entry.pop("disabled_reason", None) is not None:
        changed = True

    if no_network or fresh or (retry_at and now < retry_at):
        reason = "cache missing" if no_network and not cached else None
        return cached, reason, changed

    try:
        result = fetch_text(
            url,
            timeout=int(src.get("timeout_seconds", 15)),
            extra_headers=auth_headers,
            etag=str(entry.get("etag") or ""),
            last_modified=str(entry.get("last_modified") or ""),
        )
        entry["last_checked"] = iso(now)
        entry["last_http_status"] = result.status

        if result.status == 304:
            if not cached:
                raise ValueError("HTTP 304 received but cache is empty")
            entry.update({
                "fetched_at": iso(now),
                "last_success": iso(now),
                "failures": 0,
                "retry_at": "",
                "last_error": "",
            })
            return cached, None, True

        raw = result.text or ""
        fmt = str(src.get("format") or "rss").lower()
        parsers = {
            "gdelt": parse_gdelt,
            "gnews": parse_gnews,
            "newsapi": parse_newsapi,
            "kev": parse_kev,
        }
        parser = parsers.get(fmt, parse_feed)
        data = parser(raw, src, now)
        if not data and cached:
            raise ValueError("empty response; keeping cached items")
        if not data and str(src.get("kind") or "") != "discovery":
            raise ValueError("parsed zero items")
        entry.update({
            "data": data,
            "fetched_at": iso(now),
            "last_success": iso(now),
            "failures": 0,
            "retry_at": "",
            "last_error": "",
            "etag": result.etag or str(entry.get("etag") or ""),
            "last_modified": result.last_modified or str(entry.get("last_modified") or ""),
        })
        return data, None, True
    except Exception as exc:
        failures = int(entry.get("failures", 0)) + 1
        entry["failures"] = failures
        entry["last_checked"] = iso(now)
        entry["retry_at"] = iso(now + timedelta(minutes=backoff_minutes(failures)))
        entry["last_error"] = f"{type(exc).__name__}: {exc}"[:400]
        return cached, entry["last_error"], True


def to_articles(src: dict[str, Any], data: list[dict[str, Any]], now: datetime, max_age_hours: int) -> list[Article]:
    out: list[Article] = []
    language_codes = {
        "de": "de", "deu": "de", "ger": "de", "german": "de",
        "en": "en", "eng": "en", "english": "en",
    }
    for item in data:
        published = parse_dt(item.get("published")) or now
        if now - published > timedelta(hours=max_age_hours):
            continue
        raw_language = str(item.get("language") or src.get("language") or "").strip().casefold()
        language = language_codes.get(raw_language.split("-", 1)[0])
        if language not in {"de", "en"}:
            continue
        source_name = str(item.get("source_name") or src.get("name") or src["id"])
        publisher = str(item.get("publisher") or src.get("publisher") or source_name)
        out.append(Article(
            title=str(item.get("title") or "").strip(),
            url=canonical_url(str(item.get("url") or "")),
            published=published,
            source_id=src["id"],
            source_name=source_name,
            publisher=publisher,
            country=str(item.get("country") or src.get("country") or "").upper(),
            language=language,
            kind=str(src.get("kind") or "editorial"),
            tab=str(src.get("tab") or "world"),
            summary=str(item.get("summary") or "")[:500],
            via=str(item.get("via") or ""),
            tldr=str(item.get("tldr") or "")[:450],
            tldr_label=str(item.get("tldr_label") or "")[:80],
        ))
    return [a for a in out if a.title and a.url]


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[\wÄÖÜäöüßÀ-ÿ]{3,}", title.casefold(), flags=re.UNICODE)
    return {w for w in words if w not in STOPWORDS and not w.isdigit()}


def similarity(a: Article, b: Article) -> float:
    if canonical_url(a.url) == canonical_url(b.url):
        return 1.0
    ids_a = set(tldr.CVE.findall(a.title + " " + a.summary))
    ids_b = set(tldr.CVE.findall(b.title + " " + b.summary))
    if len(ids_a) == len(ids_b) == 1:
        return 0.9 if {x.upper() for x in ids_a} == {x.upper() for x in ids_b} else 0.0
    ta, tb = title_tokens(a.title), title_tokens(b.title)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jac = inter / union if union else 0.0
    if inter >= 4 and jac >= 0.30:
        return max(jac, 0.68)
    if inter >= 3 and jac >= 0.38:
        return max(jac, 0.58)
    return jac


def cluster_articles(articles: list[Article], window_hours: int) -> list[list[Article]]:
    clusters: list[list[Article]] = []
    for article in sorted(articles, key=lambda a: a.published, reverse=True):
        best_i = None
        best_score = 0.0
        for i, cluster in enumerate(clusters):
            if abs((article.published - cluster[0].published).total_seconds()) > window_hours * 3600:
                continue
            score = max(similarity(article, other) for other in cluster[:6])
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= 0.56:
            if canonical_url(article.url) not in {canonical_url(x.url) for x in clusters[best_i]}:
                clusters[best_i].append(article)
                clusters[best_i].sort(key=lambda a: a.published, reverse=True)
        else:
            clusters.append([article])
    return clusters


def cluster_score(cluster: list[Article], now: datetime) -> float:
    non_discovery = [a for a in cluster if a.kind != "discovery"]
    publishers = {a.publisher.casefold() for a in non_discovery}
    countries = {a.country for a in non_discovery if a.country}
    primary = any(a.kind == "primary" for a in cluster)
    newest = max(a.published for a in cluster)
    age_h = max(0.0, (now - newest).total_seconds() / 3600)
    return len(publishers) * 22 + len(non_discovery) * 5 + len(countries) * 3 + (8 if primary else 0) + max(0, 12 - age_h)


def representative(cluster: list[Article]) -> Article:
    editorial = [a for a in cluster if a.kind == "editorial"]
    if editorial:
        return max(editorial, key=lambda a: a.published)
    non_discovery = [a for a in cluster if a.kind != "discovery"]
    if non_discovery:
        return max(non_discovery, key=lambda a: a.published)
    return max(cluster, key=lambda a: a.published)


def age_label(dt: datetime, now: datetime) -> str:
    mins = max(0, int((now - dt).total_seconds() // 60))
    if mins < 2:
        return "just now"
    if mins < 60:
        return f"{mins} minutes ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} {'hour' if hours == 1 else 'hours'} ago"
    days = hours // 24
    return f"{days} {'day' if days == 1 else 'days'} ago"


def source_chip(a: Article, now: datetime) -> str:
    kind = "Official" if a.kind == "primary" else ("Found" if a.kind == "discovery" else "News")
    via = f" · via {html.escape(a.via)}" if a.via else ""
    title = f"{a.source_name} · {kind} · {age_label(a.published, now)}{via}"
    attributes = {
        "source-id": a.source_id,
        "publisher": a.publisher,
        "kind": a.kind,
        "title": a.title,
        "language": a.language,
        "age": age_label(a.published, now),
        "published": iso(a.published),
        "brief": a.summary[:450],
        "tldr": a.tldr[:450],
        "tldr-label": a.tldr_label[:80],
    }
    data = " ".join(f'data-{key}="{html.escape(value, quote=True)}"' for key, value in attributes.items())
    return (
        f'<a class="source-chip kind-{html.escape(a.kind)}" {data} href="{html.escape(a.url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" title="{html.escape(title, quote=True)}">'
        f'<span>{html.escape(a.source_name)}</span><small>{html.escape(a.country or "—")}</small></a>'
    )


def card_html(cluster: list[Article], now: datetime, hidden: bool = False) -> str:
    rep = representative(cluster)
    non_discovery = [a for a in cluster if a.kind != "discovery"]
    publisher_count = len({a.publisher.casefold() for a in non_discovery})
    countries = len({a.country for a in non_discovery if a.country})
    primary = any(a.kind == "primary" for a in cluster)
    chips = "".join(
        source_chip(a, now)
        for a in sorted(cluster, key=lambda x: (x.source_name.casefold(), -x.published.timestamp()))
    )
    source_word = "source" if len(non_discovery) == 1 else "sources"
    publisher_word = "publisher" if publisher_count == 1 else "publishers"
    meta = f"{len(non_discovery)} {source_word} · {publisher_count} {publisher_word}"
    if countries > 1:
        meta += f" · {countries} countries"
    if primary:
        meta += " · official source"
    shown = next((a for a in cluster if a.tldr), None)
    if shown:
        label, brief = shown.tldr_label, shown.tldr
    else:
        shown = next((a for a in cluster if a.summary and a.kind != "discovery"), None)
        label = "Publisher excerpt · " + shown.source_name if shown else ""
        brief = tldr.compact(shown.summary, 380) if shown else ""
    brief_html = (f'<p class="story-tldr"{"" if brief else " hidden"}><strong class="story-tldr-label">{html.escape(label)}</strong>'
                  f'<span class="story-tldr-text">{html.escape(brief)}</span></p>'
                  f'<p class="story-no-tldr"{" hidden" if brief else ""}>No verified brief is available yet. Read the sources below.</p>')
    return f"""
    <article class="story-card"{" hidden" if hidden else ""}>
      <details class="story-expand"><summary title="Open story"><span class="story-brief-title">{html.escape(rep.title)}</span><small class="story-brief-meta"> · {html.escape(age_label(rep.published, now))} · {html.escape(str(len(non_discovery)))} sources</small></summary>
        <div class="story-body">
          <div class="story-topline"><span class="story-age">{html.escape(age_label(rep.published, now))}</span><span class="source-count">{html.escape(meta)}</span></div>
          <h2><a lang="{html.escape(rep.language, quote=True)}" href="{html.escape(rep.url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(rep.title)}</a></h2>
          {brief_html}
          <div class="source-row" aria-label="Sources">{chips}</div>
        </div>
      </details>
    </article>
    """


def status_summary(cfg: dict[str, Any], state: dict[str, Any], now: datetime) -> tuple[int, int, int, int]:
    enabled = [s for s in cfg.get("sources", []) if s.get("enabled", True) and not s.get("enrichment_only")]
    ok = stale = failed = waiting = 0
    for s in enabled:
        e = state.get("sources", {}).get(s["id"], {})
        if e.get("disabled_reason"):
            waiting += 1
            continue
        last = parse_dt(e.get("last_success"))
        if not last:
            failed += 1
            continue
        age = (now - last).total_seconds() / 60
        if age <= int(s.get("stale_after_minutes", cfg.get("stale_after_minutes", 360))):
            ok += 1
        else:
            stale += 1
    return ok, stale, failed, waiting


def content_filter_html(cfg: dict[str, Any]) -> str:
    groups = []
    for tab, label in TOPICS:
        rows = []
        for source in sorted(cfg.get("sources", []), key=lambda item: str(item.get("name", "")).casefold()):
            if not source.get("enabled", True) or source.get("tab") != tab:
                continue
            sid = html.escape(str(source["id"]), quote=True)
            name = html.escape(str(source["name"]))
            country = html.escape(str(source.get("country", "")))
            optional = " · API" if source.get("optional") else ""
            rows.append(
                f'<label class="content-option"><input type="checkbox" data-source-id="{sid}" checked>'
                f'<span>{name} <small>· {country}{optional}</small></span></label>'
            )
        groups.append(f'<section><h3>{html.escape(label)}</h3>{"".join(rows)}</section>')
    topics = "".join(
        f'<label class="content-option"><input type="checkbox" data-topic-id="{html.escape(tab, quote=True)}">'
        f'<span>{html.escape(label)}</span></label>' for tab, label in TOPICS
    )
    return (
        '<div class="header-controls">'
        '<div class="content-controls"><span>Content:</span>'
        '<details id="content-filter" class="content-filter">'
        '<summary id="content-filter-summary" title="Choose topics">None</summary>'
        '<div id="content-filter-menu" class="content-menu topic-menu">'
        '<div class="content-menu-head"><strong>Show topics</strong>'
        '<button type="button" id="hide-all-topics">Clear all</button></div>'
        f'{topics}</div></details></div>'
        '<div class="content-controls"><span>Sources:</span>'
        '<details id="sources-filter" class="content-filter">'
        '<summary id="sources-filter-summary" title="Choose sources">All</summary>'
        '<div id="sources-filter-menu" class="content-menu">'
        '<div class="content-menu-head"><strong>Show sources</strong>'
        '<button type="button" id="show-all-sources">Select all</button></div>'
        '<p class="content-help">Choose which news and source links to show here. '
        'The country code shows where a source is based.</p>'
        f'<div class="content-grid">{"".join(groups)}</div></div></details></div></div>'
    )


def render_html(clusters_by_tab: dict[str, list[list[Article]]], cfg: dict[str, Any], state: dict[str, Any], now: datetime, errors: dict[str, str]) -> str:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Berlin"))
    local = now.astimezone(tz)
    max_visible = max(1, int(cfg.get("max_clusters_per_tab", 24)))
    sections = {}
    for tab, _ in TOPICS:
        cards = "\n".join(card_html(c, now, i >= max_visible) for i, c in enumerate(clusters_by_tab.get(tab, [])))
        sections[tab] = cards or '<div class="empty">No recent news yet.</div>'
    tabs_markup = "\n".join(f'<button class="tab" data-tab="{tab}" type="button" hidden>{html.escape(label)}</button>' for tab, label in TOPICS)
    panes_markup = "\n".join(
        f'<section id="pane-{tab}" class="pane">{sections[tab]}'
        '<div class="empty filtered-empty" hidden>No news from your chosen sources.</div></section>'
        for tab, _ in TOPICS
    )
    ok, stale, failed, waiting = status_summary(cfg, state, now)
    status_text = f"Sources: {ok} current · {stale} old · {failed} empty or failed · {waiting} need an API key"
    error_note = ""
    if errors:
        error_note = f'<details class="errors"><summary>{len(errors)} sources had an error</summary><pre>{html.escape(json.dumps(errors, ensure_ascii=False, indent=2))}</pre></details>'
    gov_used = any(a.tldr_label.endswith("GOV.UK") for clusters in clusters_by_tab.values()
                   for cluster in clusters for a in cluster)
    license_note = ('<div>Contains public sector information licensed under the '
                    '<a href="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" '
                    'target="_blank" rel="noopener noreferrer">Open Government Licence v3.0</a>.</div>') if gov_used else ''
    filter_markup = content_filter_html(cfg)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<meta name="page-generated-at-ms" content="{int(now.timestamp() * 1000)}">
<title>Rhacco News</title>
<style>
:root{{--bg:#0b0c0f;--panel:#14161b;--panel2:#1a1d24;--text:#f3f4f6;--muted:#9ca3af;--line:#2a2f39;--accent:#7c5cff;--accent2:#a78bfa;--good:#69d18f;--warn:#f4c95d;--shadow:0 14px 45px rgba(0,0,0,.24)}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% -10%,#1a1730 0,#0b0c0f 34%,#090a0d 100%);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}}a{{color:inherit}}.shell{{width:min(930px,calc(100% - 28px));margin:0 auto;padding:34px 0 54px}}header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:20px}}h1{{font-size:31px;letter-spacing:-.04em;margin:0}}.updated{{color:var(--muted);font-size:13px;text-align:right}}.tabs{{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:5px;padding:5px;background:rgba(20,22,27,.94);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;box-shadow:var(--shadow)}}.tab{{appearance:none;flex:1 1 135px;border:0;border-radius:10px;padding:11px 12px;background:transparent;color:var(--muted);font-weight:750;cursor:pointer}}.tab.active{{background:#28223f;color:#fff;box-shadow:inset 0 0 0 1px #51417f}}.pane{{display:none}}.pane.active{{display:block}}.story-card{{background:linear-gradient(180deg,var(--panel),#111318);border:1px solid var(--line);border-radius:16px;padding:17px 18px 15px;margin:0 0 12px;box-shadow:var(--shadow)}}.story-topline{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--muted);font-size:12px;margin-bottom:8px}}.source-count{{color:#c8cbd2}}h2{{font-size:20px;line-height:1.28;letter-spacing:-.015em;margin:0 0 7px}}h2 a{{text-decoration:none}}h2 a:hover{{text-decoration:underline;text-decoration-color:#7665bd;text-underline-offset:3px}}.source-row{{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}}.source-chip{{display:inline-flex;gap:7px;align-items:center;text-decoration:none;border:1px solid var(--line);background:var(--panel2);padding:6px 8px;border-radius:9px;font-size:12px;color:#e3e4e8}}.source-chip:hover{{border-color:#625492}}.source-chip small{{color:var(--muted);font-size:10px}}.source-chip.kind-primary{{border-color:#315c42;background:#122319}}.source-chip.kind-discovery{{border-style:dashed;color:#b8bbc3}}.empty{{border:1px dashed var(--line);border-radius:14px;padding:24px;text-align:center;color:var(--muted)}}footer{{margin-top:22px;color:var(--muted);font-size:12px;text-align:center}}.errors{{margin-top:13px;text-align:left;border:1px solid var(--line);border-radius:10px;padding:8px 10px}}pre{{white-space:pre-wrap;word-break:break-word;font-size:11px}}@media(max-width:620px){{.shell{{width:min(100% - 18px,930px);padding-top:20px}}header{{align-items:flex-start;flex-direction:column;gap:6px}}.updated{{text-align:left}}h1{{font-size:27px}}h2{{font-size:18px}}.story-card{{padding:15px}}}}
.story-card[hidden],.source-chip[hidden],.filtered-empty[hidden],.delete-cookie[hidden],.tabs[hidden],.tab[hidden],.topics-empty[hidden]{{display:none}}
.header-meta{{display:flex;flex-direction:column;align-items:flex-end;gap:7px}}
.header-controls{{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:wrap}}
.content-controls{{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;font-weight:700}}
.content-filter>summary{{list-style:none;cursor:pointer;user-select:none;border:1px solid #51417f;border-radius:999px;background:#28223f;color:#fff;padding:4px 10px;min-width:55px;text-align:center}}
.content-filter>summary::-webkit-details-marker{{display:none}}
.content-filter>summary::after{{content:" ▾";color:#cbbdff}}
.content-filter[open]>summary::after{{content:" ▴"}}
.content-menu{{position:fixed;z-index:20;visibility:hidden;pointer-events:none;width:min(660px,calc(100vw - 16px));max-height:min(460px,calc(100vh - 16px));overflow:auto;background:#16161e;border:1px solid #51417f;border-radius:12px;padding:12px;box-shadow:0 16px 40px rgba(0,0,0,.5);text-align:left;overscroll-behavior:contain}}
.content-menu.positioned{{visibility:visible;pointer-events:auto}}
.topic-menu{{width:min(340px,calc(100vw - 16px))}}
.content-menu-head{{position:sticky;top:-12px;z-index:1;background:#16161e;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 2px 10px;border-bottom:1px solid var(--line)}}
.content-menu-head strong{{font-size:12px;color:var(--text)}}
.content-menu-head button{{border:0;background:none;color:#bbabff;font:inherit;font-size:11px;cursor:pointer}}
.content-help{{font-size:11px;color:var(--muted);margin:8px 2px}}
.content-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.content-grid h3{{font-size:12px;color:#d8d0ff;margin:4px 4px 8px}}
.content-option{{display:flex;align-items:flex-start;gap:7px;padding:5px 4px;border-radius:6px;font-size:11px;color:#e1dfeb;cursor:pointer}}
.content-option:hover{{background:#252434}}
.content-option input{{margin:2px 0 0;accent-color:var(--accent);flex:0 0 auto}}
.content-option span{{min-width:0;overflow-wrap:anywhere}}
.content-option small{{font-size:10px;color:var(--muted)}}
.preference-footer{{display:flex;justify-content:center;align-items:center;gap:5px;flex-wrap:wrap;margin-top:12px;font-size:9px;color:#89909c}}
.story-expand>summary{{display:flex;align-items:baseline;gap:5px;white-space:nowrap;overflow:hidden;cursor:pointer;list-style:none;min-height:24px}}
.story-expand>summary::-webkit-details-marker{{display:none}}
.story-expand>summary::before{{content:'▸';color:#ad9aff;flex:none}}
.story-expand[open]>summary::before{{content:'▾'}}
.story-brief-title{{font-weight:690;min-width:0;overflow:hidden;text-overflow:ellipsis}}
.story-brief-meta{{flex:none;max-width:45%;overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:11px}}
.story-body{{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}}
.story-tldr{{margin:12px 0 0;line-height:1.55;color:#e8e9ee}}
.story-tldr-label{{display:block;color:#bbabff;font-size:12px;margin-bottom:3px}}
.story-no-tldr{{color:var(--muted);font-size:13px;margin:12px 0 0}}
.story-tldr[hidden],.story-no-tldr[hidden]{{display:none}}
.story-expand>summary:focus-visible{{outline:2px solid #a78bfa;outline-offset:3px;border-radius:4px}}
.preference-footer .saved{{color:#83a58b}}
.preference-footer .failed{{color:#d8a689}}
.delete-cookie{{border:0;background:none;color:#aeb5c2;text-decoration:underline;cursor:pointer;font:inherit;padding:0}}
@media(max-width:620px){{.header-meta{{align-items:flex-start}}.header-controls{{justify-content:flex-start}}.content-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="shell">
<header><div><h1>Rhacco News</h1><div class="updated">Live news · checked every {int(cfg.get("run_interval_minutes", 5))} min</div></div><div class="header-meta"><div class="updated">Updated: {html.escape(local.strftime('%d %b %Y · %H:%M'))}</div>{filter_markup}</div></header>
<nav class="tabs" aria-label="News topics" hidden>{tabs_markup}</nav>
<main>
<div id="topics-empty" class="empty topics-empty">Choose a topic under Content above.</div>
{panes_markup}
</main>
<footer><div>{html.escape(status_text)}</div>{error_note}{license_note}<div class="preference-footer"><span id="preference-note" role="status">Your choices are saved in one cookie.</span><button type="button" id="delete-preferences" class="delete-cookie" hidden>Delete cookie</button></div></footer>
</div>
<script>
(()=>{{
 const buttons=[...document.querySelectorAll('.tab')];
 const panes=[...document.querySelectorAll('.pane')];
 const tabs=document.querySelector('.tabs');
 const topicsEmpty=document.getElementById('topics-empty');
 const topicFilter=document.getElementById('content-filter');
 const topicMenu=document.getElementById('content-filter-menu');
 const sourceFilter=document.getElementById('sources-filter');
 const sourceMenu=document.getElementById('sources-filter-menu');
 const topicInputs=[...topicMenu.querySelectorAll('input[data-topic-id]')];
 const inputs=[...sourceMenu.querySelectorAll('input[data-source-id]')];
 const knownTopics=new Set(topicInputs.map(input=>input.dataset.topicId));
 const knownIds=new Set(inputs.map(input=>input.dataset.sourceId));
 const maxVisible={max_visible};
 const cookieKey='rhacco_news_prefs';
 const legacyTabKey='rhacco-news-tab-v1';
 const note=document.getElementById('preference-note');
 const deleteButton=document.getElementById('delete-preferences');
 function cookiePath(){{
   const path=window.location.pathname || '/';
   return path.endsWith('/') ? path : path.slice(0,path.lastIndexOf('/')+1) || '/';
 }}
 function rawCookie(){{
   try{{
     const entry=document.cookie.split(';').map(part=>part.trim()).find(part=>part.startsWith(cookieKey+'='));
     return entry ? entry.slice(cookieKey.length+1) : '';
   }}catch(e){{return '';}}
 }}
 function readPreferences(){{
   try{{
     const raw=rawCookie();
     if(!raw) return null;
     const data=JSON.parse(decodeURIComponent(raw));
     return data && (data.v===1||data.v===2) && knownTopics.has(data.tab) && Array.isArray(data.disabled) ? data : null;
   }}catch(e){{return null;}}
 }}
 function clearLegacyTab(){{try{{localStorage.removeItem(legacyTabKey);}}catch(e){{}}}}
 const stored=readPreferences();
 let activeTab=stored?.tab || 'bio';
 let selectedTopics=new Set((stored?.v===2 && Array.isArray(stored.topics) ? stored.topics : []).filter(id=>knownTopics.has(id)));
 let disabled=new Set((stored?.disabled || []).filter(id=>knownIds.has(id)));
 let saveState=stored ? 'saved' : rawCookie() ? 'failed' : 'idle';
 if(stored) clearLegacyTab();
 else if(!rawCookie()){{
   try{{const old=localStorage.getItem(legacyTabKey);if(old==='dach')activeTab=old;}}catch(e){{}}
   clearLegacyTab();
 }}
 function updateNote(){{
   note.classList.toggle('saved',saveState==='saved'||saveState==='deleted');
   note.classList.toggle('failed',saveState==='failed');
   note.textContent=saveState==='saved' ? 'Your choices are saved in one cookie.'
     : saveState==='deleted' ? 'Your saved choices were removed.'
     : saveState==='failed' ? 'The cookie could not be saved or read.'
     : 'Your choices are saved in one cookie.';
   deleteButton.hidden=!rawCookie();
 }}
 function savePreferences(){{
   const encoded=encodeURIComponent(JSON.stringify({{v:2,tab:activeTab,topics:[...selectedTopics].sort(),disabled:[...disabled].sort()}}));
   const secure=window.location.protocol==='https:' ? '; Secure' : '';
   try{{document.cookie=`${{cookieKey}}=${{encoded}}; Max-Age=31536000; Path=${{cookiePath()}}; SameSite=Lax${{secure}}`;}}catch(e){{}}
   saveState=rawCookie()===encoded ? 'saved' : 'failed';
   if(saveState==='saved')clearLegacyTab();
   updateNote();
 }}
 function applyTopics(){{
   topicInputs.forEach(input=>input.checked=selectedTopics.has(input.dataset.topicId));
   const count=selectedTopics.size;
   document.getElementById('content-filter-summary').textContent=count ? `${{count}}/${{knownTopics.size}}` : 'None';
   const visible=buttons.filter(button=>selectedTopics.has(button.dataset.tab));
   if(visible.length && !selectedTopics.has(activeTab))activeTab=visible[0].dataset.tab;
   tabs.hidden=visible.length===0;
   topicsEmpty.hidden=visible.length>0;
   buttons.forEach(button=>{{
     button.hidden=!selectedTopics.has(button.dataset.tab);
     const active=visible.length>0 && button.dataset.tab===activeTab;
     button.classList.toggle('active',active);
     button.setAttribute('aria-pressed',String(active));
   }});
   panes.forEach(pane=>pane.classList.toggle('active',visible.length>0 && pane.id==='pane-'+activeTab));
 }}
 function applySelection(){{
   inputs.forEach(input=>input.checked=!disabled.has(input.dataset.sourceId));
   const summary=document.getElementById('sources-filter-summary');
   summary.textContent=disabled.size ? `${{knownIds.size-disabled.size}}/${{knownIds.size}}` : 'All';
   for(const pane of panes){{
     let shown=0;
     for(const card of pane.querySelectorAll('.story-card')){{
       const chips=[...card.querySelectorAll('.source-chip')];
       const selected=chips.filter(chip=>!knownIds.has(chip.dataset.sourceId)||!disabled.has(chip.dataset.sourceId));
       card.hidden=selected.length===0 || shown>=maxVisible;
       if(card.hidden)continue;
       shown++;
       let rep=selected[0];
       const priority={{editorial:2,primary:1,discovery:0}};
       for(const chip of selected.slice(1)){{
         if((priority[chip.dataset.kind]??0)>(priority[rep.dataset.kind]??0) ||
           (chip.dataset.kind===rep.dataset.kind && chip.dataset.published>rep.dataset.published))rep=chip;
       }}
       const headline=card.querySelector('h2 a');
       headline.textContent=rep.dataset.title;
       headline.href=rep.href;
       headline.lang=rep.dataset.language||'en';
       const briefTitle=card.querySelector('.story-brief-title');
       briefTitle.textContent=rep.dataset.title;
       briefTitle.lang=rep.dataset.language||'en';
       card.querySelector('.story-age').textContent=rep.dataset.age;
       const editorial=selected.filter(chip=>chip.dataset.kind!=='discovery');
       const publishers=new Set(editorial.map(chip=>chip.dataset.publisher.toLocaleLowerCase()));
       const countries=new Set(editorial.map(chip=>chip.querySelector('small').textContent).filter(x=>x&&x!=='—'));
       let label=`${{editorial.length}} ${{editorial.length===1?'source':'sources'}} · ${{publishers.size}} ${{publishers.size===1?'publisher':'publishers'}}`;
       if(countries.size>1)label+=` · ${{countries.size}} countries`;
       if(selected.some(chip=>chip.dataset.kind==='primary'))label+=' · official source';
       card.querySelector('.source-count').textContent=label;
       card.querySelector('.story-brief-meta').textContent=` · ${{rep.dataset.age}} · ${{editorial.length}} ${{editorial.length===1?'source':'sources'}}`;
       const source=selected.find(chip=>chip.dataset.tldr) || selected.find(chip=>chip.dataset.kind!=='discovery'&&chip.dataset.brief);
       const paragraph=card.querySelector('.story-tldr');
       const emptyBrief=card.querySelector('.story-no-tldr');
       if(source){{
         if(paragraph){{
           paragraph.hidden=false;
           paragraph.querySelector('.story-tldr-label').textContent=source.dataset.tldr ? source.dataset.tldrLabel : `Publisher excerpt · ${{source.querySelector('span').textContent}}`;
           paragraph.querySelector('.story-tldr-text').textContent=source.dataset.tldr || source.dataset.brief;
         }}
         if(emptyBrief)emptyBrief.hidden=true;
       }}else{{
         if(paragraph)paragraph.hidden=true;
         if(emptyBrief)emptyBrief.hidden=false;
       }}
       for(const chip of chips){{
         chip.hidden=!selected.includes(chip);
       }}
     }}
     pane.querySelector('.filtered-empty').hidden=shown>0 || !pane.querySelector('.story-card');
   }}
 }}
 function positionMenu(filter){{
   if(!filter.open)return;
   const menu=filter.querySelector('.content-menu');
   const anchor=filter.querySelector('summary').getBoundingClientRect();
   const margin=8, gap=6, width=Math.min(filter===topicFilter?340:660,window.innerWidth-margin*2);
   menu.style.width=`${{width}}px`;
   menu.style.left=`${{Math.max(margin,Math.round((window.innerWidth-width)/2))}}px`;
   const below=window.innerHeight-anchor.bottom-gap-margin;
   const above=anchor.top-gap-margin;
   const useBelow=below>=Math.min(menu.scrollHeight,220) || below>=above;
   const available=Math.max(0,Math.min(460,window.innerHeight-margin*2,useBelow?below:above));
   menu.style.maxHeight=`${{available}}px`;
   menu.style.top=`${{Math.max(margin,useBelow?anchor.bottom+gap:anchor.top-gap-available)}}px`;
   menu.classList.add('positioned');
 }}
 function closeMenus(){{
   for(const filter of [topicFilter,sourceFilter]){{filter.open=false;filter.querySelector('.content-menu').classList.remove('positioned');}}
 }}
 topicMenu.addEventListener('change',event=>{{
   const input=event.target.closest('input[data-topic-id]');
   if(!input || !knownTopics.has(input.dataset.topicId))return;
   if(input.checked)selectedTopics.add(input.dataset.topicId);
   else selectedTopics.delete(input.dataset.topicId);
   applyTopics();savePreferences();
 }});
 document.getElementById('hide-all-topics').addEventListener('click',()=>{{
   selectedTopics.clear();applyTopics();savePreferences();
 }});
 sourceMenu.addEventListener('change',event=>{{
   const input=event.target.closest('input[data-source-id]');
   if(!input || !knownIds.has(input.dataset.sourceId))return;
   if(input.checked)disabled.delete(input.dataset.sourceId);
   else disabled.add(input.dataset.sourceId);
   applySelection();savePreferences();
 }});
 document.getElementById('show-all-sources').addEventListener('click',()=>{{
   disabled.clear();applySelection();savePreferences();
 }});
 buttons.forEach(button=>button.addEventListener('click',()=>{{
   if(!selectedTopics.has(button.dataset.tab))return;
   activeTab=button.dataset.tab;applyTopics();savePreferences();
 }}));
 deleteButton.addEventListener('click',()=>{{
   const paths=new Set(['/']);let parent='';
   for(const part of cookiePath().split('/').filter(Boolean)){{parent+='/'+part;paths.add(parent);paths.add(parent+'/');}}
   try{{
     for(const path of paths)for(const domain of ['',`; Domain=${{window.location.hostname}}`])
       document.cookie=`${{cookieKey}}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=${{path}}${{domain}}; SameSite=Lax`;
   }}catch(e){{}}
   clearLegacyTab();
   selectedTopics.clear();disabled.clear();activeTab='bio';applyTopics();applySelection();
   saveState=rawCookie() ? 'failed' : 'deleted';
   updateNote();
 }});
 for(const filter of [topicFilter,sourceFilter])filter.addEventListener('toggle',()=>{{
   const other=filter===topicFilter?sourceFilter:topicFilter;
   if(filter.open){{other.open=false;other.querySelector('.content-menu').classList.remove('positioned');positionMenu(filter);}}
   else filter.querySelector('.content-menu').classList.remove('positioned');
 }});
 document.addEventListener('pointerdown',event=>{{
   if((topicFilter.open&&!topicFilter.contains(event.target))||(sourceFilter.open&&!sourceFilter.contains(event.target)))closeMenus();
 }});
 document.addEventListener('keydown',event=>{{
   const open=[topicFilter,sourceFilter].find(filter=>filter.open);
   if(event.key==='Escape'&&open){{closeMenus();open.querySelector('summary').focus();}}
 }});
 window.addEventListener('resize',()=>{{for(const filter of [topicFilter,sourceFilter])if(filter.open)positionMenu(filter);}});
 window.addEventListener('scroll',()=>{{for(const filter of [topicFilter,sourceFilter])if(filter.open)positionMenu(filter);}},true);
 applyTopics();applySelection();updateNote();
}})();
</script>
</body></html>"""


def dedupe_articles(articles: list[Article]) -> list[Article]:
    """Deduplicate exact canonical URLs while preferring direct publishers."""
    by_url: dict[str, Article] = {}
    kind_priority = {"discovery": 0, "primary": 1, "editorial": 2}
    for art in articles:
        key = canonical_url(art.url)
        old = by_url.get(key)
        if old is None:
            by_url[key] = art
            continue
        old_p = kind_priority.get(old.kind, 1)
        new_p = kind_priority.get(art.kind, 1)
        if new_p > old_p or (new_p == old_p and art.published > old.published):
            by_url[key] = art
    return list(by_url.values())


def select_display_candidates(clusters: list[list[Article]], limit: int, source_ids: list[str]) -> list[list[Article]]:
    """Keep the usual top stories and some candidates for each selectable source."""
    included = set(range(min(limit, len(clusters))))
    per_source = min(limit, 12)
    for source_id in source_ids:
        matches = (i for i, cluster in enumerate(clusters) if any(a.source_id == source_id for a in cluster))
        for i, match in enumerate(matches):
            if i >= per_source:
                break
            included.add(match)
    return [cluster for i, cluster in enumerate(clusters) if i in included]


def enrich_clusters(clusters_by_tab: dict[str, list[list[Article]]], state: dict[str, Any],
                    now: datetime, no_network: bool, cfg: dict[str, Any]) -> bool:
    """Only match existing stories; extra APIs cannot manufacture an event."""
    changed = False
    capacity = max(0, int(cfg.get("tldr", {}).get("max_enrich_requests", 6)))
    requests_left = {"gov": capacity // 2, "ghsa": capacity // 3,
                     "epmc": capacity - capacity // 2 - capacity // 3}
    model_budget = [int(cfg.get("tldr", {}).get("max_ai_requests", 2))]
    for tab, clusters in clusters_by_tab.items():
        for cluster in clusters[:int(cfg.get("tldr", {}).get("max_clusters_per_tab", 5))]:
            for i, article in enumerate(cluster):
                gov_url = tldr.gov_content_url(article.url)
                if not gov_url or requests_left["gov"] <= 0:
                    continue
                requests_left["gov"] -= 1
                def read_gov(payload: Any) -> dict[str, str] | None:
                    body, brief = tldr.gov_brief(payload)
                    return {"body": body, "brief": brief} if body and brief else None
                data, fetched = tldr.cached_lookup(state, "gov:" + article.url, gov_url,
                                                     read_gov, now, no_network, ttl_hours=6)
                changed = changed or fetched
                if data and data.get("brief"):
                    generated, created = tldr.model_summary(article.title, data["body"], state, model_budget, no_network)
                    changed = changed or created
                    cluster[i] = replace(article, tldr=generated or data["brief"],
                                         tldr_label="AI TLDR · GOV.UK" if generated else "Official brief · GOV.UK")

            # CVE matching is exact, even if the news item's wording is different.
            if tab == "cyber" and requests_left["ghsa"] > 0:
                ids = {m.group(0).upper() for a in cluster for m in tldr.CVE.finditer(a.title + " " + a.summary)}
                for cve in sorted(ids)[:1]:
                    requests_left["ghsa"] -= 1
                    url = "https://api.github.com/advisories?" + urllib.parse.urlencode({"cve_id": cve, "type": "reviewed", "per_page": 5})
                    data, fetched = tldr.cached_lookup(state, "ghsa:" + cve, url,
                        lambda payload: tldr.ghsa_brief(payload, cve), now, no_network)
                    changed = changed or fetched
                    if data and not any(a.source_id == "github-advisories" and a.url == data["url"] for a in cluster):
                        published = parse_dt(data.get("published")) or cluster[0].published
                        cluster.append(Article(title=data["title"], url=data["url"], published=published,
                            source_id="github-advisories", source_name="GitHub Advisory", publisher="GitHub Advisory Database",
                            country="US", language="en", kind="primary", tab=tab,
                            summary=data["summary"], tldr=data["tldr"], tldr_label=data["tldr_label"]))

            # Literature is linked only by an exact DOI, never by a fuzzy headline.
            if tab == "bio" and requests_left["epmc"] > 0:
                dois = {tldr.first_doi(a.url + " " + a.summary) for a in cluster}
                for doi in sorted(dois - {""})[:1]:
                    requests_left["epmc"] -= 1
                    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
                        {"query": f'DOI:"{doi}" AND (LANG:eng OR LANG:ger)',
                         "format": "json", "resultType": "core", "pageSize": 1})
                    data, fetched = tldr.cached_lookup(state, "epmc:" + doi, url,
                        lambda payload: tldr.epmc_study(payload, doi), now, no_network, ttl_hours=24)
                    changed = changed or fetched
                    if data and not any(a.source_id == "europe-pmc" and a.url == data["url"] for a in cluster):
                        published = parse_dt(data.get("published")) or cluster[0].published
                        cluster.append(Article(title=data["title"], url=data["url"], published=published,
                            source_id="europe-pmc", source_name="Europe PMC · study", publisher="Europe PMC",
                            country="GB", language="en", kind="primary", tab=tab, summary=data["summary"]))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description='Static news page for Rhacco News')
    ap.add_argument("--now", help="ISO-8601 test time")
    ap.add_argument("--no-network", action="store_true", help="Use cache only")
    ap.add_argument("--output", help="Override HTML output path")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    if not cfg:
        print("FATAL: missing/invalid config", file=sys.stderr)
        return 2
    now = parse_dt(args.now) if args.now else now_utc()
    if not now:
        print("FATAL: invalid --now", file=sys.stderr)
        return 2

    raw_state = load_json(STATE_PATH, None)
    if STATE_PATH.exists() and (
        not isinstance(raw_state, dict)
        or raw_state.get("version") not in {1, CACHE_SCHEMA_VERSION}
        or not isinstance(raw_state.get("sources"), dict)
    ):
        print("FATAL: invalid cache; keeping the previous page and cache", file=sys.stderr)
        return 2
    migrating = isinstance(raw_state, dict) and raw_state.get("version") == 1
    state = normalize_state(raw_state)
    errors: dict[str, str] = {}
    changed = migrating
    configured = {src["id"] for src in cfg.get("sources", [])}
    for old_id in set(state["sources"]) - configured:
        del state["sources"][old_id]
        changed = True
    articles: list[Article] = []

    for src in cfg.get("sources", []):
        if not src.get("enabled", True) or src.get("enrichment_only"):
            continue
        data, err, source_changed = refresh_source(state, src, now, args.no_network)
        changed = changed or source_changed
        if err:
            errors[src["id"]] = err
        articles.extend(to_articles(src, data, now, int(cfg.get("max_age_hours", 48))))

    # Exact URL dedupe before semantic clustering. Direct feeds beat discovery
    # aggregators for the same article so GDELT/API sightings never replace the
    # publisher's own feed metadata merely because their seen-time is newer.
    articles = dedupe_articles(articles)
    if not articles:
        print("FATAL: no recent news; keeping the previous page and cache", file=sys.stderr)
        return 1

    by_tab: dict[str, list[Article]] = {key: [] for key, _ in TOPICS}
    for art in articles:
        by_tab.setdefault(art.tab, []).append(art)

    clusters_by_tab: dict[str, list[list[Article]]] = {}
    limit = max(1, int(cfg.get("max_clusters_per_tab", 20)))
    for tab, tab_articles in by_tab.items():
        clusters = cluster_articles(tab_articles, int(cfg.get("cluster_window_hours", 18)))
        clusters.sort(key=lambda c: cluster_score(c, now), reverse=True)
        source_ids = [src["id"] for src in cfg.get("sources", []) if src.get("enabled", True) and src.get("tab") == tab]
        clusters_by_tab[tab] = select_display_candidates(clusters, limit, source_ids)

    changed = enrich_clusters(clusters_by_tab, state, now, args.no_network, cfg) or changed

    page = render_html(clusters_by_tab, cfg, state, now, errors)
    out = Path(args.output).resolve() if args.output else INDEX_PATH
    atomic_write(out, page)
    if changed or not STATE_PATH.exists():
        atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    counts = " ".join(f"{key}={len(clusters_by_tab.get(key, []))}" for key, _ in TOPICS)
    print(f"OK engine={ENGINE_VERSION} output={out} articles={len(articles)} {counts} errors={len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
