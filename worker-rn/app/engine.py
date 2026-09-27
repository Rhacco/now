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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parent
ENGINE_VERSION = "0.1.2"
CONFIG_PATH = WORKER_ROOT / "config" / "settings.json"
STATE_PATH = WORKER_ROOT / "data" / "cache.json"
INDEX_PATH = REPO_ROOT / "rn" / "index.html"
CACHE_SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
USER_AGENT = f"RhaccoNews/{ENGINE_VERSION} (+static GitHub Actions news aggregator)"

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


def normalize_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("version") != CACHE_SCHEMA_VERSION:
        return {"version": CACHE_SCHEMA_VERSION, "sources": {}}
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
        }
        parser = parsers.get(fmt, parse_feed)
        data = parser(raw, src, now)
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
    for item in data:
        published = parse_dt(item.get("published")) or now
        if now - published > timedelta(hours=max_age_hours):
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
            language=str(item.get("language") or src.get("language") or "").lower(),
            kind=str(src.get("kind") or "editorial"),
            tab=str(src.get("tab") or "world"),
            summary=str(item.get("summary") or "")[:500],
            via=str(item.get("via") or ""),
        ))
    return [a for a in out if a.title and a.url]


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[\wÄÖÜäöüßÀ-ÿ]{3,}", title.casefold(), flags=re.UNICODE)
    return {w for w in words if w not in STOPWORDS and not w.isdigit()}


def similarity(a: Article, b: Article) -> float:
    if canonical_url(a.url) == canonical_url(b.url):
        return 1.0
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
        return "gerade eben"
    if mins < 60:
        return f"vor {mins} Min."
    hours = mins // 60
    if hours < 24:
        return f"vor {hours} Std."
    return f"vor {hours // 24} T."


def source_chip(a: Article, now: datetime) -> str:
    kind = "Primär" if a.kind == "primary" else ("Discovery" if a.kind == "discovery" else "Medium")
    via = f" · via {html.escape(a.via)}" if a.via else ""
    title = f"{a.source_name} · {kind} · {age_label(a.published, now)}{via}"
    return (
        f'<a class="source-chip kind-{html.escape(a.kind)}" href="{html.escape(a.url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" title="{html.escape(title, quote=True)}">'
        f'<span>{html.escape(a.source_name)}</span><small>{html.escape(a.country or "—")}</small></a>'
    )


def card_html(cluster: list[Article], now: datetime, demo: bool) -> str:
    rep = representative(cluster)
    non_discovery = [a for a in cluster if a.kind != "discovery"]
    publisher_count = len({a.publisher.casefold() for a in non_discovery})
    countries = len({a.country for a in non_discovery if a.country})
    primary = any(a.kind == "primary" for a in cluster)
    chips = "".join(source_chip(a, now) for a in sorted(cluster, key=lambda x: (x.kind == "discovery", -x.published.timestamp()))[:8])
    source_word = "Quelle" if len(non_discovery) == 1 else "Quellen"
    publisher_word = "Herausgeber" if publisher_count == 1 else "Herausgeber"
    meta = f"{len(non_discovery)} {source_word} · {publisher_count} {publisher_word}"
    if countries > 1:
        meta += f" · {countries} Länder"
    if primary:
        meta += " · Primärquelle"
    demo_badge = '<span class="badge demo-badge">DEMO</span>' if demo else ""
    # Public live page deliberately avoids republishing feed descriptions.
    summary = strip_tags(rep.summary) if demo else ""
    summary_html = f'<p class="summary">{html.escape(summary[:220])}</p>' if summary else ""
    return f"""
    <article class="story-card">
      <div class="story-topline"><span>{html.escape(age_label(rep.published, now))}</span><span class="source-count">{html.escape(meta)}</span>{demo_badge}</div>
      <h2><a href="{html.escape(rep.url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(rep.title)}</a></h2>
      {summary_html}
      <div class="source-row">{chips}</div>
    </article>
    """


def status_summary(cfg: dict[str, Any], state: dict[str, Any], now: datetime) -> tuple[int, int, int, int]:
    enabled = [s for s in cfg.get("sources", []) if s.get("enabled", True)]
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


def render_html(clusters_by_tab: dict[str, list[list[Article]]], cfg: dict[str, Any], state: dict[str, Any], now: datetime, demo: bool, errors: dict[str, str]) -> str:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Berlin"))
    local = now.astimezone(tz)
    world = "\n".join(card_html(c, now, demo) for c in clusters_by_tab.get("world", [])) or '<div class="empty">Keine aktuellen Meldungen im Zeitfenster.</div>'
    dach = "\n".join(card_html(c, now, demo) for c in clusters_by_tab.get("dach", [])) or '<div class="empty">Keine aktuellen Meldungen im Zeitfenster.</div>'
    ok, stale, failed, waiting = status_summary(cfg, state, now)
    if demo:
        status_text = f"Prototyp · {len([s for s in cfg.get('sources',[]) if s.get('enabled',True)])} Quellen konfiguriert · keine Live-Abfrage im Snapshot"
    else:
        status_text = f"Quellenstatus: {ok} aktuell · {stale} veraltet · {failed} Fehler/leer · {waiting} optionale API ohne Secret"
    error_note = ""
    if errors and not demo:
        error_note = f'<details class="errors"><summary>{len(errors)} Quellen mit Fehler/Fallback</summary><pre>{html.escape(json.dumps(errors, ensure_ascii=False, indent=2))}</pre></details>'
    demo_banner = '<div class="demo-banner">HTML-Snapshot mit DEMO-DATEN – keine Live-Nachrichten.</div>' if demo else ""
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>&quot;Rhacco News&quot;</title>
<style>
:root{{--bg:#0b0c0f;--panel:#14161b;--panel2:#1a1d24;--text:#f3f4f6;--muted:#9ca3af;--line:#2a2f39;--accent:#7c5cff;--accent2:#a78bfa;--good:#69d18f;--warn:#f4c95d;--shadow:0 14px 45px rgba(0,0,0,.24)}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% -10%,#1a1730 0,#0b0c0f 34%,#090a0d 100%);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;min-height:100vh}}a{{color:inherit}}.shell{{width:min(930px,calc(100% - 28px));margin:0 auto;padding:34px 0 54px}}header{{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:20px}}h1{{font-size:31px;letter-spacing:-.04em;margin:0}}.updated{{color:var(--muted);font-size:13px;text-align:right}}.demo-banner{{border:1px solid #6654bc;background:#211b3a;color:#d9d1ff;border-radius:12px;padding:9px 12px;margin-bottom:14px;font-size:13px}}.tabs{{position:sticky;top:0;z-index:5;display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:5px;background:rgba(20,22,27,.94);backdrop-filter:blur(14px);border:1px solid var(--line);border-radius:14px;margin-bottom:16px;box-shadow:var(--shadow)}}.tab{{appearance:none;border:0;border-radius:10px;padding:11px 12px;background:transparent;color:var(--muted);font-weight:750;cursor:pointer}}.tab.active{{background:#28223f;color:#fff;box-shadow:inset 0 0 0 1px #51417f}}.pane{{display:none}}.pane.active{{display:block}}.story-card{{background:linear-gradient(180deg,var(--panel),#111318);border:1px solid var(--line);border-radius:16px;padding:17px 18px 15px;margin:0 0 12px;box-shadow:var(--shadow)}}.story-topline{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--muted);font-size:12px;margin-bottom:8px}}.source-count{{color:#c8cbd2}}.badge{{border-radius:999px;border:1px solid var(--line);padding:2px 7px;font-weight:750;letter-spacing:.02em}}.demo-badge{{color:#d9d1ff;border-color:#6654bc;background:#211b3a}}h2{{font-size:20px;line-height:1.28;letter-spacing:-.015em;margin:0 0 7px}}h2 a{{text-decoration:none}}h2 a:hover{{text-decoration:underline;text-decoration-color:#7665bd;text-underline-offset:3px}}.summary{{color:#c4c7ce;margin:0 0 12px;font-size:14px}}.source-row{{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}}.source-chip{{display:inline-flex;gap:7px;align-items:center;text-decoration:none;border:1px solid var(--line);background:var(--panel2);padding:6px 8px;border-radius:9px;font-size:12px;color:#e3e4e8}}.source-chip:hover{{border-color:#625492}}.source-chip small{{color:var(--muted);font-size:10px}}.source-chip.kind-primary{{border-color:#315c42;background:#122319}}.source-chip.kind-discovery{{border-style:dashed;color:#b8bbc3}}.empty{{border:1px dashed var(--line);border-radius:14px;padding:24px;text-align:center;color:var(--muted)}}footer{{margin-top:22px;color:var(--muted);font-size:12px;text-align:center}}.errors{{margin-top:13px;text-align:left;border:1px solid var(--line);border-radius:10px;padding:8px 10px}}pre{{white-space:pre-wrap;word-break:break-word;font-size:11px}}@media(max-width:620px){{.shell{{width:min(100% - 18px,930px);padding-top:20px}}header{{align-items:flex-start;flex-direction:column;gap:6px}}.updated{{text-align:left}}h1{{font-size:27px}}h2{{font-size:18px}}.story-card{{padding:15px}}}}
</style>
</head>
<body>
<div class="shell">
<header><div><h1>&quot;Rhacco News&quot;</h1><div class="updated">Live-Quellen-Aggregator · Workflow alle {int(cfg.get("run_interval_minutes", 5))} Min. · Quellen mit eigenem TTL</div></div><div class="updated">Stand: {html.escape(local.strftime('%d.%m.%Y · %H:%M'))}</div></header>
{demo_banner}
<nav class="tabs" aria-label="News-Bereiche">
<button class="tab active" data-tab="world" type="button">Weltpolitik</button>
<button class="tab" data-tab="dach" type="button">DACH + Luxemburg</button>
</nav>
<main>
<section id="pane-world" class="pane active">{world}</section>
<section id="pane-dach" class="pane">{dach}</section>
</main>
<footer>{html.escape(status_text)}{error_note}</footer>
</div>
<script>
(()=>{{
 const buttons=[...document.querySelectorAll('.tab')];
 const panes=[...document.querySelectorAll('.pane')];
 const key='rhacco-news-tab-v1';
 function setTab(name){{
   buttons.forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
   panes.forEach(p=>p.classList.toggle('active',p.id==='pane-'+name));
   try{{localStorage.setItem(key,name)}}catch(e){{}}
 }}
 buttons.forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
 let saved='world';try{{saved=localStorage.getItem(key)||'world'}}catch(e){{}}
 if(!buttons.some(b=>b.dataset.tab===saved))saved='world';setTab(saved);
}})();
</script>
</body></html>"""


def demo_articles(now: datetime) -> list[Article]:
    def a(title: str, mins: int, source: str, publisher: str, country: str, kind: str, tab: str, url: str, summary: str = "") -> Article:
        return Article(title, url, now - timedelta(minutes=mins), source.casefold().replace(" ", "-"), source, publisher, country, "de", kind, tab, summary)
    return [
        a("Beispiel: Mehrere Staaten beraten über neue gemeinsame Maßnahmen", 12, "Deutschlandfunk", "Deutschlandradio", "DE", "editorial", "world", "https://example.com/world-1-dlf", "Mehrere voneinander getrennte Quellen berichten über dasselbe internationale Ereignis."),
        a("Beispiel: Staaten beraten über gemeinsame Maßnahmen", 18, "DER STANDARD", "STANDARD", "AT", "editorial", "world", "https://example.com/world-1-standard"),
        a("Beispiel: Staaten beraten über neue gemeinsame Maßnahmen", 24, "SRF", "SRG SSR", "CH", "editorial", "world", "https://example.com/world-1-srf"),
        a("Beispiel: Offizielle Mitteilung – Staaten beraten gemeinsame Maßnahmen", 9, "EU-Kommission", "EU", "EU", "primary", "world", "https://example.com/world-1-primary"),
        a("Beispiel: Zweites weltpolitisches Ereignis mit zwei Medienquellen", 37, "RTL Today", "RTL", "LU", "editorial", "world", "https://example.com/world-2-rtl"),
        a("Beispiel: Zweites weltpolitisches Ereignis mit zwei Medienquellen", 44, "Deutschlandfunk", "Deutschlandradio", "DE", "editorial", "world", "https://example.com/world-2-dlf"),
        a("Beispiel: Grenzüberschreitendes DACH-Thema wird neu geregelt", 16, "Deutschlandfunk", "Deutschlandradio", "DE", "editorial", "dach", "https://example.com/dach-1-dlf", "Der Prototyp bündelt ähnliche Meldungen und zählt getrennte Herausgeber statt bloß Feed-Einträge."),
        a("Beispiel: Grenzüberschreitendes DACH-Thema wird neu geregelt", 22, "ORF", "ORF", "AT", "editorial", "dach", "https://example.com/dach-1-orf"),
        a("Beispiel: Grenzüberschreitendes DACH-Thema wird neu geregelt", 28, "SRF", "SRG SSR", "CH", "editorial", "dach", "https://example.com/dach-1-srf"),
        a("Beispiel: Luxemburg veröffentlicht eine neue nationale Mitteilung", 31, "RTL Luxembourg", "RTL", "LU", "editorial", "dach", "https://example.com/dach-2-rtl"),
        a("Beispiel: Offizielle Mitteilung aus Deutschland", 47, "Bundesregierung", "Bund", "DE", "primary", "dach", "https://example.com/dach-3-bund"),
    ]


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


def main() -> int:
    ap = argparse.ArgumentParser(description='Static two-tab aggregator for "Rhacco News"')
    ap.add_argument("--now", help="ISO-8601 test time")
    ap.add_argument("--no-network", action="store_true", help="Use cache only")
    ap.add_argument("--demo", action="store_true", help="Generate deterministic demo snapshot")
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

    state = normalize_state(load_json(STATE_PATH, {}))
    errors: dict[str, str] = {}
    changed = False
    articles: list[Article] = []

    if args.demo:
        articles = demo_articles(now)
    else:
        for src in cfg.get("sources", []):
            if not src.get("enabled", True):
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

    by_tab: dict[str, list[Article]] = {"world": [], "dach": []}
    for art in articles:
        by_tab.setdefault(art.tab, []).append(art)

    clusters_by_tab: dict[str, list[list[Article]]] = {}
    for tab, tab_articles in by_tab.items():
        clusters = cluster_articles(tab_articles, int(cfg.get("cluster_window_hours", 18)))
        clusters.sort(key=lambda c: cluster_score(c, now), reverse=True)
        clusters_by_tab[tab] = clusters[: int(cfg.get("max_clusters_per_tab", 20))]

    page = render_html(clusters_by_tab, cfg, state, now, args.demo, errors)
    out = Path(args.output).resolve() if args.output else INDEX_PATH
    atomic_write(out, page)
    if changed or not STATE_PATH.exists():
        atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"OK engine={ENGINE_VERSION} output={out} articles={len(articles)} world={len(clusters_by_tab.get('world', []))} dach={len(clusters_by_tab.get('dach', []))} errors={len(errors)} demo={args.demo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
