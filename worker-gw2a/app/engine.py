#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
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
    "maps": "https://api.guildwars2.com/v2/maps?ids=all&lang=en",
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

ANNOUNCEMENT_SOURCE_URLS = {
    "Meta-Train": "https://docs.google.com/spreadsheets/d/1I2501rbqKjAD6HXQOtHAPQjLdqeS2tQIc9kEwyXuDKo/edit",
    "Hardstuck": "https://hardstuck.gg/events/",
    "GW2Community": "https://gw2community.de/calendar/",
    "ViP": "https://gw2vip.net/",
    "TT Wurm EU": "https://sites.google.com/view/ttwurm/calendar",
    "DCAP": "https://wiki.guildwars2.com/wiki/User:DCAP",
    "Choyareset": "https://choyaaa.com/meta",
}


CACHE_SCHEMA_VERSION = 2
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

SOURCE_DATA_VERSIONS = {
    "maps": 1,
    "catalog": 1,
    "ninja": 1,
    "metasheet": 1,
    "hardstuck": 1,
    "ttwurm": 1,
    "dcap": 1,
    "gw2community": 1,
    "vip": 1,
    "choya": 1,
    "fast": 1,
    "news": 1,
}

EMPTY_RESULT_GUARDS = {
    "metasheet",
    "hardstuck",
    "gw2community",
    "vip",
}

DROP_GUARDS = {
    "metasheet",
    "hardstuck",
    "gw2community",
    "vip",
}

ANNOUNCEMENT_ALLOWED_HOSTS = {
    "docs.google.com",
    "hardstuck.gg",
    "www.hardstuck.gg",
    "gw2community.de",
    "www.gw2community.de",
    "gw2vip.net",
    "www.gw2vip.net",
    "sites.google.com",
    "wiki.guildwars2.com",
    "choyaaa.com",
    "www.choyaaa.com",
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

CATALOG_TRACKS_IGNORED = {
    "Day and night", "Cantha: Day and night", "PvP Tournaments"
}
CATALOG_TRACKS_REPLACED_BY_VERIFIED_SCHEDULE = {
    "Scarlet's Invasion"
}
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

SPECIAL_RECURRING_TERMS = (
    # Rotating / invasion / incursion / anomaly style events.
    "fractal incursion", "scarlet invasion", "awakened invasion",
    "ley line anomaly", "dragonstorm", "convergence",
    "twisted marionette", "battle for lion s arch", "tower of nightmares",

    # Recognisable recurring world bosses from the old loop.
    "admiral taidha covington", "taidha covington",
    "the shatterer", "shatterer", "shadow behemoth",
    "evolved jungle wurm", "great jungle wurm", "tequatl",

    # Strong repeating metas.
    "octovine", "chak gerent", "dragon s stand", "battle for the jade sea",
    "choya pinata", "palawadan", "death branded shatterer",
    "aetherblade assault", "kaineng blackout", "gang war"
)

# Confirmed destination/waypoint table for the verified rotating-event layer.
# This is not a priority list; it maps a known rotating map to a useful destination.
ROTATING_EVENT_DESTINATIONS = {
    "Kessex Hills": {"place": "Cereboth Canyon", "waypoint": "[&BBIAAAA=]"},
    "Diessa Plateau": {"place": "Rancher's Wash", "waypoint": "[&BN0AAAA=]"},
    "Brisban Wildlands": {"place": "Venlin Vale", "waypoint": "[&BHUAAAA=]"},
    "Snowden Drifts": {"place": "The Frozen Sweeps", "waypoint": "[&BLQAAAA=]"},
    "Gendarran Fields": {"place": "Provern Shore", "waypoint": "[&BOQAAAA=]"},
    "Southsun Cove": {"place": "Kiel's Outpost", "waypoint": "[&BNwGAAA=]"},
    "Metrica Province": {"place": "Muridian", "waypoint": "[&BEcAAAA=]"},
    "Caledon Forest": {"place": "Twilight Arbor", "waypoint": "[&BEEFAAA=]"},
    "Queensdale": {"place": "Swamplost Haven", "waypoint": "[&BPcAAAA=]"},
    "Wayfarer Foothills": {"place": "Krennak's Homestead", "waypoint": "[&BMIDAAA=]"},
    "Plains of Ashford": {"place": "Loreclaw", "waypoint": "[&BMcDAAA=]"},
}

ROTATING_EVENT_WIKI = {
    "Fractal Incursion": "https://wiki.guildwars2.com/wiki/Defeat_the_enemy_spawned_by_the_fractal_incursion",
    "Awakened Invasion": "https://wiki.guildwars2.com/wiki/Defeat_the_invading_Awakened",
    "Scarlet's Invasion": "https://wiki.guildwars2.com/wiki/Defeat_the_invading_minions_of_Scarlet_Briar",
}

# Recommendation lifetime after START, based on event scope/structure rather
# than character level or the priority score. All values stay within 5–10 min.
ACTION_WINDOW_OVERRIDES = (
    (("dragon s stand", "battle for the jade sea", "defense of amnytas",
      "unlocking the wizard s tower", "palawadan", "convergence",
      "scarlet s invasion", "awakened invasion", "evolved jungle wurm",
      "triple trouble"), 10),
    (("octovine", "chak gerent", "dragonstorm", "tequatl",
      "aetherblade assault", "kaineng blackout", "gang war"), 9),
    (("fractal incursion", "admiral taidha covington", "shatterer",
      "karka queen", "modniir ulgoth", "death branded shatterer"), 7),
    (("ley line anomaly", "choya pinata", "shadow behemoth",
      "great jungle wurm", "fire elemental", "svanir shaman",
      "megadestroyer"), 5),
)

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
    announcement_url: str = ""
    direct_waypoint: bool = False
    fast_seen: bool = False
    level: int | None = None
    level_min: int | None = None
    special: bool = False

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


def safe_https_url(value: str, allowed_hosts: set[str] | None = None) -> str:
    value = html.unescape((value or "").strip())
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not host:
        return ""
    if allowed_hosts and host not in allowed_hosts:
        return ""
    return urllib.parse.urlunsplit(parsed)


def fetch_text(url: str, timeout: int = 15, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "gw2action/1.7.0 (+GitHub Actions; static community dashboard)",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length:
            try:
                content_length = int(length)
            except ValueError:
                content_length = 0
            if content_length > max_bytes:
                raise ValueError(f"response too large: {content_length} bytes")

        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"response too large: >{max_bytes} bytes")

        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def normalize_state(raw: Any) -> tuple[dict[str, Any], bool]:
    changed = False

    if not isinstance(raw, dict):
        raw = {}
        changed = True

    sources = raw.get("sources")
    if not isinstance(sources, dict):
        sources = {}
        changed = True

    state = dict(raw)
    state["sources"] = sources

    if state.get("version") != CACHE_SCHEMA_VERSION:
        state["version"] = CACHE_SCHEMA_VERSION
        changed = True

    for name, entry in list(sources.items()):
        if not isinstance(entry, dict):
            sources[name] = {}
            changed = True
            continue

        if entry.get("checked_at") and not entry.get("last_success"):
            entry["last_success"] = entry["checked_at"]
            changed = True

        defaults = {
            "failure_count": 0,
            "last_failure": "",
            "retry_after": "",
            "last_error": "",
            "pending_drop_count": None,
            "pending_drop_seen": 0,
            "data_version": SOURCE_DATA_VERSIONS.get(name, 1),
        }
        for key, value in defaults.items():
            if key not in entry:
                entry[key] = value
                changed = True

    return state, changed


def source_item_count(name: str, data: Any) -> int | None:
    if data is None:
        return None
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None

    key_map = {
        "ninja": "occurrences",
        "ttwurm": "gathers_cet",
        "dcap": "events",
        "choya": "route",
        "news": "lines",
    }
    key = key_map.get(name)
    if key and isinstance(data.get(key), list):
        return len(data[key])

    if name == "fast":
        return len(data.get("normalized_text", ""))

    return None


def backoff_minutes(failure_count: int) -> int:
    if failure_count <= 1:
        return 1
    if failure_count == 2:
        return 5
    if failure_count <= 4:
        return 15
    return 30


def record_failure(entry: dict[str, Any], now: datetime, message: str) -> None:
    count = int(entry.get("failure_count", 0) or 0) + 1
    delay = backoff_minutes(count)
    entry["failure_count"] = count
    entry["last_failure"] = iso(now)
    entry["retry_after"] = iso(now + timedelta(minutes=delay))
    entry["last_error"] = message[:300]


def clear_failure(entry: dict[str, Any]) -> None:
    entry["failure_count"] = 0
    entry["last_failure"] = ""
    entry["retry_after"] = ""
    entry["last_error"] = ""


def cache_fresh(entry: dict[str, Any], ttl_minutes: int, now: datetime) -> bool:
    checked = parse_iso(entry.get("last_success") or entry.get("checked_at"))
    return bool(checked and (now - checked) < timedelta(minutes=ttl_minutes))


def retry_paused(entry: dict[str, Any], now: datetime) -> bool:
    retry_at = parse_iso(entry.get("retry_after"))
    return bool(retry_at and now < retry_at)


def suspicious_result(
    name: str,
    data: Any,
    previous: Any,
    entry: dict[str, Any],
) -> tuple[bool, str]:
    new_count = source_item_count(name, data)
    old_count = source_item_count(name, previous)

    if new_count is None or old_count is None or old_count <= 0:
        entry["pending_drop_count"] = None
        entry["pending_drop_seen"] = 0
        return False, ""

    if name in EMPTY_RESULT_GUARDS and new_count == 0:
        entry["pending_drop_count"] = 0
        entry["pending_drop_seen"] = int(entry.get("pending_drop_seen", 0) or 0) + 1
        return True, f"suspicious empty result; keeping {old_count} cached items"

    if (
        name in DROP_GUARDS
        and old_count >= 10
        and 0 < new_count <= max(1, int(old_count * 0.2))
    ):
        pending_count = entry.get("pending_drop_count")
        pending_seen = int(entry.get("pending_drop_seen", 0) or 0)
        if pending_count == new_count and pending_seen >= 1:
            entry["pending_drop_count"] = None
            entry["pending_drop_seen"] = 0
            return False, ""

        entry["pending_drop_count"] = new_count
        entry["pending_drop_seen"] = pending_seen + 1
        return True, f"suspicious item drop {old_count}->{new_count}; awaiting confirmation"

    entry["pending_drop_count"] = None
    entry["pending_drop_seen"] = 0
    return False, ""


def refresh_cached(
    state: dict[str, Any],
    name: str,
    ttl_minutes: int,
    parser: Callable[[str], Any],
    now: datetime,
    fetcher: Callable[[str], str] = fetch_text,
) -> tuple[Any, bool, str | None]:
    sources = state.setdefault("sources", {})
    entry = sources.setdefault(name, {})
    previous = entry.get("data")

    expected_version = SOURCE_DATA_VERSIONS.get(name, 1)
    cached_version = int(entry.get("data_version", expected_version) or expected_version)

    if cached_version != expected_version:
        previous = None

    if cache_fresh(entry, ttl_minutes, now) and previous is not None:
        return previous, False, None

    if retry_paused(entry, now):
        return previous, False, f"retry paused until {entry.get('retry_after', '')}"

    try:
        raw = fetcher(URLS[name])
        data = parser(raw)

        suspicious, reason = suspicious_result(name, data, previous, entry)
        if suspicious and previous is not None:
            record_failure(entry, now, reason)
            return previous, True, reason

        entry["data"] = data
        entry["data_version"] = expected_version
        entry["checked_at"] = iso(now)
        entry["last_success"] = iso(now)
        clear_failure(entry)
        entry["pending_drop_count"] = None
        entry["pending_drop_seen"] = 0
        return data, True, None

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        record_failure(entry, now, message)
        return previous, True, message


def source_age_minutes(state: dict[str, Any], name: str, now: datetime) -> float | None:
    entry = state.get("sources", {}).get(name, {})
    last_success = parse_iso(entry.get("last_success") or entry.get("checked_at"))
    if not last_success:
        return None
    return max(0.0, (now - last_success).total_seconds() / 60.0)


def user_status_notice(
    state: dict[str, Any],
    ttl: dict[str, Any],
    now: datetime,
) -> str:
    notices: list[str] = []

    catalog_age = source_age_minutes(state, "catalog", now)
    catalog_entry = state.get("sources", {}).get("catalog", {})
    catalog_failed = int(catalog_entry.get("failure_count", 0) or 0) > 0

    if catalog_age is not None and (
        catalog_age > 24 * 60
        or (catalog_failed and catalog_age > max(120, int(ttl.get("catalog", 720)) * 2))
    ):
        notices.append("Event data may be delayed.")

    community_names = [
        "metasheet",
        "hardstuck",
        "ttwurm",
        "gw2community",
        "vip",
        "choya",
    ]
    degraded = 0
    for name in community_names:
        entry = state.get("sources", {}).get(name, {})
        age = source_age_minutes(state, name, now)
        source_ttl = int(ttl.get(name, 15) or 15)
        failed = int(entry.get("failure_count", 0) or 0) > 0
        missing = entry.get("data") is None
        stale = age is not None and age > max(60, source_ttl * 3)
        if missing or (failed and stale):
            degraded += 1

    if degraded >= 3:
        notices.append("Community signals may be limited.")

    return " ".join(notices)


def parse_maps(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("maps root is not a list")
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append({
            "name": item.get("name", ""),
            "min_level": int(item.get("min_level", 0) or 0),
            "max_level": int(item.get("max_level", 0) or 0),
            "type": item.get("type", "")
        })
    if len(out) < 50:
        raise ValueError(f"maps unexpectedly small: {len(out)}")
    return out


def is_special_recurring(event: str, track: str) -> bool:
    text = norm(event + " " + track)
    return any(term in text for term in SPECIAL_RECURRING_TERMS)


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
    if name in CATALOG_TRACKS_IGNORED or name in CATALOG_TRACKS_REPLACED_BY_VERIFIED_SCHEDULE:
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
                base_priority=base_priority(cfg, track.get("category", ""), name, ev, seg.get("rewards", {}) or {}, int(seg.get("lfg", 0) or 0)),
                special=is_special_recurring(disp, name)
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


def verified_rotating_event_candidates(now: datetime) -> list[Candidate]:
    """Small verified schedule layer for recurring Core Tyria special events."""
    out: list[Candidate] = []
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    fractal_maps = ["Kessex Hills", "Diessa Plateau", "Brisban Wildlands", "Snowden Drifts"]
    awakened_maps = [
        "Southsun Cove", "Metrica Province", "Caledon Forest",
        "Queensdale", "Wayfarer Foothills", "Plains of Ashford",
        "Gendarran Fields"
    ]

    for day_delta in (-1, 0, 1, 2):
        day = midnight + timedelta(days=day_delta)
        for hour in range(24):
            start = day + timedelta(hours=hour)

            fmap = fractal_maps[hour % 4]
            f = ROTATING_EVENT_DESTINATIONS[fmap]
            out.append(Candidate(
                event="Fractal Incursion",
                track="Fractal Incursions",
                category="Core Tyria",
                start=start,
                end=start + timedelta(minutes=15),
                location=f"{fmap} · {f['place']}",
                waypoint=f["waypoint"],
                source="verified rotating schedule",
                wiki=ROTATING_EVENT_WIKI["Fractal Incursion"],
                base_priority=80,
                lightning=False,
                direct_waypoint=True,
                special=True,
            ))

            if hour % 2 == 1:
                s = ROTATING_EVENT_DESTINATIONS["Gendarran Fields"]
                out.append(Candidate(
                    event="Scarlet's Invasion",
                    track="Scarlet's Invasion",
                    category="Living World Season 1",
                    start=start,
                    end=start + timedelta(minutes=15),
                    location="Gendarran Fields · map-wide invasion",
                    waypoint=s["waypoint"],
                    source="verified rotating schedule",
                    wiki=ROTATING_EVENT_WIKI["Scarlet's Invasion"],
                    base_priority=84,
                    lightning=False,
                    direct_waypoint=True,
                    special=True,
                ))

            astart = start + timedelta(minutes=30)
            day_offset = (astart.weekday() * 3) % 7
            amap = awakened_maps[(day_offset + hour) % 7]
            a = ROTATING_EVENT_DESTINATIONS[amap]
            out.append(Candidate(
                event="Awakened Invasion",
                track="Awakened Invasion",
                category="Core Tyria",
                start=astart,
                end=astart + timedelta(minutes=15),
                location=f"{amap} · {a['place']}",
                waypoint=a["waypoint"],
                source="verified rotating schedule",
                wiki=ROTATING_EVENT_WIKI["Awakened Invasion"],
                base_priority=82,
                lightning=False,
                direct_waypoint=True,
                special=True,
            ))

    low = now - timedelta(minutes=30)
    high = now + timedelta(hours=4)
    return [c for c in out if c.end >= low and c.start <= high]


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


def apply_map_levels(cands: list[Candidate], maps: list[dict[str, Any]] | None) -> None:
    if not maps:
        return
    lookup: dict[str, dict[str, Any]] = {}
    for item in maps:
        name = norm(str(item.get("name", "")))
        if not name:
            continue
        old = lookup.get(name)
        if old is None or int(item.get("max_level", 0) or 0) > int(old.get("max_level", 0) or 0):
            lookup[name] = item

    for c in cands:
        match = None
        location_map = c.location.split("·", 1)[0].strip() if c.location else ""
        for label in (location_map, c.location, c.track, c.event):
            hit = lookup.get(norm(label))
            if hit:
                match = hit
                break
        if not match:
            continue
        max_level = int(match.get("max_level", 0) or 0)
        min_level = int(match.get("min_level", 0) or 0)
        if max_level > 0:
            c.level = max_level
            c.level_min = min_level if min_level > 0 else max_level


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


def add_community_signal(
    cands: list[Candidate],
    target: str,
    when: datetime,
    source: str,
    window: int = 120,
    direct_wp: str = "",
    announcement_url: str = ""
) -> None:
    cand = nearest_target(cands, target, when, window)
    if not cand:
        return

    # Strict rule: the bolt means an actual external community/event signal.
    cand.lightning = True

    if source not in cand.community_sources:
        cand.community_sources.append(source)

    if announcement_url and not cand.announcement_url:
        cand.announcement_url = announcement_url

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
                announcement_url = safe_https_url(
                    row.get("url", ""),
                    ANNOUNCEMENT_ALLOWED_HOSTS,
                ) or ANNOUNCEMENT_SOURCE_URLS.get(source_name, "")
                add_community_signal(
                    cands, target, start, source_name, 150,
                    announcement_url=announcement_url
                )

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
            add_community_signal(
                cands,
                "Evolved Jungle Wurm",
                gather + timedelta(minutes=45),
                "TT Wurm EU",
                20,
                announcement_url=ANNOUNCEMENT_SOURCE_URLS["TT Wurm EU"]
            )

    # DCAP runs are explicitly NA; do not promote them on an EU dashboard.
    if dcap and region == "NA":
        for item in dcap.get("events", []):
            hh, mm = map(int, item["time_utc"].split(":"))
            for delta in range(-1, 3):
                d = (now + timedelta(days=delta)).date()
                dt = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
                if weekday_match(item["rule"], dt):
                    add_community_signal(
                        cands, item["target"], dt, "DCAP", 30,
                        announcement_url=ANNOUNCEMENT_SOURCE_URLS["DCAP"]
                    )

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
                    if not c.announcement_url:
                        c.announcement_url = ANNOUNCEMENT_SOURCE_URLS["Choyareset"]
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


def action_window_minutes(c: Candidate) -> int:
    """Useful 'go there now' lifetime after start, based on event scope."""
    text = norm(c.event + " " + c.track)

    for terms, minutes in ACTION_WINDOW_OVERRIDES:
        if any(term in text for term in terms):
            return minutes

    scheduled = max(1.0, (c.end - c.start).total_seconds() / 60.0)
    if scheduled <= 15:
        return 5
    if scheduled <= 30:
        return 7
    return 10


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
            window = float(action_window_minutes(c))
            age = max(0.0, (now - c.start).total_seconds() / 60.0)
            freshness = max(0.0, 1.0 - age / max(1.0, window))
            score += 14.0 * freshness
        c.score = score


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    best: dict[str, Candidate] = {}
    for c in cands:
        k = c.key()
        old = best.get(k)
        if not old or c.score > old.score:
            best[k] = c
    return list(best.values())



def choose(
    cands: list[Candidate],
    cfg: dict[str, Any],
    now: datetime
) -> tuple[
    list[Candidate], list[Candidate],
    list[Candidate], list[Candidate],
    list[Candidate], list[Candidate]
]:
    cands = [c for c in cands if c.waypoint]
    score_candidates(cands, now)
    cands = dedupe(cands)

    prestart = timedelta(minutes=5)

    active = [
        c for c in cands
        if (c.start - prestart) <= now
        < min(c.end, c.start + timedelta(minutes=action_window_minutes(c)))
    ]

    upcoming_end = now + timedelta(minutes=int(cfg.get("upcoming_horizon_minutes", 180)))
    upcoming = [
        c for c in cands
        if (now + prestart) < c.start <= upcoming_end
    ]

    now_limit = int(cfg.get("now_limit", 3))
    next_limit = int(cfg.get("next_limit", 3))
    extra_limit = int(cfg.get("extra_limit", 2))

    active_top = sorted(active, key=lambda c: (-c.score, c.start))[:now_limit]
    upcoming_top = sorted(upcoming, key=lambda c: (-c.score, c.start))[:next_limit]

    active_top_keys = {c.key() for c in active_top}
    upcoming_top_keys = {c.key() for c in upcoming_top}

    active_rest = [c for c in active if c.key() not in active_top_keys]
    upcoming_rest = [c for c in upcoming if c.key() not in upcoming_top_keys]

    def pick_extras(rest: list[Candidate]) -> list[Candidate]:
        spotlight = sorted(
            [c for c in rest if c.special],
            key=lambda c: (c.start, -c.score)
        )
        picked = spotlight[:extra_limit]
        picked_keys = {c.key() for c in picked}
        if len(picked) < extra_limit:
            fallback = sorted(
                [c for c in rest if c.key() not in picked_keys],
                key=lambda c: (-c.score, c.start)
            )
            picked.extend(fallback[:extra_limit - len(picked)])
        return picked

    # NOW stays simpler: Top 3 are visible, every other currently valid
    # event goes directly into the expandable list.
    active_extra: list[Candidate] = []

    # NEXT keeps the compact places 4-5 spotlight.
    upcoming_extra = pick_extras(upcoming_rest)

    active_shown = active_top_keys
    upcoming_shown = upcoming_top_keys | {c.key() for c in upcoming_extra}

    active_more = sorted(
        [c for c in active if c.key() not in active_shown],
        key=lambda c: (c.start, -c.score, c.event)
    )
    upcoming_more = sorted(
        [c for c in upcoming if c.key() not in upcoming_shown],
        key=lambda c: (c.start, -c.score, c.event)
    )

    active_top.sort(key=lambda c: c.start)
    upcoming_top.sort(key=lambda c: c.start)
    active_extra.sort(key=lambda c: c.start)
    upcoming_extra.sort(key=lambda c: c.start)

    return (
        active_top, upcoming_top,
        active_extra, upcoming_extra,
        active_more, upcoming_more
    )


def heat_hue(value: float, low: float, high: float) -> int:
    if high <= low:
        return 60
    ratio = max(0.0, min(1.0, (value - low) / (high - low)))
    return int(round(120 - 120 * ratio))


def priority_badge(c: Candidate) -> str:
    score = int(round(c.score))
    hue = heat_hue(score, 65, 125)
    return f'<span class="badge heat" style="--h:{hue}" title="Higher = more likely to be active">Priority {score}</span>'


def level_badge(c: Candidate) -> str:
    if not c.level:
        return '<span class="badge level-na" title="Map level unavailable">Level n/a</span>'
    hue = heat_hue(c.level, 20, 80)
    if c.level_min and c.level_min != c.level:
        title = f'Map level {c.level_min}–{c.level}'
    else:
        title = f'Map level {c.level}'
    return f'<span class="badge heat" style="--h:{hue}" title="{html.escape(title)}">Level {c.level}</span>'



def client_event_pool(
    cands: list[Candidate],
    cfg: dict[str, Any],
    now: datetime,
) -> list[Candidate]:
    # Keep enough absolute-time data in the page for exact client-side boundary updates.
    horizon = int(cfg.get("upcoming_horizon_minutes", 120))
    deduped = dedupe([c for c in cands if c.waypoint])
    low = now - timedelta(minutes=20)
    high = now + timedelta(minutes=horizon + 15)

    out: list[Candidate] = []
    for c in deduped:
        active_until = min(c.end, c.start + timedelta(minutes=action_window_minutes(c)))
        if active_until < low:
            continue
        if c.start > high:
            continue
        out.append(c)

    return sorted(out, key=lambda c: (c.start, -c.score, c.event))


def client_event_payload(cands: list[Candidate]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in cands:
        active_from = c.start - timedelta(minutes=5)
        active_until = min(c.end, c.start + timedelta(minutes=action_window_minutes(c)))
        rows.append({
            "key": hashlib.sha256((c.key() + "|" + c.waypoint).encode("utf-8")).hexdigest()[:16],
            "event": c.event,
            "location": c.location,
            "start": iso(c.start),
            "active_from": iso(active_from),
            "active_until": iso(active_until),
            "score": round(float(c.score), 3),
            "special": bool(c.special),
            "waypoint": c.waypoint,
            "priority_html": priority_badge(c),
            "level_html": level_badge(c),
            "links_html": event_links(c),
            "flash_html": signal_flash(c),
        })
    return rows


def signal_flash(c: Candidate) -> str:
    if not c.lightning:
        return ""
    sources = ", ".join(c.community_sources)
    title = "Community event"
    if sources:
        title += f" · {sources}"
    return f'<span class="flash" title="{html.escape(title)}">⚡️</span>'


def info_link(c: Candidate) -> str:
    direct = safe_https_url(c.wiki, {"wiki.guildwars2.com"})
    if direct:
        url = direct
        title = "Event info"
    else:
        url = "https://wiki.guildwars2.com/index.php?search=" + urllib.parse.quote(c.event)
        title = "Find event info"
    return (
        f'<a class="wiki" href="{html.escape(url)}" target="_blank" '
        f'rel="noopener" title="{html.escape(title)}">Wiki</a>'
    )


def announcement_link(c: Candidate) -> str:
    url = safe_https_url(c.announcement_url, ANNOUNCEMENT_ALLOWED_HOSTS)
    if not url:
        return ""
    return (
        f'<a class="announcement" href="{html.escape(url)}" '
        f'target="_blank" rel="noopener" title="Event announcement">Announcement</a>'
    )


def event_links(c: Candidate) -> str:
    return info_link(c) + announcement_link(c)


def card_html(c: Candidate, tz: ZoneInfo, upcoming: bool) -> str:
    st = c.start.astimezone(tz)
    time_label = st.strftime("%H:%M")
    return f'''<article class="event-card">
      <div class="time">{time_label}</div>
      <div class="info">
        <div class="event-line"><span class="name">{signal_flash(c)}{html.escape(c.event)}</span><span class="inline-location"> · {html.escape(c.location)}</span></div>
        <div class="badges">{priority_badge(c)}{level_badge(c)}{event_links(c)}</div>
      </div>
      <button class="wp" data-copy="{html.escape(c.waypoint)}" title="Copy waypoint" aria-label="Copy waypoint for {html.escape(c.event)}">{html.escape(c.waypoint)}</button>
    </article>'''


def mini_card_html(c: Candidate, tz: ZoneInfo, upcoming: bool, rank: int) -> str:
    st = c.start.astimezone(tz)
    time_label = st.strftime("%H:%M")
    return f'''<div class="mini-card">
      <span class="rank">{rank}</span>
      <span class="mini-time">{time_label}</span>
      <div class="mini-info"><span class="mini-line">{signal_flash(c)}<b>{html.escape(c.event)}</b><span class="inline-location"> · {html.escape(c.location)}</span></span></div>
      <div class="mini-badges">{priority_badge(c)}{level_badge(c)}{event_links(c)}</div>
      <button class="wp mini-wp" data-copy="{html.escape(c.waypoint)}" title="Copy waypoint" aria-label="Copy waypoint for {html.escape(c.event)}">{html.escape(c.waypoint)}</button>
    </div>'''


def compact_action_html(c: Candidate, tz: ZoneInfo) -> str:
    st = c.start.astimezone(tz)
    return f'''<div class="all-card">
      <span class="all-time">{st.strftime("%H:%M")}</span>
      <div class="all-info"><span class="title-row">{signal_flash(c)}<b class="event-title">{html.escape(c.event)}</b><span class="inline-location"> · {html.escape(c.location)}</span></span></div>
      <div class="all-badges">{priority_badge(c)}{level_badge(c)}{event_links(c)}</div>
      <button class="wp all-wp" data-copy="{html.escape(c.waypoint)}" title="Copy waypoint" aria-label="Copy waypoint for {html.escape(c.event)}">{html.escape(c.waypoint)}</button>
    </div>'''


def page_version(
    active: list[Candidate], upcoming: list[Candidate],
    active_extra: list[Candidate], upcoming_extra: list[Candidate],
    active_more: list[Candidate], upcoming_more: list[Candidate],
    client_events: list[Candidate],
    notice: str = ""
) -> str:
    rows = [["notice", notice]]
    for group, items in (
        ("now", active), ("next", upcoming),
        ("now-extra", active_extra), ("next-extra", upcoming_extra),
        ("now-more", active_more), ("next-more", upcoming_more)
    ):
        for c in items:
            rows.append([
                group, c.event, iso(c.start), iso(c.end), c.location,
                c.waypoint, int(round(c.score)), c.level,
                c.lightning, c.special, c.announcement_url
            ])
    for c in client_events:
        rows.append([
            "client", c.event, iso(c.start), iso(c.end), c.location,
            c.waypoint, int(round(c.score)), c.level, c.lightning, c.special,
            c.announcement_url, action_window_minutes(c)
        ])
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def render_html(
    active: list[Candidate], upcoming: list[Candidate],
    active_extra: list[Candidate], upcoming_extra: list[Candidate],
    active_more: list[Candidate], upcoming_more: list[Candidate],
    client_events: list[Candidate],
    cfg: dict[str, Any], now: datetime,
    notice: str = ""
) -> str:
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    version = page_version(
        active, upcoming, active_extra, upcoming_extra,
        active_more, upcoming_more, client_events, notice
    )
    client_json = json.dumps(
        client_event_payload(client_events),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    def section(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return '<div class="empty">No events with a reliably resolved waypoint are currently available.</div>'
        return "\n".join(card_html(c, tz, is_upcoming) for c in items)

    def extras(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return ""
        rows = "\n".join(mini_card_html(c, tz, is_upcoming, 4 + i) for i, c in enumerate(items))
        return f'<div class="extra-block"><div class="extra-title">More Activity · Ranks 4–5</div>{rows}</div>'

    def expandable(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return ""
        label = "All Other Current Events" if not is_upcoming else "More Upcoming Activity · Next 2 Hours"
        rows = "\n".join(compact_action_html(c, tz) for c in items)
        return (
            f'<details class="all-block">'
            f'<summary>{html.escape(label)} <span>({len(items)})</span></summary>'
            f'<div class="all-list">{rows}</div>'
            f'</details>'
        )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="gw2-page-version" content="{version}">
<title>GW2 Action</title>
<style>
:root{{--bg:#0f1012;--panel:#17191d;--panel2:#1d2025;--line:#2b2f36;--text:#f3f4f6;--muted:#9aa1aa;--gold:#d7aa42;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}}
.app{{max-width:920px;margin:auto;padding:18px}}
header{{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 82%,rgba(15,16,18,0));padding:8px 0 12px;text-align:center}}
#clock{{font-size:23px;font-weight:800}}
.status-note{{display:inline-block;margin-top:7px;padding:5px 9px;border:1px solid #55492f;border-radius:999px;background:#1b1811;color:#d7bd7a;font-size:10px;font-weight:700}}
.tz-tools{{display:flex;justify-content:center;gap:4px;margin-top:6px}}
.tz-btn{{border:1px solid #30353d;background:#121419;color:#8f97a2;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:700;cursor:pointer}}
.tz-btn:hover{{color:#fff;border-color:#555e6b}}
.tz-btn.active{{color:#fff;background:#242932;border-color:#5d6673}}
h2{{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--gold);margin:16px 2px 8px}}
.event-card{{display:grid;grid-template-columns:82px 1fr 150px;align-items:center;gap:8px;min-height:62px;padding:8px 12px;margin:7px 0;background:var(--panel);border:1px solid var(--line);border-radius:12px}}
.event-card:hover{{background:var(--panel2)}}
.time{{font:800 15px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}}
.event-line,.mini-line,.title-row{{display:flex;align-items:baseline;min-width:0;white-space:nowrap;overflow:hidden}}
.name{{font-size:16px;font-weight:800;max-width:58%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 1 auto}}
.inline-location{{color:#aeb5bf;font-size:12px;font-weight:500;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto}}
.flash{{display:inline-block;margin-right:5px;line-height:1;vertical-align:-.08em;font-family:"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif;flex:0 0 auto}}
.badges{{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:5px}}
.badge{{display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:800;line-height:1;border:1px solid #3a4048}}
.badge.heat{{color:hsl(var(--h) 86% 76%);border-color:hsl(var(--h) 55% 38%);background:hsl(var(--h) 45% 16% / .8)}}
.level-na{{color:#a7adb6;background:#171a1f}}
.wiki,.announcement{{font-size:10px;color:#aab1bb;text-decoration:none;margin-left:2px}}
.wiki:hover,.announcement:hover{{text-decoration:underline;color:#fff}}
.announcement{{color:#d5b76f}}
.wp{{border:1px solid #424751;background:#101216;color:#fff;border-radius:9px;padding:10px 8px;cursor:pointer;font:800 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace}}
.wp:hover{{border-color:#69717e;background:#151820}}
.extra-block{{margin:8px 0 4px;padding:8px 10px;background:#131519;border:1px solid #242931;border-radius:10px}}
.extra-title{{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#7f8792;margin:0 0 5px 2px}}
.mini-card{{display:grid;grid-template-columns:24px 78px 1fr auto 128px;gap:7px;align-items:center;padding:5px 4px;border-top:1px solid #252a31;min-height:42px}}
.mini-card:first-of-type{{border-top:0}}
.rank{{font:800 11px ui-monospace,SFMono-Regular,Consolas,monospace;color:#7f8792;text-align:center}}
.mini-time{{font:800 11px ui-monospace,SFMono-Regular,Consolas,monospace}}
.mini-info{{min-width:0}}
.mini-info b{{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%;flex:0 1 auto}}
.mini-info .inline-location{{font-size:10px}}
.mini-badges{{display:flex;gap:4px;align-items:center;flex-wrap:wrap}}
.mini-badges .badge{{font-size:9px;padding:3px 5px}}
.mini-wp{{padding:8px 6px;font-size:10px}}
.all-block{{margin:8px 0 4px;background:#111317;border:1px solid #242931;border-radius:10px;overflow:hidden}}
.all-block summary{{cursor:pointer;padding:9px 12px;color:#aeb5bf;font-size:11px;font-weight:800;letter-spacing:.02em;user-select:none}}
.all-block summary:hover{{color:#fff;background:#16191e}}
.all-block summary span{{color:#737b86}}
.all-list{{padding:0 9px 8px}}
.all-card{{display:grid;grid-template-columns:62px minmax(180px,1fr) auto 128px;gap:8px;align-items:center;min-height:38px;padding:5px 4px;border-top:1px solid #252a31}}
.all-card:first-child{{border-top:0}}
.all-time{{font:800 11px ui-monospace,SFMono-Regular,Consolas,monospace}}
.all-info{{min-width:0}}
.all-info .event-title{{font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%;flex:0 1 auto}}
.all-info .inline-location{{font-size:10px}}
.all-badges{{display:flex;gap:4px;align-items:center;flex-wrap:wrap}}
.all-badges .badge{{font-size:9px;padding:3px 5px}}
.all-wp{{padding:8px 6px;font-size:10px}}
.empty{{padding:20px;text-align:center;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px}}
@media(max-width:760px){{
  .app{{padding:10px}}
  .event-card{{grid-template-columns:64px 1fr;grid-template-areas:'time info' 'wp wp';min-height:56px}}
  .time{{grid-area:time}}
  .info{{grid-area:info;min-width:0}}
  .wp{{grid-area:wp;width:100%}}
  .name{{max-width:52%}}
  .mini-card{{grid-template-columns:22px 58px 1fr}}
  .mini-badges{{grid-column:3}}
  .mini-wp{{grid-column:1/4;width:100%}}
  .all-card{{grid-template-columns:50px 1fr}}
  .all-badges{{grid-column:2}}
  .all-wp{{grid-column:1/3;width:100%}}
}}
</style>
</head>
<body>
<div class="app">
<header>
  <div id="clock"></div>
  <div id="time-zone-tools" class="tz-tools" hidden>
    <button type="button" class="tz-btn" data-tz-mode="local">Local</button>
    <button type="button" class="tz-btn" data-tz-mode="server">Server · UTC</button>
  </div>
  {f'<div class="status-note">{html.escape(notice)}</div>' if notice else ''}
</header>

<h2>Now · Highest Activity</h2>
<div id="now-top">{section(active, False)}</div>
<div id="now-more">{expandable(active_more, False)}</div>

<h2>Up Next · Highest Priority</h2>
<div id="next-top">{section(upcoming, True)}</div>
<div id="next-extra">{extras(upcoming_extra, True)}</div>
<div id="next-more">{expandable(upcoming_more, True)}</div>
</div>

<script>
const EVENT_DATA={client_json};
const UPCOMING_HORIZON_MS=120*60*1000;
const PRESTART_MS=5*60*1000;

let localZone="";
try {{ localZone=Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }} catch(e) {{}}
const utcLikeZones=new Set(["UTC","Etc/UTC","GMT","Etc/GMT"]);
const localDistinct=Boolean(localZone && !utcLikeZones.has(localZone));
let timeMode="server";
try {{
  const saved=localStorage.getItem("gw2action-time-mode");
  if(saved==="local" && localDistinct) timeMode="local";
  else if(saved==="server") timeMode="server";
  else if(localDistinct) timeMode="local";
}} catch(e) {{ if(localDistinct) timeMode="local"; }}

function selectedZone() {{ return timeMode==="local" && localDistinct ? localZone : "UTC"; }}
function saveTimeMode() {{ try {{ localStorage.setItem("gw2action-time-mode",timeMode); }} catch(e) {{}} }}

function updateTimeZoneControls() {{
  const tools=document.getElementById("time-zone-tools");
  if(!tools) return;
  if(localDistinct) {{
    tools.hidden=false;
    const localButton=tools.querySelector('[data-tz-mode="local"]');
    if(localButton) localButton.title=`Local time · ${{localZone}}`;
  }} else {{
    tools.hidden=true;
  }}
  tools.querySelectorAll(".tz-btn").forEach(btn=>{{
    const active=btn.dataset.tzMode===timeMode;
    btn.classList.toggle("active",active);
    btn.setAttribute("aria-pressed",active?"true":"false");
  }});
}}

function clockFormatter() {{
  return new Intl.DateTimeFormat("en-GB",{{timeZone:selectedZone(),weekday:"long",day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}});
}}
function eventTimeFormatter() {{
  return new Intl.DateTimeFormat("en-GB",{{timeZone:selectedZone(),hour:"2-digit",minute:"2-digit",hourCycle:"h23"}});
}}
let clockFmt=clockFormatter();
let eventTimeFmt=eventTimeFormatter();
function formatEventTime(iso) {{ return eventTimeFmt.format(new Date(iso)); }}
function tickClock() {{ const el=document.getElementById("clock"); if(el) el.textContent=clockFmt.format(new Date()); }}

function esc(value) {{
  return String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('\"',"&quot;").replaceAll("'","&#39;");
}}
function topCard(e) {{
  return `<article class="event-card"><div class="time">${{formatEventTime(e.start)}}</div><div class="info"><div class="event-line"><span class="name">${{e.flash_html}}${{esc(e.event)}}</span><span class="inline-location"> · ${{esc(e.location)}}</span></div><div class="badges">${{e.priority_html}}${{e.level_html}}${{e.links_html}}</div></div><button class="wp" data-copy="${{esc(e.waypoint)}}" title="Copy waypoint" aria-label="Copy waypoint for ${{esc(e.event)}}">${{esc(e.waypoint)}}</button></article>`;
}}
function miniCard(e,rank) {{
  return `<div class="mini-card"><span class="rank">${{rank}}</span><span class="mini-time">${{formatEventTime(e.start)}}</span><div class="mini-info"><span class="mini-line">${{e.flash_html}}<b>${{esc(e.event)}}</b><span class="inline-location"> · ${{esc(e.location)}}</span></span></div><div class="mini-badges">${{e.priority_html}}${{e.level_html}}${{e.links_html}}</div><button class="wp mini-wp" data-copy="${{esc(e.waypoint)}}" title="Copy waypoint" aria-label="Copy waypoint for ${{esc(e.event)}}">${{esc(e.waypoint)}}</button></div>`;
}}
function compactCard(e) {{
  return `<div class="all-card"><span class="all-time">${{formatEventTime(e.start)}}</span><div class="all-info"><span class="title-row">${{e.flash_html}}<b class="event-title">${{esc(e.event)}}</b><span class="inline-location"> · ${{esc(e.location)}}</span></span></div><div class="all-badges">${{e.priority_html}}${{e.level_html}}${{e.links_html}}</div><button class="wp all-wp" data-copy="${{esc(e.waypoint)}}" title="Copy waypoint" aria-label="Copy waypoint for ${{esc(e.event)}}">${{esc(e.waypoint)}}</button></div>`;
}}

function byScore(a,b) {{ return (b.score-a.score)||(Date.parse(a.start)-Date.parse(b.start))||a.event.localeCompare(b.event); }}
function byTime(a,b) {{ return (Date.parse(a.start)-Date.parse(b.start))||(b.score-a.score)||a.event.localeCompare(b.event); }}
function pickUpcomingExtras(rest) {{
  const spotlight=rest.filter(e=>e.special).sort(byTime);
  const picked=spotlight.slice(0,2);
  const keys=new Set(picked.map(e=>e.key));
  if(picked.length<2) {{
    const fallback=rest.filter(e=>!keys.has(e.key)).sort(byScore);
    picked.push(...fallback.slice(0,2-picked.length));
  }}
  return picked;
}}
function layoutAt(nowMs) {{
  const active=EVENT_DATA.filter(e=>Date.parse(e.active_from)<=nowMs && nowMs<Date.parse(e.active_until));
  const upcoming=EVENT_DATA.filter(e=>{{const start=Date.parse(e.start);return (nowMs+PRESTART_MS)<start && start<=(nowMs+UPCOMING_HORIZON_MS);}});
  const activeTop=[...active].sort(byScore).slice(0,3).sort(byTime);
  const activeTopKeys=new Set(activeTop.map(e=>e.key));
  const activeMore=active.filter(e=>!activeTopKeys.has(e.key)).sort(byTime);
  const upcomingTop=[...upcoming].sort(byScore).slice(0,3).sort(byTime);
  const upcomingTopKeys=new Set(upcomingTop.map(e=>e.key));
  const upcomingRest=upcoming.filter(e=>!upcomingTopKeys.has(e.key));
  const upcomingExtra=pickUpcomingExtras(upcomingRest).sort(byTime);
  const extraKeys=new Set(upcomingExtra.map(e=>e.key));
  const upcomingMore=upcomingRest.filter(e=>!extraKeys.has(e.key)).sort(byTime);
  return {{activeTop,activeMore,upcomingTop,upcomingExtra,upcomingMore}};
}}
function emptyBlock() {{ return '<div class="empty">No events with a reliably resolved waypoint are currently available.</div>'; }}
function detailsBlock(label,items,wasOpen) {{
  if(!items.length) return "";
  return `<details class="all-block" ${{wasOpen?"open":""}}><summary>${{label}} <span>(${{items.length}})</span></summary><div class="all-list">${{items.map(compactCard).join("")}}</div></details>`;
}}
let lastLayoutSignature="";
function renderLive(force=false) {{
  const nowMoreOpen=document.querySelector("#now-more details")?.open || false;
  const nextMoreOpen=document.querySelector("#next-more details")?.open || false;
  const groups=layoutAt(Date.now());
  const signature=JSON.stringify([groups.activeTop.map(e=>e.key),groups.activeMore.map(e=>e.key),groups.upcomingTop.map(e=>e.key),groups.upcomingExtra.map(e=>e.key),groups.upcomingMore.map(e=>e.key),timeMode]);
  if(!force && signature===lastLayoutSignature) return;
  lastLayoutSignature=signature;
  document.getElementById("now-top").innerHTML=groups.activeTop.length?groups.activeTop.map(topCard).join(""):emptyBlock();
  document.getElementById("now-more").innerHTML=detailsBlock("All Other Current Events",groups.activeMore,nowMoreOpen);
  document.getElementById("next-top").innerHTML=groups.upcomingTop.length?groups.upcomingTop.map(topCard).join(""):emptyBlock();
  document.getElementById("next-extra").innerHTML=groups.upcomingExtra.length?`<div class="extra-block"><div class="extra-title">More Activity · Ranks 4–5</div>${{groups.upcomingExtra.map((e,i)=>miniCard(e,4+i)).join("")}}</div>`:"";
  document.getElementById("next-more").innerHTML=detailsBlock("More Upcoming Activity · Next 2 Hours",groups.upcomingMore,nextMoreOpen);
}}

document.getElementById("time-zone-tools")?.addEventListener("click",ev=>{{
  const btn=ev.target.closest(".tz-btn");
  if(!btn) return;
  const mode=btn.dataset.tzMode;
  if(mode==="local" && !localDistinct) return;
  if(mode!=="local" && mode!=="server") return;
  timeMode=mode;saveTimeMode();clockFmt=clockFormatter();eventTimeFmt=eventTimeFormatter();updateTimeZoneControls();tickClock();renderLive(true);
}});

document.querySelector(".app")?.addEventListener("click",async ev=>{{
  const btn=ev.target.closest(".wp");
  if(!btn) return;
  const value=btn.dataset.copy;
  try {{await navigator.clipboard.writeText(value);const old=btn.textContent;btn.textContent=`✓ ${{value}}`;setTimeout(()=>{{btn.textContent=old;}},900);}} catch(e) {{}}
}});

updateTimeZoneControls();tickClock();renderLive(true);setInterval(tickClock,1000);
// Exact local category transition, independent of Pages publication latency.
setInterval(()=>renderLive(false),2000);

if(window.location.search){{history.replaceState(null,"",window.location.pathname+window.location.hash);}}
const currentVersion=document.querySelector('meta[name="gw2-page-version"]').content;
async function checkForUpdate(){{
  try{{
    const u=new URL(window.location.pathname,window.location.origin);u.searchParams.set("_",Date.now().toString());
    const r=await fetch(u.toString(),{{cache:"no-store"}});if(!r.ok)return;
    const text=await r.text();const match=text.match(/<meta name="gw2-page-version" content="([^"]+)">/);
    if(match && match[1]!==currentVersion){{const next=new URL(window.location.pathname,window.location.origin);next.searchParams.set("_",Date.now().toString());window.location.replace(next.toString());}}
  }}catch(e){{}}
}}
setInterval(checkForUpdate,20000);
</script>
</body>
</html>'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", help="Test time as ISO-8601 UTC/local")
    ap.add_argument("--no-network", action="store_true", help="Use committed cache only")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    raw_state = load_json(STATE_PATH, {"version": CACHE_SCHEMA_VERSION, "sources": {}})
    state, cache_state_updated = normalize_state(raw_state)

    now = parse_iso(args.now) if args.now else now_utc()
    if not now:
        raise SystemExit("Invalid --now value")

    ttl = cfg.get("source_ttls_minutes", {})
    errors: dict[str, str] = {}

    parsers = {
        "maps": parse_maps,
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
        "news": parse_news,
    }

    values: dict[str, Any] = {}
    for name, parser in parsers.items():
        if args.no_network:
            values[name] = state["sources"].get(name, {}).get("data")
            if values[name] is None:
                errors[name] = "cache missing"
            continue

        data, changed, err = refresh_cached(
            state,
            name,
            int(ttl.get(name, 60)),
            parser,
            now,
        )
        values[name] = data
        cache_state_updated = cache_state_updated or changed
        if err:
            errors[name] = err

    catalog = values.get("catalog") or []
    if not catalog:
        if cache_state_updated or not STATE_PATH.exists():
            atomic_write(
                STATE_PATH,
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        print("FATAL: no event catalog available", file=sys.stderr)
        return 2

    cands = catalog_candidates(catalog, cfg, now)
    cands.extend(verified_rotating_event_candidates(now))
    apply_map_levels(cands, values.get("maps"))
    apply_ninja_waypoints(cands, values.get("ninja"))
    apply_community(
        cands,
        now,
        cfg.get("region", "EU"),
        values.get("metasheet"),
        values.get("hardstuck"),
        values.get("ttwurm"),
        values.get("dcap"),
        values.get("gw2community"),
        values.get("vip"),
        values.get("choya"),
    )
    apply_fast_context(cands, values.get("fast"))

    active, upcoming, active_extra, upcoming_extra, active_more, upcoming_more = choose(
        cands, cfg, now
    )
    client_events = client_event_pool(cands, cfg, now)

    notice = user_status_notice(state, ttl, now)

    page = render_html(
        active,
        upcoming,
        active_extra,
        upcoming_extra,
        active_more,
        upcoming_more,
        client_events,
        cfg,
        now,
        notice,
    )

    old_page = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
    page_changed = page != old_page
    if page_changed:
        atomic_write(INDEX_PATH, page)

    if cache_state_updated or not STATE_PATH.exists():
        atomic_write(
            STATE_PATH,
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    print(
        f"candidates={len(cands)} active={len(active)} upcoming={len(upcoming)} "
        f"active_extra={len(active_extra)} upcoming_extra={len(upcoming_extra)} "
        f"active_more={len(active_more)} upcoming_more={len(upcoming_more)} "
        f"page_changed={page_changed} cache_state_updated={cache_state_updated}"
    )

    if notice:
        print(f"User status: {notice}")

    if errors:
        print("Source warnings:")
        for name, message in errors.items():
            print(f"  {name}: {message}")

    for label, items in [
        ("NOW", active),
        ("NOW+", active_extra),
        ("NOW-ALL", active_more),
        ("NEXT", upcoming),
        ("NEXT+", upcoming_extra),
        ("NEXT-ALL", upcoming_more),
    ]:
        for c in items:
            print(
                f"{label} {c.start.isoformat()} {c.event} {c.location} "
                f"{c.waypoint} score={c.score:.1f} level={c.level} "
                f"lightning={c.lightning} special={c.special}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
