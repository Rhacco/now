#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parent
CONFIG_PATH = WORKER_ROOT / "config" / "settings.json"
STATE_PATH = WORKER_ROOT / "data" / "cache.json"
INDEX_PATH = REPO_ROOT / "gw2a" / "index.html"

URLS = {
    "catalog": "https://raw.githubusercontent.com/giovazz89/gw2-api-event-timers/main/events.json",
    "ninja": "https://gw2.ninja/timer",
    "metasheet": "https://docs.google.com/spreadsheets/d/1I2501rbqKjAD6HXQOtHAPQjLdqeS2tQIc9kEwyXuDKo/export?format=csv&gid=1233590379",
    "hardstuck": "https://hardstuck.gg/events/",
    "ttwurm": "https://sites.google.com/view/ttwurm/calendar",
    "dcap": "https://wiki.guildwars2.com/wiki/User:DCAP",
    "gw2community": "https://gw2community.de/calendar/calendar-export/273/",
    "vip": "https://gw2vip.net/",
    "choya": "https://choyaaa.com/meta",
    "fast": "https://fast.farming-community.eu/open-world/meta",
    "news": "https://www.guildwars2.com/en/feed/"
}

WORLD_BOSS_LOCATIONS = {
    "Admiral Taidha Covington": "Bloodtide Coast",
    "Claw of Jormag": "Frostgorge Sound",
    "Fire Elemental": "Metrica Province",
    "Golem Mark II": "Mount Maelstrom",
    "Great Jungle Wurm": "Caledon Forest",
    "Megadestroyer": "Mount Maelstrom",
    "Modniir Ulgoth": "Harathi Hinterlands",
    "Shadow Behemoth": "Queensdale",
    "Svanir Shaman Chief": "Wayfarer Foothills",
    "The Shatterer": "Blazeridge Steppes",
    "Evolved Jungle Wurm": "Bloodtide Coast",
    "Karka Queen": "Southsun Cove",
    "Tequatl the Sunless": "Sparkfly Fen"
}

LOCATION_OVERRIDES = {
    **WORLD_BOSS_LOCATIONS,
    "Dragonstorm": "Eye of the North",
    "Twisted Marionette (Public)": "Eye of the North",
    "Tower of Nightmares (Public)": "Eye of the North",
    "Battle For Lion's Arch (Public)": "Eye of the North"
}

TRACK_DISPLAY_OVERRIDES = {
    "Dragon's Stand": "Dragon's Stand"
}

EXCLUDED_TRACKS = {"Day and night", "Cantha: Day and night", "PvP Tournaments"}
EXCLUDED_EVENTS = {"Reset", "Target Practice", "Fly by Night", "Target Practice & Fly by Night"}
PHASE_PENALTIES = {
    "Pylons": -16,
    "Challenges": -8,
    "Help the Outposts": -18,
    "Prep": -10,
    "Preparations": -16,
    "Rounds 1 to 3": -12,
    "Day: Securing Verdant Brink": -18,
    "Night: Night and the Enemy": -10,
    "Crash Site": -12,
    "Escorts": -8
}

ALIASES = {
    "triple trouble": "Evolved Jungle Wurm",
    "triple trouble wurm": "Evolved Jungle Wurm",
    "tequatl": "Tequatl the Sunless",
    "chak gerent": "Chak Gerent",
    "octovine": "Octovine",
    "dragonstorm": "Dragonstorm",
    "dragon s end": "The Battle for the Jade Sea",
    "battle for the jade sea": "The Battle for the Jade Sea",
    "dragon s stand": "Dragon's Stand",
    "defense of amnytas": "Defense of Amnytas",
    "unlocking the wizard s tower": "Unlocking the Wizard's Tower",
    "convergence mount balrior": "Convergence: Mount Balrior",
    "convergence nexus of eternity": "Convergence: Nexus of Eternity",
    "nexus of eternity": "Convergence: Nexus of Eternity",
    "depths of cruelty": "Depths of Cruelty",
    "secrets of the weald": "Secrets of the Weald",
    "shackles of the ancients": "Shackles of the Ancients",
    "of mists and monsters": "Of Mists and Monsters",
    "a titanic voyage": "A Titanic Voyage",
    "aetherblade assault": "Aetherblade Assault",
    "kaineng blackout": "Kaineng Blackout",
    "gang war": "Gang War",
    "palawadan": "Palawadan",
    "death branded shatterer": "Death-Branded Shatterer",
    "ley line anomaly": "Ley-Line Anomaly"
}

@dataclass
class Candidate:
    event: str
    track: str
    category: str
    start: datetime
    end: datetime
    location: str
    waypoint: str
    source: str
    wiki: str = ""
    base_priority: int = 40
    score: float = 0.0
    lightning: bool = False
    community_sources: list[str] = field(default_factory=list)
    direct_waypoint: bool = False
    fast_seen: bool = False

    def key(self) -> str:
        minute = self.start.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
        return f"{norm(self.event)}|{minute}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def norm(value: str) -> str:
    value = html.unescape(value or "").lower()
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<script\b.*?</script>", " ", value)
    value = re.sub(r"(?is)<style\b.*?</style>", " ", value)
    value = re.sub(r"(?is)<[^>]+>", "\n", value)
    value = html.unescape(value)
    return "\n".join(x.strip() for x in value.splitlines() if x.strip())


def wiki_url(link: str) -> str:
    if not link:
        return ""
    return "https://wiki.guildwars2.com/wiki/" + urllib.parse.quote(link.replace(" ", "_"), safe="_()'!-:")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def fetch_text(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "gw2action/1.1.0 (+GitHub Actions; static community dashboard)",
            "Accept": "*/*",
            "Accept-Encoding": "identity"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def cache_fresh(entry: dict[str, Any], ttl_minutes: int, now: datetime) -> bool:
    checked = parse_iso(entry.get("checked_at"))
    return bool(checked and (now - checked) < timedelta(minutes=ttl_minutes))


def refresh_cached(
    state: dict[str, Any],
    name: str,
    ttl_minutes: int,
    parser: Callable[[str], Any],
    now: datetime,
    fetcher: Callable[[str], str] = fetch_text
) -> tuple[Any, bool, str | None]:
    entry = state.setdefault("sources", {}).get(name, {})
    if cache_fresh(entry, ttl_minutes, now) and "data" in entry:
        return entry["data"], False, None

    try:
        raw = fetcher(URLS[name])
        data = parser(raw)
        state["sources"][name] = {
            "checked_at": iso(now),
            "data": data
        }
        return data, True, None
    except Exception as exc:
        if "data" in entry:
            return entry["data"], False, f"{type(exc).__name__}: {exc}"
        return None, False, f"{type(exc).__name__}: {exc}"


def parse_catalog(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("catalog root is not a list")
    out = []
    for track in data:
        if not isinstance(track, dict) or not track.get("active", True):
            continue
        segments = []
        for seg in track.get("segments", []):
            if not isinstance(seg, dict):
                continue
            segments.append({
                "id": seg.get("id"),
                "name": seg.get("name", ""),
                "chatlink": seg.get("chatlink", ""),
                "link": seg.get("link", track.get("link", "")),
                "lfg": seg.get("lfg", 0) or 0,
                "rewards": seg.get("rewards", {}) or {}
            })
        out.append({
            "name": track.get("name", ""),
            "category": track.get("category", ""),
            "segments": segments,
            "sequences": track.get("sequences", {}) or {}
        })
    if len(out) < 20:
        raise ValueError(f"catalog unexpectedly small: {len(out)} tracks")
    return out


def parse_ninja(raw: str) -> dict[str, Any]:
    # GW2 Ninja embeds event occurrences as JSON in the server-rendered page.
    event_re = re.compile(
        r'"startMinute":(?P<start>\d+).*?"endMinute":(?P<end>\d+).*?'
        r'"isFiller":false,"event":\{.*?"name":"(?P<name>(?:\\.|[^"\\])*)".*?'
        r'"chatlink":"(?P<wp>\[&[A-Za-z0-9+/=]+\])"',
        re.S
    )
    occ = []
    for m in event_re.finditer(raw):
        name = bytes(m.group("name"), "utf-8").decode("unicode_escape", errors="ignore")
        occ.append({"start_minute": int(m.group("start")), "end_minute": int(m.group("end")), "event": name, "waypoint": m.group("wp")})
    if not occ:
        raise ValueError("no Ninja occurrences parsed")
    return {"occurrences": occ[:2000]}


def parse_metasheet(raw: str) -> list[dict[str, Any]]:
    out = []
    rows = csv.reader(raw.splitlines())
    pending_join = ""
    for row in rows:
        row = list(row) + [""] * (4 - len(row))
        left, event, date_text, note = [x.strip() for x in row[:4]]
        if left.lower().startswith("/sqjoin"):
            pending_join = left
        if not event or not re.match(r"\d{2}\.\d{2}\.\d{4}", date_text):
            continue
        try:
            dt = datetime.strptime(date_text, "%d.%m.%Y %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Berlin"))
        except ValueError:
            continue
        out.append({"title": event, "start": iso(dt), "sqjoin": pending_join, "note": note, "region": "EU"})
        pending_join = ""
    return out


def parse_hardstuck(raw: str) -> list[dict[str, Any]]:
    cards = re.findall(r'(?is)<a[^>]+href="(?P<href>/events/[^"]+)"[^>]*class="[^"]*event-card[^"]*"[^>]*>(?P<body>.*?)</a>', raw)
    out = []
    for href, body in cards:
        tm = re.search(r'<time[^>]+datetime="([^"]+)"', body, re.I)
        nm = re.search(r'(?is)<h4[^>]*class="[^"]*event-name[^"]*"[^>]*>(.*?)</h4>', body)
        if not tm or not nm:
            continue
        try:
            dt = datetime.strptime(html.unescape(tm.group(1)), "%B %d, %Y, %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        title = re.sub(r"\s+", " ", strip_tags(nm.group(1))).strip()
        region = "EU" if "region-bg-eu" in nm.group(1) else "NA" if "region-bg-na" in nm.group(1) else ""
        out.append({"title": title, "start": iso(dt), "end": iso(dt + timedelta(hours=2)), "region": region, "url": "https://hardstuck.gg" + href})
    return out


def parse_ttwurm(raw: str) -> dict[str, Any]:
    # Google Sites also embeds a clean text copy, which is more reliable than
    # reconstructing words split across decorative spans.
    days = []
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        m = re.search(rf"(?i)\b{day}\s+(\d{{1,2}})\s*:\s*(\d{{2}})\s+on\s+discord", raw)
        if not m:
            text = strip_tags(raw)
            m = re.search(rf"(?i)\b{day}\s+(\d{{1,2}})\s*:\s*(\d{{2}})", text)
        if m:
            days.append({"weekday": day, "hour": int(m.group(1)), "minute": int(m.group(2))})
    if not days:
        raise ValueError("TT schedule not found")
    return {"gathers_cet": days, "region": "EU"}


def parse_dcap(raw: str) -> dict[str, Any]:
    text = strip_tags(raw)
    known = [
        ("Triple Trouble", "Evolved Jungle Wurm", "12:30", "weekdays"),
        ("The Battle For Lion's Arch", "Battle For Lion's Arch (Public)", "14:00", "monday"),
        ("Dragonstorm", "Dragonstorm", "14:00", "tuesday"),
        ("Dragon's Stand", "Dragon's Stand", "14:30", "tuesday"),
        ("Secrets of the Weald", "Secrets of the Weald", "15:00", "wednesday"),
        ("The Twisted Marionette", "Twisted Marionette (Public)", "14:00", "thursday"),
        ("The Battle for the Jade Sea", "The Battle for the Jade Sea", "12:30", "weekend")
    ]
    found = []
    for label, target, hhmm, rule in known:
        if label.lower() in text.lower() and hhmm in text:
            found.append({"title": label, "target": target, "time_utc": hhmm, "rule": rule, "region": "NA"})
    if not found:
        raise ValueError("DCAP schedule not found")
    return {"events": found}


def unfold_ical(raw: str) -> list[str]:
    lines = raw.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in lines:
        if out and (line.startswith(" ") or line.startswith("\t")):
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_ical_dt(value: str) -> datetime | None:
    value = value.strip()
    for fmt, tz in [("%Y%m%dT%H%M%SZ", timezone.utc), ("%Y%m%dT%H%M%S", timezone.utc), ("%Y%m%dT%H%M", timezone.utc)]:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    return None


def parse_gw2community(raw: str) -> list[dict[str, Any]]:
    lines = unfold_ical(raw)
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = []
        elif line == "END:VEVENT" and cur is not None:
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(line)
    out = []
    for block in blocks:
        props: dict[str, str] = {}
        for line in block:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            props[k.split(";", 1)[0]] = v
        start = parse_ical_dt(props.get("DTSTART", ""))
        end = parse_ical_dt(props.get("DTEND", ""))
        if not start:
            continue
        out.append({
            "title": props.get("SUMMARY", "").replace("\\,", ","),
            "description": props.get("DESCRIPTION", "").replace("\\n", " "),
            "start": iso(start),
            "end": iso(end or (start + timedelta(hours=2))),
            "url": props.get("URL", ""),
            "region": "EU"
        })
    return out[-1000:]


def parse_vip(raw: str) -> list[dict[str, Any]]:
    pat = re.compile(
        r'\\?"summary\\?":\\?"(?P<title>.*?)\\?".*?'
        r'\\?"start\\?":\{\\?"dateTime\\?":\\?"(?P<start>[^"\\]+)\\?".*?\}.*?'
        r'\\?"end\\?":\{\\?"dateTime\\?":\\?"(?P<end>[^"\\]+)\\?"',
        re.S
    )
    out = []
    seen = set()
    for m in pat.finditer(raw):
        title = m.group("title").replace("\\u0026", "&").replace("\\\"", '"')
        start = parse_iso(m.group("start"))
        end = parse_iso(m.group("end"))
        if not start or not end:
            continue
        key = (title, iso(start))
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "start": iso(start), "end": iso(end), "region": "NA"})
    return out


def parse_choya(raw: str) -> dict[str, Any]:
    text = strip_tags(raw)
    join = ""
    jm = re.search(r"(?i)/sqjoin\s+[^\n<]{2,80}", text)
    if jm:
        join = jm.group(0).strip()
    pairs = []
    # Source label -> canonical timer event. Some route labels are playful.
    names = [
        ("Tequatl the Sunless", "Tequatl the Sunless"),
        ("Ley-Line Anomaly", "Ley-Line Anomaly"),
        ("Treasure Mushroom", "Treasure Mushroom"),
        ("Chak Gerent", "Chak Gerent"),
        ("Octovine", "Octovine"),
        ("The Ooze Pits", "Ooze Pits"),
        ("Shackles of the Choya", "Shackles of the Ancients")
    ]
    for source_name, canonical in names:
        i = text.lower().find(source_name.lower())
        if i < 0:
            continue
        snippet = text[i:i+360]
        wp = re.search(r"\[&[A-Za-z0-9+/=]{6,24}\]", snippet)
        pairs.append({"title": canonical, "waypoint": wp.group(0) if wp else ""})
    if not pairs:
        raise ValueError("Choyareset route not found")
    return {"sqjoin": join, "route": pairs}


def parse_fast(raw: str) -> dict[str, Any]:
    text = norm(strip_tags(raw))
    return {"normalized_text": text[:200000]}


def parse_news(raw: str) -> dict[str, Any]:
    text = strip_tags(raw)
    # Keep only compact lines that look like dated special-event announcements.
    lines = []
    for line in text.splitlines():
        if len(line) > 240:
            continue
        if re.search(r"(?i)(bonus event|rush event|fractal incursion|world boss|meta-event|convergence)", line):
            lines.append(line)
    return {"lines": lines[:100]}


def segment_location(track_name: str, event_name: str) -> str:
    if event_name in LOCATION_OVERRIDES:
        return LOCATION_OVERRIDES[event_name]
    if track_name == "Ley-Line Anomaly":
        return event_name
    return track_name


def display_name(track_name: str, event_name: str) -> str:
    if track_name in TRACK_DISPLAY_OVERRIDES:
        return TRACK_DISPLAY_OVERRIDES[track_name]
    return event_name


def base_priority(cfg: dict[str, Any], category: str, track: str, event: str, rewards: dict[str, Any], lfg: int) -> int:
    p = int(cfg.get("category_priority", {}).get(category, 40))
    override = cfg.get("priority_overrides", {})
    p = max(p, int(override.get(track, 0) or 0), int(override.get(event, 0) or 0))
    p += PHASE_PENALTIES.get(event, 0)
    if rewards.get("random_items"):
        p += 3
    if rewards.get("achievements"):
        p += 2
    if lfg:
        p += min(6, int(lfg) // 3)
    return max(1, min(110, p))


def emit_sequence(track: dict[str, Any], cfg: dict[str, Any], day: datetime) -> list[Candidate]:
    name = track.get("name", "")
    if name in EXCLUDED_TRACKS:
        return []
    seg_by_id = {s.get("id"): s for s in track.get("segments", [])}
    seqs = track.get("sequences", {}) or {}
    partial = seqs.get("partial", []) or []
    pattern = seqs.get("pattern", []) or []
    out: list[Candidate] = []
    cursor = day
    end_day = day + timedelta(days=1)

    def run(seq: list[dict[str, Any]], skip_first_emit: bool = False) -> None:
        nonlocal cursor
        for idx, item in enumerate(seq):
            minutes = int(item.get("d", 0) or 0)
            start = cursor
            end = start + timedelta(minutes=minutes)
            cursor = end
            seg = seg_by_id.get(item.get("r"), {})
            ev = seg.get("name", "")
            wp = seg.get("chatlink", "")
            # In this schedule format a shorter first partial segment is the
            # tail of an occurrence that started before UTC midnight. The
            # previous day's expansion already emits that real start time.
            if skip_first_emit and idx == 0:
                continue
            if not ev or ev in EXCLUDED_EVENTS or not wp:
                continue
            disp = display_name(name, ev)
            out.append(Candidate(
                event=disp,
                track=name,
                category=track.get("category", ""),
                start=start,
                end=end,
                location=segment_location(name, ev),
                waypoint=wp,
                source="gw2-api-event-timers",
                wiki=wiki_url(seg.get("link", "")),
                base_priority=base_priority(cfg, track.get("category", ""), name, ev, seg.get("rewards", {}) or {}, int(seg.get("lfg", 0) or 0))
            ))

    partial_is_tail = bool(
        partial and pattern
        and partial[0].get("r") == pattern[0].get("r")
        and int(partial[0].get("d", 0) or 0) < int(pattern[0].get("d", 0) or 0)
    )
    run(partial, skip_first_emit=partial_is_tail)
    if pattern:
        guard = 0
        while cursor < end_day and guard < 100:
            before = cursor
            run(pattern)
            if cursor <= before:
                break
            guard += 1
    return out


def catalog_candidates(catalog: list[dict[str, Any]], cfg: dict[str, Any], now: datetime) -> list[Candidate]:
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    out: list[Candidate] = []
    for delta in (-1, 0, 1, 2):
        day = midnight + timedelta(days=delta)
        for track in catalog:
            out.extend(emit_sequence(track, cfg, day))
    low = now - timedelta(hours=3)
    high = now + timedelta(hours=8)
    return [c for c in out if c.end >= low and c.start <= high]


def apply_ninja_waypoints(cands: list[Candidate], ninja: dict[str, Any] | None) -> None:
    if not ninja:
        return
    lookup: dict[str, str] = {}
    for o in ninja.get("occurrences", []):
        if o.get("event") and o.get("waypoint"):
            lookup[norm(o["event"])] = o["waypoint"]
    for c in cands:
        if not c.waypoint and norm(c.event) in lookup:
            c.waypoint = lookup[norm(c.event)]
            c.source += "+GW2 Ninja"


def nearest_target(cands: list[Candidate], target: str, when: datetime, window_minutes: int = 90) -> Candidate | None:
    tn = norm(target)
    matches = []
    for c in cands:
        if norm(c.event) == tn or norm(c.track) == tn:
            diff = abs((c.start - when).total_seconds()) / 60
            if diff <= window_minutes:
                matches.append((diff, c))
    return min(matches, key=lambda x: x[0])[1] if matches else None


def resolve_alias(title: str, cands: list[Candidate]) -> str | None:
    n = norm(title)
    for key, target in ALIASES.items():
        if key in n:
            return target
    # Exact occurrence name/track is safer than fuzzy matching.
    names = sorted({c.event for c in cands} | {c.track for c in cands}, key=len, reverse=True)
    for name in names:
        nn = norm(name)
        if len(nn) >= 7 and nn in n:
            return name
    return None


def add_community_signal(cands: list[Candidate], target: str, when: datetime, source: str, window: int = 120, direct_wp: str = "") -> None:
    cand = nearest_target(cands, target, when, window)
    if not cand:
        return
    cand.lightning = True
    if source not in cand.community_sources:
        cand.community_sources.append(source)
    if direct_wp:
        cand.waypoint = direct_wp
        cand.direct_waypoint = True


def weekday_match(rule: str, dt: datetime) -> bool:
    wd = dt.weekday()  # Mon=0
    return {
        "monday": wd == 0,
        "tuesday": wd == 1,
        "wednesday": wd == 2,
        "thursday": wd == 3,
        "friday": wd == 4,
        "weekdays": wd <= 4,
        "weekend": wd >= 5
    }.get(rule, True)


def apply_community(
    cands: list[Candidate],
    now: datetime,
    region: str,
    metasheet: list[dict[str, Any]] | None,
    hardstuck: list[dict[str, Any]] | None,
    ttwurm: dict[str, Any] | None,
    dcap: dict[str, Any] | None,
    gw2community: list[dict[str, Any]] | None,
    vip: list[dict[str, Any]] | None,
    choya: dict[str, Any] | None
) -> None:
    # Concrete public calendar rows.
    for source_name, rows in [("Meta-Train", metasheet or []), ("Hardstuck", hardstuck or []), ("GW2Community", gw2community or []), ("ViP", vip or [])]:
        for row in rows:
            row_region = row.get("region", "")
            if row_region and row_region != region:
                continue
            start = parse_iso(row.get("start"))
            if not start:
                continue
            target = resolve_alias(row.get("title", ""), cands)
            if target:
                add_community_signal(cands, target, start, source_name, 150)

    # Triple Trouble EU community: page publishes gather times in CET (fixed UTC+1).
    if ttwurm and region == "EU":
        fixed_cet = timezone(timedelta(hours=1))
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        by_day = {x["weekday"]: x for x in ttwurm.get("gathers_cet", [])}
        berlin = ZoneInfo("Europe/Berlin")
        local_now = now.astimezone(berlin)
        for delta in range(-1, 3):
            d = (local_now + timedelta(days=delta)).date()
            item = by_day.get(day_names[d.weekday()])
            if not item:
                continue
            gather = datetime(d.year, d.month, d.day, item["hour"], item["minute"], tzinfo=fixed_cet).astimezone(timezone.utc)
            # Organized map normally gathers 45 minutes before the relevant TT slot.
            add_community_signal(cands, "Evolved Jungle Wurm", gather + timedelta(minutes=45), "TT Wurm EU", 20)

    # DCAP runs are explicitly NA; do not promote them on an EU dashboard.
    if dcap and region == "NA":
        for item in dcap.get("events", []):
            hh, mm = map(int, item["time_utc"].split(":"))
            for delta in range(-1, 3):
                d = (now + timedelta(days=delta)).date()
                dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
                if weekday_match(item["rule"], dt):
                    add_community_signal(cands, item["target"], dt, "DCAP", 30)

    # Choyareset: daily route starts around daily reset. Use its direct waypoints only
    # when the corresponding timed event itself is present in the catalog window.
    if choya:
        route = choya.get("route", [])
        for item in route:
            target = ALIASES.get(norm(item.get("title", "")), item.get("title", ""))
            # Only route occurrences within 3 hours after reset are plausible train stops.
            for c in cands:
                if norm(c.event) != norm(target) and norm(c.track) != norm(target):
                    continue
                minutes_after_reset = (c.start.hour * 60 + c.start.minute)
                if 0 <= minutes_after_reset <= 180:
                    c.lightning = True
                    if "Choyareset" not in c.community_sources:
                        c.community_sources.append("Choyareset")
                    if item.get("waypoint"):
                        c.waypoint = item["waypoint"]
                        c.direct_waypoint = True


def apply_fast_context(cands: list[Candidate], fast: dict[str, Any] | None) -> None:
    if not fast:
        return
    text = fast.get("normalized_text", "")
    for c in cands:
        terms = [norm(c.event), norm(c.track)]
        if any(len(t) >= 6 and t in text for t in terms):
            c.fast_seen = True


def score_candidates(cands: list[Candidate], now: datetime) -> None:
    # Agreement count by event/start.
    counts: dict[str, int] = {}
    for c in cands:
        counts[c.key()] = counts.get(c.key(), 0) + 1
    for c in cands:
        score = float(c.base_priority)
        if c.lightning:
            score += 30
        score += min(12, 4 * max(0, counts.get(c.key(), 1) - 1))
        if c.fast_seen:
            score += 5
        if c.direct_waypoint:
            score += 3
        if c.start > now:
            mins = max(0.0, (c.start - now).total_seconds() / 60)
            score += max(0.0, 30.0 - mins / 4.0)
        else:
            # Prefer events that still have meaningful time left.
            remain = max(0.0, (c.end - now).total_seconds() / 60)
            score += min(12.0, remain / 3.0)
        c.score = score


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for c in cands:
        k = c.key()
        old = best.get(k)
        if not old or c.score > old.score:
            best[k] = c
    return list(best.values())


def choose(cands: list[Candidate], cfg: dict[str, Any], now: datetime) -> tuple[list[Candidate], list[Candidate]]:
    cands = [c for c in cands if c.waypoint]
    score_candidates(cands, now)
    cands = dedupe(cands)
    active = [c for c in cands if c.start <= now < c.end]
    upcoming_end = now + timedelta(minutes=int(cfg.get("upcoming_horizon_minutes", 180)))
    upcoming = [c for c in cands if now < c.start <= upcoming_end]

    active_top = sorted(active, key=lambda c: (-c.score, c.start))[: int(cfg.get("now_limit", 3))]
    upcoming_top = sorted(upcoming, key=lambda c: (-c.score, c.start))[: int(cfg.get("next_limit", 3))]
    # User-facing order is chronological after relevance selection.
    active_top.sort(key=lambda c: c.start)
    upcoming_top.sort(key=lambda c: c.start)
    return active_top, upcoming_top


def card_html(c: Candidate, tz: ZoneInfo, now: datetime, upcoming: bool) -> str:
    st = c.start.astimezone(tz)
    en = c.end.astimezone(tz)
    time_label = st.strftime("%H:%M")
    if not upcoming:
        time_label += "–" + en.strftime("%H:%M")
    flash = '<span class="flash" title="Community-/Sonderevent bestätigt">⚡</span>' if c.lightning else ""
    sources = " · ".join(c.community_sources) if c.community_sources else c.source
    wiki = f'<a class="wiki" href="{html.escape(c.wiki)}" target="_blank" rel="noopener">Wiki</a>' if c.wiki else ""
    return f'''<article class="event-card">
      <div class="time">{time_label}</div>
      <div class="info">
        <div class="name">{flash}{html.escape(c.event)}</div>
        <div class="location">{html.escape(c.location)}</div>
        <div class="meta">Priorität {int(round(c.score))} · {html.escape(sources)} {wiki}</div>
      </div>
      <button class="wp" data-copy="{html.escape(c.waypoint)}" title="Waypoint kopieren">{html.escape(c.waypoint)}</button>
    </article>'''


def render_html(active: list[Candidate], upcoming: list[Candidate], cfg: dict[str, Any], now: datetime, health: dict[str, str]) -> str:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Berlin"))
    def section(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return '<div class="empty">Keine ausreichend sicher auflösbaren Events gefunden.</div>'
        return "\n".join(card_html(c, tz, now, is_upcoming) for c in items)

    health_ok = sum(1 for v in health.values() if v == "ok")
    health_total = len(health)
    return f'''<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GW2 Action Now</title>
<style>
:root{{--bg:#0f1012;--panel:#17191d;--panel2:#1d2025;--line:#2b2f36;--text:#f3f4f6;--muted:#9aa1aa;--gold:#c8a85b;--green:#65a98a;--accent:#d96b70;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}}
.app{{max-width:920px;margin:auto;padding:18px}} header{{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 82%,rgba(15,16,18,0));padding:8px 0 18px;text-align:center}}
#clock{{font-size:23px;font-weight:800}} .sub{{font-size:12px;color:var(--muted);margin-top:5px}} h2{{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--gold);margin:18px 2px 8px}}
.event-card{{display:grid;grid-template-columns:82px 1fr 150px;align-items:center;gap:8px;min-height:72px;padding:10px 12px;margin:7px 0;background:var(--panel);border:1px solid var(--line);border-radius:12px}}
.event-card:hover{{background:var(--panel2)}} .time{{font:800 15px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}} .name{{font-size:16px;font-weight:800}} .flash{{margin-right:5px}} .location{{margin-top:4px;font-size:13px;color:#d6d8dc}} .meta{{margin-top:5px;font-size:10px;color:var(--muted)}}
.wp{{border:1px solid #424751;background:#101216;color:#fff;border-radius:9px;padding:10px 8px;cursor:pointer;font:800 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace}} .wp:hover{{border-color:#69717e;background:#151820}} .wiki{{color:var(--muted)}}
.empty{{padding:24px;text-align:center;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px}} footer{{font-size:10px;color:#737a84;text-align:center;padding:16px 2px}}
@media(max-width:680px){{.app{{padding:10px}}.event-card{{grid-template-columns:70px 1fr;grid-template-areas:'time info' 'wp wp'}}.time{{grid-area:time}}.info{{grid-area:info}}.wp{{grid-area:wp;width:100%}}}}
</style>
</head>
<body>
<div class="app">
<header><div id="clock"></div><div class="sub">Europe/Berlin · automatisch aktualisiert · Quellenstatus {health_ok}/{health_total}</div></header>
<h2>Jetzt · höchste Action</h2>
{section(active, False)}
<h2>Als Nächstes · höchste Priorität</h2>
{section(upcoming, True)}
<footer>⚡ = direktes Community-/Sonderevent-Signal · Waypoints werden nur bestätigt übernommen, nie geraten.</footer>
</div>
<script>
const fmt=new Intl.DateTimeFormat('de-DE',{{timeZone:'Europe/Berlin',weekday:'long',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}});
function tick(){{document.getElementById('clock').textContent=fmt.format(new Date());}} tick(); setInterval(tick,1000);
document.querySelectorAll('.wp').forEach(btn=>btn.addEventListener('click',async()=>{{const v=btn.dataset.copy;try{{await navigator.clipboard.writeText(v);const old=btn.textContent;btn.textContent='✓ '+v;setTimeout(()=>btn.textContent=old,900);}}catch(e){{}}}}));
</script>
</body></html>'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="test time as ISO-8601 UTC/local")
    ap.add_argument("--no-network", action="store_true", help="use committed cache only")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    state = load_json(STATE_PATH, {"version": 1, "sources": {}})
    state.setdefault("version", 1)
    state.setdefault("sources", {})
    now = parse_iso(args.now) if args.now else now_utc()
    if not now:
        raise SystemExit("invalid --now")

    ttl = cfg.get("source_ttls_minutes", {})
    errors: dict[str, str] = {}
    refreshed = False

    parsers = {
        "catalog": parse_catalog,
        "ninja": parse_ninja,
        "metasheet": parse_metasheet,
        "hardstuck": parse_hardstuck,
        "ttwurm": parse_ttwurm,
        "dcap": parse_dcap,
        "gw2community": parse_gw2community,
        "vip": parse_vip,
        "choya": parse_choya,
        "fast": parse_fast,
        "news": parse_news
    }

    values: dict[str, Any] = {}
    for name, parser in parsers.items():
        if args.no_network:
            values[name] = state["sources"].get(name, {}).get("data")
            if values[name] is None:
                errors[name] = "cache missing"
            continue
        data, changed, err = refresh_cached(state, name, int(ttl.get(name, 60)), parser, now)
        values[name] = data
        refreshed = refreshed or changed
        if err:
            errors[name] = err

    catalog = values.get("catalog") or []
    if not catalog:
        print("FATAL: no event catalog available", file=sys.stderr)
        return 2

    cands = catalog_candidates(catalog, cfg, now)
    apply_ninja_waypoints(cands, values.get("ninja"))
    apply_community(
        cands, now, cfg.get("region", "EU"),
        values.get("metasheet"), values.get("hardstuck"), values.get("ttwurm"), values.get("dcap"),
        values.get("gw2community"), values.get("vip"), values.get("choya")
    )
    apply_fast_context(cands, values.get("fast"))
    active, upcoming = choose(cands, cfg, now)

    health = {name: ("stale" if name in errors and values.get(name) is not None else "error" if name in errors else "ok") for name in parsers}
    page = render_html(active, upcoming, cfg, now, health)

    old_page = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
    page_changed = page != old_page
    if page_changed:
        atomic_write(INDEX_PATH, page)
    if refreshed or not STATE_PATH.exists():
        atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    print(f"candidates={len(cands)} active={len(active)} upcoming={len(upcoming)} page_changed={page_changed} sources_refreshed={refreshed}")
    if errors:
        print("source warnings:")
        for k, v in errors.items():
            print(f"  {k}: {v}")
    for label, items in [("NOW", active), ("NEXT", upcoming)]:
        for c in items:
            print(f"{label} {c.start.isoformat()} {c.event} {c.location} {c.waypoint} score={c.score:.1f} lightning={c.lightning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
