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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

WORKER_ROOT = Path(__file__).resolve().parents[1]
ENGINE_VERSION = "2.2.5"
REPO_ROOT = WORKER_ROOT.parent
CONFIG_PATH = WORKER_ROOT / "config" / "settings.json"
STATE_PATH = WORKER_ROOT / "data" / "cache.json"
INDEX_PATH = WORKER_ROOT / "index.html"

URLS = {
    "waypoints": "https://api.guildwars2.com/v2/continents/1/floors/1?lang=en",
    "waypoints_desert": "https://api.guildwars2.com/v2/continents/1/floors/49/regions/12?lang=en",
    "event_levels": "https://api.guildwars2.com/v1/event_details.json?lang=en",
    "maps": "https://api.guildwars2.com/v2/maps?ids=all&lang=en",
    "catalog": "https://raw.githubusercontent.com/giovazz89/gw2-api-event-timers/main/events.json",
    "ninja": "https://gw2.ninja/timer",
    "metasheet": "https://docs.google.com/spreadsheets/d/1I2501rbqKjAD6HXQOtHAPQjLdqeS2tQIc9kEwyXuDKo/export?format=csv&gid=1233590379",
    "hardstuck": "https://hardstuck.gg/events/",
    "ttwurm": "https://sites.google.com/view/ttwurm/calendar",
    "dcap": "https://wiki.guildwars2.com/wiki/User:DCAP",
    "gw2community": "https://gw2community.de/calendar/calendar-feed/",
    "vip": "https://gw2vip.net/",
    "choya": "https://choyaaa.com/meta",
    "fast": "https://fast.farming-community.eu/open-world/meta",
    "news": "https://www.guildwars2.com/en/feed/"
}

COMMUNITY_INFO_URLS = {
    "Meta-Train": "https://docs.google.com/spreadsheets/d/1I2501rbqKjAD6HXQOtHAPQjLdqeS2tQIc9kEwyXuDKo/edit",
    "Hardstuck": "https://hardstuck.gg/events/",
    "GW2Community": "https://gw2community.de/calendar/",
    "ViP": "https://guildwarsvip.com/",
    "TT Wurm EU": "https://sites.google.com/view/ttwurm/calendar",
    "DCAP": "https://wiki.guildwars2.com/wiki/User:DCAP",
    "Choyareset": "https://choyaaa.com/meta",
}

CREDIT_GROUPS = (
    ("Game data & event reference", (
        ("ArenaNet · Guild Wars 2 API", "https://wiki.guildwars2.com/wiki/API:Main",
         "Map levels, event details and waypoint data"),
        ("GW2 API Event Timers · giovazz89", "https://github.com/giovazz89/gw2-api-event-timers",
         "Recurring event schedules"),
        ("Guild Wars 2 Wiki contributors", "https://wiki.guildwars2.com/wiki/Main_Page",
         "Map and event guides"),
        ("GW2 Ninja", "https://gw2.ninja/timer", "Timer and waypoint references"),
        ("Guild Wars 2 News", "https://www.guildwars2.com/en/news/",
         "Official announcements"),
    )),
    ("Community plans & farming", (
        ("Meta-Train schedule", COMMUNITY_INFO_URLS["Meta-Train"], "Community run times"),
        ("Hardstuck", COMMUNITY_INFO_URLS["Hardstuck"], "Community events and guides"),
        ("GW2Community.de", COMMUNITY_INFO_URLS["GW2Community"], "Community calendar"),
        ("ViP", COMMUNITY_INFO_URLS["ViP"], "Community calendar"),
        ("Triple Trouble Wurm EU", COMMUNITY_INFO_URLS["TT Wurm EU"], "Organized wurm runs"),
        ("DCAP", COMMUNITY_INFO_URLS["DCAP"], "NA community runs"),
        ("Choyareset", COMMUNITY_INFO_URLS["Choyareset"], "Meta train route"),
        ("[fast] Farming Community", URLS["fast"], "Farming context"),
    )),
)

SOURCE_REGIONS = {"metasheet": "EU", "ttwurm": "EU", "gw2community": "EU", "dcap": "NA"}


CACHE_SCHEMA_VERSION = 2
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

SOURCE_DATA_VERSIONS = {
    "maps": 2,
    "catalog": 2,
    "waypoints": 1,
    "waypoints_desert": 1,
    "event_levels": 1,
    "ninja": 2,
    "metasheet": 1,
    "hardstuck": 1,
    "ttwurm": 1,
    "dcap": 1,
    "gw2community": 2,
    "vip": 1,
    "choya": 2,
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
    "hardstuck.gg",
    "www.hardstuck.gg",
    "gw2community.de",
    "www.gw2community.de",
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
    "Battle For Lion's Arch (Public)": "Eye of the North",
    "Convergences (Public)": "The Wizard's Tower",
    "Target Practice": "The Wizard's Tower",
    "Fly by Night": "The Wizard's Tower"
}

TRACK_DISPLAY_OVERRIDES = {
    "Dragon's Stand": "Dragon's Stand",
    "Convergences": "Convergence: Outer Nayos"
}

CONTENT_GROUPS = (
    {"id": "core", "short": "Core", "label": "Core Tyria", "category": "Core Tyria", "hue": 210},
    {"id": "lw1", "short": "LW1", "label": "Living World Season 1", "category": "Living World Season 1", "hue": 340},
    {"id": "lw2", "short": "LW2", "label": "Living World Season 2", "category": "Living World Season 2", "hue": 325},
    {"id": "hot", "short": "HoT", "label": "Heart of Thorns", "category": "Heart of Thorns", "hue": 105},
    {"id": "lw3", "short": "LW3", "label": "Living World Season 3", "category": "Living World Season 3", "hue": 300},
    {"id": "pof", "short": "PoF", "label": "Path of Fire", "category": "Path of Fire", "hue": 28},
    {"id": "lw4", "short": "LW4", "label": "Living World Season 4", "category": "Living World Season 4", "hue": 282},
    {"id": "ibs", "short": "IBS", "label": "The Icebrood Saga", "category": "The Icebrood Saga", "hue": 202},
    {"id": "eod", "short": "EoD", "label": "End of Dragons", "category": "End of Dragons", "hue": 174},
    {"id": "soto", "short": "SotO", "label": "Secrets of the Obscure", "category": "Secrets of the Obscure", "hue": 43},
    {"id": "jw", "short": "JW", "label": "Janthir Wilds", "category": "Janthir Wilds", "hue": 220},
    {"id": "voe", "short": "VoE", "label": "Visions of Eternity", "category": "Visions of Eternity", "hue": 14},
    {"id": "special", "short": "Special", "label": "Special Events", "category": "Special Events", "hue": 55,
     "hint": "Festivals and limited-time activities appear here when scheduled."},
)
CONTENT_UNKNOWN = {
    "id": "unknown",
    "short": "Unknown",
    "label": "Unknown event, please contact the developer",
    "category": "",
    "hue": 215,
}
CONTENT_BY_CATEGORY = {item["category"]: item for item in CONTENT_GROUPS}
CONTENT_OPTIONS = CONTENT_GROUPS

# Untimed farm opportunities. These are directions for checking an active
# instance via the in-game LFG, never assertions that a spawn is live.
FARM_MAPS = (
    ("The Silverwastes · RIBA", "The Silverwastes", "Living World Season 2",
     "Camp Resolve Waypoint", "[&BH8HAAA=]", "The_Silverwastes",
     "https://hardstuck.gg/gw2/guides/events/the-silverwastes-guide-to-riba/",
     "RIBA guide by Hardstuck"),
    ("Dragonfall · Meta train", "Dragonfall", "Living World Season 4",
     "Pact Command Waypoint", "[&BN4LAAA=]", "Dragonfall",
     "https://fast.farming-community.eu/open-world/farmtrain",
     "Farmtrain overview by [fast]"),
    ("Drizzlewood Coast · Meta train", "Drizzlewood Coast", "The Icebrood Saga",
     "Base Camp Waypoint", "[&BGQMAAA=]", "Drizzlewood_Coast",
     "https://fast.farming-community.eu/open-world/farmtrain",
     "Farmtrain overview by [fast]"),
)

CATALOG_TRACKS_IGNORED = {
    "Day and night", "Cantha: Day and night", "PvP Tournaments"
}
CATALOG_TRACKS_REPLACED_BY_VERIFIED_SCHEDULE = {
    "Scarlet's Invasion"
}
EXCLUDED_EVENTS = {"Reset"}
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
    "Escorts": -8,
    "Target Practice": -65,
    "Fly by Night": -65
}

# Identities, content origin and entry maps verified against the GW2 Wiki,
# 2026-09-26. Instance type and level scaling are deliberately independent.
PUBLIC_INSTANCES = (
    ("The Twisted Marionette", "Living World Season 1", "Eye of the North", False),
    ("The Tower of Nightmares", "Living World Season 1", "Eye of the North", False),
    ("The Battle For Lion's Arch", "Living World Season 1", "Eye of the North", False),
    ("Dragonstorm", "The Icebrood Saga", "Eye of the North", False),
    ("Convergence: Outer Nayos", "Secrets of the Obscure", "The Wizard's Tower", False),
    ("Convergence: Mount Balrior", "Janthir Wilds", "Lowland Shore · Harvest Den", False),
    ("Convergence: Nexus of Eternity", "Visions of Eternity", "Leyspring Hollows · Rooted Sanctuary", False),
    ("Dragon Arena", "Special Events", "Hoelbrak · Lake Mourn", True),
)


def instance_identity(value: str) -> str:
    value = re.sub(r"\s*\((?:public|private|challenge mode)\)\s*$", "", value.strip(), flags=re.I)
    return re.sub(r"^the ", "", norm(value))


def public_instance_meta(c: "Candidate") -> tuple[str, str, str, bool] | None:
    # A general article link does not prove that the timed segment itself is
    # that public instance. Only the event or corroborating timer track does.
    labels = {instance_identity(c.event)}
    # A generic track is only useful with its content category; never treat
    # every unrelated occurrence of the word 'convergence' as this content.
    if norm(c.track) == "convergences" and content_meta(c.category)["id"] == "soto":
        labels.add(instance_identity("Convergence: Outer Nayos"))
    for item in PUBLIC_INSTANCES:
        if instance_identity(item[0]) in labels:
            return item
    # A phase can carry the instance identity in its track. Mount Balrior
    # alone is ambiguous (also a raid), so do not infer it from that name.
    track = instance_identity(c.track)
    for item in PUBLIC_INSTANCES:
        if track == instance_identity(item[0]) and (
            item[0].startswith("Convergence:") or track in {
                "dragonstorm", "twisted marionette", "tower of nightmares",
                "battle for lion s arch", "dragon arena",
            }
        ):
            return item
    return None


def is_known_level80_public_instance(c: "Candidate") -> bool:
    meta = public_instance_meta(c)
    return bool(meta and not meta[3])


def is_structured_convergence(c: "Candidate") -> bool:
    """Forward-compatible, corroborated identity; not a free substring match."""
    title = urllib.parse.unquote(urllib.parse.urlsplit(c.wiki).path).removeprefix("/wiki/").replace("_", " ")
    named = bool(re.match(r"^Convergence\s*:\s*\S", c.event, re.I))
    wiki_match = named and norm(c.event) == norm(title)
    track_match = norm(c.track) in {"convergence", "convergences"}
    content_id = content_meta(c.category)["id"]
    return named and (wiki_match or track_match) and (
        content_id in {"soto", "jw", "voe"} or norm(c.category) == "public instances"
    )


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
    "convergence outer nayos": "Convergence: Outer Nayos",
    "outer nayos": "Convergence: Outer Nayos",
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
    waypoint_name: str = ""
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
    event_id: str = ""
    public_instance: bool = False
    upscaled: bool = False
    level_kind: str = "map"

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


def specific_announcement_url(source: str, value: str) -> str:
    """Only link to the dated event page, never to a general calendar."""
    url = safe_https_url(value, ANNOUNCEMENT_ALLOWED_HOSTS)
    parsed = urllib.parse.urlsplit(url) if url else None
    host = parsed.hostname if parsed else ""
    path = parsed.path if parsed else ""
    if (source == "Hardstuck" and host in {"hardstuck.gg", "www.hardstuck.gg"}
            and path.startswith("/events/") and path.strip("/") != "events"):
        return url
    if (source == "GW2Community" and host in {"gw2community.de", "www.gw2community.de"}
            and path.startswith("/calendar/event/") and path.strip("/") != "calendar/event"):
        return url
    return ""


def fetch_text(url: str, timeout: int = 15, max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"gw2action/{ENGINE_VERSION} (+GitHub Actions; static community dashboard)",
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
    if name in {"waypoints", "waypoints_desert", "event_levels"}:
        return len(data)

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
        if isinstance(exc, urllib.error.HTTPError) and exc.code in {429, 503}:
            retry = exc.headers.get("Retry-After", "") if exc.headers else ""
            try:
                retry_at = now + timedelta(seconds=max(0, int(retry))) if retry.isdigit() else parsedate_to_datetime(retry)
                retry_at = retry_at.astimezone(timezone.utc)
                if retry_at > parse_iso(entry["retry_after"]):
                    entry["retry_after"] = iso(retry_at)
            except (ValueError, TypeError, OverflowError):
                pass
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
    region: str = "EU",
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
        if SOURCE_REGIONS.get(name, region) != region:
            continue
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
            "id": item.get("id"),
            "name": item.get("name", ""),
            "min_level": int(item.get("min_level", 0) or 0),
            "max_level": int(item.get("max_level", 0) or 0),
            "type": item.get("type", "")
        })
    if len(out) < 50:
        raise ValueError(f"maps unexpectedly small: {len(out)}")
    return out


def parse_waypoints(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    regions = data.get("regions", {"region": data})
    out = {}
    for region in regions.values():
        for map_id, area in region.get("maps", {}).items():
            for poi in area.get("points_of_interest", {}).values():
                if poi.get("type") != "waypoint" or not poi.get("name"):
                    continue
                code = poi.get("chat_link", "").strip()
                if not re.fullmatch(r"\[&[A-Za-z0-9+/=]+\]", code):
                    continue
                out[code] = {"name": poi["name"], "map": area["name"],
                             "map_id": int(map_id), "min_level": area.get("min_level", 0),
                             "max_level": area.get("max_level", 0)}
    if not out:
        raise ValueError("no official waypoint records")
    return out


def parse_event_levels(raw: str) -> dict[str, Any]:
    events = json.loads(raw).get("events", {})
    out = {key: {"level": value["level"], "map_id": value["map_id"]}
           for key, value in events.items()
           if isinstance(value, dict) and isinstance(value.get("level"), int)
           and 1 <= value["level"] <= 80 and isinstance(value.get("map_id"), int)}
    if len(out) < 100:
        raise ValueError("event metadata unexpectedly small")
    return out


def is_special_recurring(event: str, track: str) -> bool:
    text = norm(event + " " + track)
    # A title about a convergence is not itself a timed convergence.
    if re.match(r"(?i)^Convergence:\s*\S", event) or norm(track) == "convergences":
        return True
    return any(re.search(r"\b" + re.escape(term) + r"\b", text)
               for term in SPECIAL_RECURRING_TERMS if term != "convergence")


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
                "chatlink": str(seg.get("chatlink", "")).strip(),
                "event_id": seg.get("v1_event_id", ""),
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
        name = json.loads('"' + m.group("name") + '"')
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


def parse_ical_dt(value: str, tzid: str = "UTC") -> datetime | None:
    value = value.strip()
    try:
        local_tz = ZoneInfo(tzid)
    except (ValueError, KeyError):
        return None
    for fmt, tz in [("%Y%m%dT%H%M%SZ", timezone.utc), ("%Y%m%dT%H%M%S", local_tz), ("%Y%m%dT%H%M", local_tz)]:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    return None


def parse_gw2community(raw: str) -> list[dict[str, Any]]:
    if "<rss" in raw[:500]:
        # The public feed expands recurring appointments. pubDate is the
        # posting date, NOT the occurrence time; read the explicit title date.
        root = ET.fromstring(raw)
        months = {name: i for i, name in enumerate(("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"), 1)}
        out = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title", "")
            match = re.search(r"\((?:\w+), (\d{1,2})\. (\w+) (\d{4}), (\d{2}):(\d{2})(?:[–-](\d{2}):(\d{2}))?\)$", title)
            if not match or match[2] not in months:
                continue
            start = datetime(int(match[3]), months[match[2]], int(match[1]), int(match[4]), int(match[5]), tzinfo=ZoneInfo("Europe/Berlin"))
            end = start.replace(hour=int(match[6]), minute=int(match[7])) if match[6] else start + timedelta(hours=2)
            if end <= start:
                end += timedelta(days=1)
            out.append({"title": title[:match.start()].strip(), "start": iso(start), "end": iso(end), "url": item.findtext("link", ""), "region": "EU"})
        if root.findall("./channel/item") and not out:
            raise ValueError("calendar feed contains no readable occurrence dates")
        return out
    if "BEGIN:VCALENDAR" not in raw:
        raise ValueError("expected calendar RSS or iCalendar data")
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
        zones: dict[str, str] = {}
        for line in block:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            props[k.split(";", 1)[0]] = v
            zone = re.search(r'(?:^|;)TZID="?([^;"\r\n]+)', k)
            if zone:
                zones[k.split(";", 1)[0]] = zone[1]
        if props.get("STATUS") == "CANCELLED":
            continue
        start = parse_ical_dt(props.get("DTSTART", ""), zones.get("DTSTART", "UTC"))
        end = parse_ical_dt(props.get("DTEND", ""), zones.get("DTEND", "UTC"))
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
        # The calendar's display timezone does not establish the game region.
        # Respect a region only when the individual title says so explicitly.
        eu = bool(re.search(r"\bEU\b", title, re.I))
        na = bool(re.search(r"\bNA\b", title, re.I))
        row_region = "EU" if eu and not na else "NA" if na and not eu else ""
        out.append({"title": title, "start": iso(start), "end": iso(end), "region": row_region})
    return out


def parse_choya(raw: str) -> dict[str, Any]:
    aliases = {
        "bonk tequatl": "Tequatl the Sunless", "bonk naked man": "Ley-Line Anomaly",
        "eat chak gerent": "Chak Gerent", "save gold city from evil plant": "Octovine",
        "slurp the ooze": "Ooze Pits", "shackles of the choya": "Shackles of the Ancients",
        "depths of choya": "Depths of Cruelty", "bearvergence": "Convergence: Mount Balrior",
        "beamvergence": "Convergence: Nexus of Eternity", "chovergence": "Convergence: Outer Nayos",
        "choyastorm": "Dragonstorm", "choyakkar": "Drakkar and Spirits of the Wild",
    }
    out = []
    for block in re.split(r'<div[^>]+class="stop-card"', raw)[1:]:
        title = re.search(r'class="stop-boss"[^>]*>(.*?)</span>', block, re.S)
        utc = re.search(r'data-utc="(\d{2}:\d{2})"', block)
        code = re.search(r'data-code="([^"<>]+)"', block)
        if not title or not utc or not code:
            continue
        name = norm(strip_tags(title[1]))
        canonical = aliases.get(name) or ALIASES.get(name)
        if canonical:
            out.append({"title": canonical, "time_utc": utc[1], "waypoint": html.unescape(code[1])})
    if not out:
        raise ValueError("Choyareset route has no explicit timed stops")
    # Do not infer a game region from the displayed timezone.
    text = norm(strip_tags(raw))
    na = bool(re.search(r"\b(?:north america|north american|na servers?|na region)\b", text))
    eu = bool(re.search(r"\b(?:european servers?|eu servers?|eu region)\b", text))
    region = "NA" if na and not eu else "EU" if eu and not na else ""
    return {"route": out, "region": region}


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
        if re.search(r"(?i)\b(?:bonus event|rush event|fractal incursion|world boss|meta-event|convergences?)\b", line):
            lines.append(line)
    return {"lines": lines[:100]}


def segment_location(track_name: str, event_name: str) -> str:
    if event_name in LOCATION_OVERRIDES:
        return LOCATION_OVERRIDES[event_name]
    if track_name in {"Ley-Line Anomaly", "Dragon Bash"}:
        return event_name
    return track_name


def display_name(track_name: str, event_name: str) -> str:
    if track_name == "Ley-Line Anomaly":
        return "Ley-Line Anomaly"
    if track_name == "Dragon Bash":
        return "Hologram Stampede"
    if track_name in TRACK_DISPLAY_OVERRIDES:
        return TRACK_DISPLAY_OVERRIDES[track_name]
    return event_name


def base_priority(cfg: dict[str, Any], category: str, track: str, event: str, rewards: dict[str, Any], lfg: int) -> int:
    p = int(cfg.get("category_priority", {}).get(category, 40))
    override = cfg.get("priority_overrides", {})
    p = max(p, int(override.get(track, 0) or 0), int(override.get(event, 0) or 0))
    p += PHASE_PENALTIES.get(event, 0)
    # Catalog rewards are opportunities, not a measured gold/hour rate or
    # account-specific unearned achievement points. Catalog LFG is not live.
    if rewards.get("random_items"):
        p += 2
    if rewards.get("achievements"):
        p += 1
    if lfg:
        p += min(3, max(0, int(lfg)) // 4)
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
            if not ev or ev in EXCLUDED_EVENTS:
                continue
            phases = ("Target Practice", "Fly by Night") if name == "Wizard's Tower" and ev == "Target Practice & Fly by Night" else (ev,)
            for phase in phases:
                disp = "Skyscale Target Practice" if name == "Wizard's Tower" and phase == "Target Practice" else display_name(name, phase)
                wiki_link = seg.get("link", "")
                if name == "Wizard's Tower" and phase == "Target Practice":
                    wiki_link = "Skyscale Target Practice in the Wizard's Tower"
                elif name == "Wizard's Tower" and phase == "Fly by Night":
                    wiki_link = "Wizard's Tower: Fly by Night"
                elif name == "Convergences" and phase == "Convergences (Public)":
                    wiki_link = "Convergence: Outer Nayos"
                out.append(Candidate(
                    event=disp,
                    track=name,
                    category=track.get("category", ""),
                    start=start,
                    end=end,
                    location=segment_location(name, phase),
                    waypoint=wp,
                    source="gw2-api-event-timers",
                    wiki=wiki_url(wiki_link),
                    base_priority=base_priority(cfg, track.get("category", ""), name, phase, seg.get("rewards", {}) or {}, int(seg.get("lfg", 0) or 0)),
                    special=is_special_recurring(disp, name),
                    event_id=seg.get("event_id", "")
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
                waypoint_name=f["place"],
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
                    waypoint_name=s["place"],
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
                waypoint_name=a["place"],
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
        # Prefer the open-world map to a same-named story/raid instance.
        if old is None or (item.get("type") == "Public", int(item.get("max_level", 0) or 0)) > (old.get("type") == "Public", int(old.get("max_level", 0) or 0)):
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


def apply_known_level_overrides(cands: list[Candidate]) -> None:
    for c in cands:
        meta = public_instance_meta(c)
        if meta:
            c.category, c.location = meta[1], meta[2]
            c.wiki = wiki_url(meta[0])
            c.public_instance = True
            c.upscaled = meta[3]
            c.level_kind = "upscaled" if c.upscaled else "public"
            c.level = c.level_min = 80
        elif is_structured_convergence(c):
            c.public_instance = True
            c.level_kind = "public"
            c.level = 80
            c.level_min = 80


WIKI_OVERRIDES = {
    "Maws of Torment": "Maws of Torment", "Junundu Rising": "Junundu Rising",
    "Serpents' Ire": "Serpents' Ire", "Forged with Fire": "Forged with Fire",
    "The Oil Floes": "The Oil Floes", "Preparations": "The Battle for the Jade Sea",
}


def apply_metadata(cands: list[Candidate], cfg: dict[str, Any], values: dict[str, Any]) -> None:
    """Enrich by source IDs/exact identities before using verified fallbacks."""
    fallback = cfg.get("verified_metadata", {})
    waypoint_data = dict(fallback.get("waypoints", {}))
    for source in ("waypoints_desert", "waypoints"):
        waypoint_data.update(values.get(source) or {})
    maps = values.get("maps") or fallback.get("maps", [])
    event_levels = dict(fallback.get("event_levels", {}))
    event_levels.update(values.get("event_levels") or {})
    track_data = fallback.get("tracks", {})
    apply_map_levels(cands, maps)
    map_by_id = {m.get("id"): m for m in maps}
    for c in cands:
        saved = track_data.get(norm(c.track), {})
        if content_meta(c.category)["id"] == "unknown" and not c.category.strip():
            c.category = saved.get("category", c.category)
        if not c.waypoint:
            c.waypoint = saved.get("entry_waypoint", "")
        point = waypoint_data.get(c.waypoint)
        if point:
            c.waypoint_name = point["name"]
            if not c.level and point.get("max_level", 0) > 0:
                c.level = point["max_level"]
                c.level_min = point.get("min_level") or c.level
        detail = event_levels.get(c.event_id)
        if detail:
            c.level = c.level_min = detail["level"]
            c.level_kind = "event"
            if not c.location and detail.get("map_id") in map_by_id:
                c.location = map_by_id[detail["map_id"]]["name"]
        if not c.wiki and c.event in WIKI_OVERRIDES:
            c.wiki = wiki_url(WIKI_OVERRIDES[c.event])
    apply_known_level_overrides(cands)


def is_level80_content(c: Candidate) -> bool:
    if c.upscaled:
        return False
    if c.level_kind in {"event", "public"}:
        return c.level == 80
    return c.level == 80 and c.level_min == 80


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
    # These are different activities, even when they mention a timed meta/map.
    if re.search(r"\b(?:cm|challenge mode|private|map clear|map completion|hp train)\b", n):
        return None
    for key, target in ALIASES.items():
        if re.search(r"\b" + re.escape(key) + r"\b", n):
            return target
    # Exact occurrence name/track is safer than fuzzy matching.
    names = sorted({c.event for c in cands} | {c.track for c in cands}, key=len, reverse=True)
    for name in names:
        nn = norm(name)
        if nn in {"convergence", "convergences", "eye of the north", "the mists"}:
            continue
        if len(nn) >= 7 and re.search(r"\b" + re.escape(nn) + r"\b", n):
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
                add_community_signal(
                    cands, target, start, source_name, 30,
                    announcement_url=specific_announcement_url(source_name, row.get("url", ""))
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
                    )

    # Use the source's explicit UTC route stops and a confirmed game region.
    if choya and choya.get("region") == region:
        route = choya.get("route", [])
        for item in route:
            target = ALIASES.get(norm(item.get("title", "")), item.get("title", ""))
            for c in cands:
                if norm(c.event) != norm(target) and norm(c.track) != norm(target):
                    continue
                hhmm = item.get("time_utc", "")
                if c.start.strftime("%H:%M") == hhmm:
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
        if any(len(t) >= 6 and re.search(r"\b" + re.escape(t) + r"\b", text) for t in terms):
            c.fast_seen = True


def action_window_minutes(c: Candidate) -> int:
    """Useful 'go there now' lifetime after start, based on event scope."""
    text = norm(c.event + " " + c.track)

    for terms, minutes in ACTION_WINDOW_OVERRIDES:
        if any(re.search(r"\b" + re.escape(term) + r"\b", text)
               and (term != "convergence" or c.public_instance or is_structured_convergence(c))
               for term in terms):
            return minutes

    scheduled = max(1.0, (c.end - c.start).total_seconds() / 60.0)
    if scheduled <= 15:
        return 5
    if scheduled <= 30:
        return 7
    return 10


def score_candidates(cands: list[Candidate], now: datetime) -> None:
    for c in cands:
        score = float(c.base_priority)
        if c.lightning:
            score += 18  # Announced organized run, not a measured map population.
        if c.fast_seen:
            score += 3
        if c.start > now:
            mins = max(0.0, (c.start - now).total_seconds() / 60)
            score += max(0.0, 24.0 - mins / 5.0)
        else:
            window = float(action_window_minutes(c))
            age = max(0.0, (now - c.start).total_seconds() / 60.0)
            freshness = max(0.0, 1.0 - age / max(1.0, window))
            score += 16.0 * freshness
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


def content_meta(category: str) -> dict[str, Any]:
    category_key = norm(category)
    for item in CONTENT_GROUPS:
        if category_key in {norm(item["category"]), norm(item["short"]), norm(item["id"])}:
            return item
    return CONTENT_UNKNOWN


def content_badge(c: Candidate) -> str:
    meta = content_meta(c.category)
    if meta["id"] == "unknown":
        return (
            '<span class="badge content-badge content-unknown" '
            'title="Unknown event, please contact the developer">Unknown</span>'
        )
    return (
        f'<span class="badge content-badge" style="--content-h:{meta["hue"]}" '
        f'title="{html.escape(meta.get("hint", meta["label"]))}">{html.escape(meta["short"])}</span>'
    )


def waypoint_label(c: Candidate) -> str:
    """Best confirmed human-readable destination for the waypoint button."""
    if c.waypoint_name.strip():
        return c.waypoint_name.strip()
    return "Waypoint (name unavailable)"


def content_options_payload() -> list[dict[str, str]]:
    return [
        {"id": item["id"], "short": item["short"], "label": item["label"],
         "hint": item.get("hint", "")}
        for item in CONTENT_OPTIONS
    ]


def priority_badge(c: Candidate) -> str:
    score = int(round(c.score))
    hue = heat_hue(score, 65, 125)
    return f'<span class="badge heat" style="--h:{hue}" title="Higher means a stronger suggestion. This is not a percentage or live player count.">Prio {score}</span>'


def level_badge(c: Candidate) -> str:
    if not c.level:
        return '<span class="badge level-na" title="Map level unavailable">Level n/a</span>'
    hue = heat_hue(c.level, 20, 80)
    label = f"Level {c.level}"
    if c.upscaled:
        title = "Public instance · Characters scale up to level 80"
        label = "Scaled 80"
    elif c.public_instance:
        title = "Level 80 · Public instance"
    elif c.level_kind == "event":
        title = f"Event level {c.level}"
    elif c.level_min and c.level_min != c.level:
        title = f'Map level {c.level_min}–{c.level}'
        label = f'Map {c.level_min}–{c.level}'
    else:
        title = f'Map level {c.level}'
    return f'<span class="badge heat" style="--h:{hue}" title="{html.escape(title)}">{label}</span>'



def client_event_pool(
    cands: list[Candidate],
    cfg: dict[str, Any],
    now: datetime,
) -> list[Candidate]:
    # Keep enough absolute-time data in the page for exact client-side boundary updates.
    horizon = int(cfg.get("upcoming_horizon_minutes", 120))
    deduped = dedupe(cands)
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
            "waypoint_label": waypoint_label(c),
            "level": c.level,
            "level80_content": is_level80_content(c),
            "content_id": content_meta(c.category)["id"],
            "content_html": content_badge(c),
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
    title = "Listed in a community schedule; check in game for a group"
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


def source_info_link(c: Candidate) -> str:
    for source in c.community_sources:
        url = safe_https_url(COMMUNITY_INFO_URLS.get(source, ""))
        if url:
            return (
                f'<a class="info-link" href="{html.escape(url)}" '
                f'target="_blank" rel="noopener" '
                f'title="Community schedule · {html.escape(source)}">Info</a>'
            )
    return ""


def event_links(c: Candidate) -> str:
    return info_link(c) + source_info_link(c) + announcement_link(c)


def waypoint_button(c: Candidate, extra_class: str = "") -> str:
    if not c.waypoint:
        return '<span class="waypoint-missing" title="See the Wiki link for directions">Waypoint unavailable</span>'
    label = html.escape(waypoint_label(c))
    code = html.escape(c.waypoint)
    return f'<button class="wp {extra_class}" data-copy="{code}" title="Copy: {label}" aria-label="Copy: {label}">{code}</button>'


def card_html(c: Candidate, tz: ZoneInfo, upcoming: bool) -> str:
    st = c.start.astimezone(tz)
    time_label = st.strftime("%H:%M")
    return f'''<article class="event-card">
      <div class="time">{time_label}</div>
      <div class="info">
        <div class="event-line"><span class="name" title="{html.escape(c.event)}">{signal_flash(c)}{html.escape(c.event)}</span><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="{html.escape(c.location)}">{html.escape(c.location)}</span></div>
        <div class="badges">{priority_badge(c)}{level_badge(c)}{content_badge(c)}{event_links(c)}</div>
      </div>
      {waypoint_button(c)}
    </article>'''


def mini_card_html(c: Candidate, tz: ZoneInfo, upcoming: bool) -> str:
    st = c.start.astimezone(tz)
    time_label = st.strftime("%H:%M")
    return f'''<div class="mini-card">
      <span class="mini-time">{time_label}</span>
      <div class="mini-info"><span class="mini-line">{signal_flash(c)}<b title="{html.escape(c.event)}">{html.escape(c.event)}</b><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="{html.escape(c.location)}">{html.escape(c.location)}</span></span></div>
      <div class="mini-badges">{priority_badge(c)}{level_badge(c)}{content_badge(c)}{event_links(c)}</div>
      {waypoint_button(c, "mini-wp")}
    </div>'''


def compact_action_html(c: Candidate, tz: ZoneInfo) -> str:
    st = c.start.astimezone(tz)
    return f'''<div class="all-card">
      <span class="all-time">{st.strftime("%H:%M")}</span>
      <div class="all-info"><span class="title-row">{signal_flash(c)}<b class="event-title" title="{html.escape(c.event)}">{html.escape(c.event)}</b><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="{html.escape(c.location)}">{html.escape(c.location)}</span></span></div>
      <div class="all-badges">{priority_badge(c)}{level_badge(c)}{content_badge(c)}{event_links(c)}</div>
      {waypoint_button(c, "all-wp")}
    </div>'''


def farm_options_html() -> str:
    rows = []
    for title, location, category, waypoint_name, waypoint, wiki_page, info_url, info_title in FARM_MAPS:
        content = content_meta(category)
        rows.append(
            f'<div class="farm-card" data-farm-content-id="{content["id"]}">'
            f'<div class="farm-name"><b>{html.escape(title)}</b>'
            f'<span> · {html.escape(location)}</span></div>'
            f'<div class="farm-links"><span class="badge heat" style="--h:{heat_hue(80, 20, 80)}" title="Map level 80">Level 80</span>'
            f'<span class="badge content-badge" '
            f'style="--content-h:{content["hue"]}" '
            f'title="{html.escape(content["label"])}">{content["short"]}</span>'
            f'<a class="wiki" target="_blank" rel="noopener" '
            f'href="https://wiki.guildwars2.com/wiki/{wiki_page}">Wiki</a>'
            f'<a class="info-link" target="_blank" rel="noopener" '
            f'href="{html.escape(info_url)}" title="{html.escape(info_title)}">Info</a></div>'
            f'<button class="wp farm-wp" type="button" data-copy="{waypoint}" '
            f'title="Copy: {html.escape(waypoint_name)}" '
            f'aria-label="Copy: {html.escape(waypoint_name)}">{waypoint}</button>'
            '</div>'
        )
    return (
        '<details id="farm-options" class="all-block" data-ui-state-key="farm-options">'
        f'<summary title="Find an active map through the in-game LFG; progress varies by map.">'
        f'Farm Maps · Check LFG <span id="farm-count">({len(rows)})</span></summary>'
        '<p class="farm-note">Open the in-game LFG to join an active map. '
        'Random spawns are not live-tracked.</p>'
        f'<div class="farm-list">{"".join(rows)}</div>'
        '</details>'
    )


def credits_html() -> str:
    groups = []
    for heading, sources in CREDIT_GROUPS:
        items = "".join(
            f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">'
            f'{html.escape(name)}</a><span>{html.escape(detail)}</span></li>'
            for name, url, detail in sources
        )
        groups.append(f'<section><h2>{html.escape(heading)}</h2><ul class="credit-list">{items}</ul></section>')
    return (
        '<section id="credits" class="credits-view" hidden>'
        '<button class="back-link" id="back-to-activity" type="button">← Back to activity</button>'
        '<h1>Sources & Thanks</h1>'
        '<p>Thanks to the players, communities and creators who share schedules, guides and game data.</p>'
        f'{"".join(groups)}'
        '<p class="credits-note">Community plans can change. Check in game for an active group.</p>'
        '</section>'
    )


def page_version(
    active: list[Candidate], upcoming: list[Candidate],
    active_extra: list[Candidate], upcoming_extra: list[Candidate],
    active_more: list[Candidate], upcoming_more: list[Candidate],
    client_events: list[Candidate],
    notice: str = ""
) -> str:
    rows = [["engine", ENGINE_VERSION], ["notice", notice]]
    for group, items in (
        ("now", active), ("next", upcoming),
        ("now-extra", active_extra), ("next-extra", upcoming_extra),
        ("now-more", active_more), ("next-more", upcoming_more)
    ):
        for c in items:
            rows.append([
                group, c.event, iso(c.start), iso(c.end), c.location,
                c.waypoint, c.level, c.category,
                c.lightning, c.special, c.announcement_url
            ])
    for c in client_events:
        rows.append([
            "client", c.event, iso(c.start), iso(c.end), c.location,
            c.waypoint, c.level, c.category, c.lightning, c.special,
            c.announcement_url, action_window_minutes(c)
        ])
    # Live score/heat changes do not require a full document reload. The
    # group memberships and all stable event/link metadata remain versioned.
    rows.append([{k: v for k, v in item.items() if k not in {"score", "priority_html"}}
                 for item in client_event_payload(client_events)])
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
    snapshot_json = json.dumps(
        {"format": 1, "page_version": version, "engine": ENGINE_VERSION,
         "events": client_event_payload(client_events), "notice": notice,
         "generated_at_ms": int(now.timestamp() * 1000)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    content_options_json = json.dumps(
        content_options_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    def section(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return '<div class="empty">No scheduled events in this time window.</div>'
        return "\n".join(card_html(c, tz, is_upcoming) for c in items)

    def extras(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return ""
        rows = "\n".join(mini_card_html(c, tz, is_upcoming) for c in items)
        return f'<div class="extra-block">{rows}</div>'

    def expandable(items: list[Candidate], is_upcoming: bool) -> str:
        if not items:
            return ""
        label = "More Current Activity" if not is_upcoming else "More Upcoming Activity · Next 2 Hours"
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
<meta name="page-generated-at-ms" content="{int(now.timestamp() * 1000)}">
<title>GW2 Action</title>
<style>
:root{{--bg:#0f1012;--panel:#17191d;--panel2:#1d2025;--line:#2b2f36;--text:#f3f4f6;--muted:#9aa1aa;--gold:#d7aa42;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}}
.app{{max-width:920px;margin:auto;padding:18px}}
header{{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg) 82%,rgba(15,16,18,0));padding:8px 0 12px;text-align:center}}
#clock{{font-size:clamp(15px,2.8vw,23px);font-weight:800}}
.status-note{{display:inline-block;margin-top:7px;padding:5px 9px;border:1px solid #55492f;border-radius:999px;background:#1b1811;color:#d7bd7a;font-size:10px;font-weight:700}}
.status-note[hidden]{{display:none}}
.header-tools{{display:flex;justify-content:center;align-items:center;gap:8px;margin-top:7px;position:relative;flex-wrap:wrap}}
.control-group{{display:flex;align-items:center;gap:4px;position:relative;min-width:0}}
.control-group+.control-group{{margin-left:2px;padding-left:9px;border-left:1px solid #2a2f36}}
.control-label{{font-size:10px;line-height:1;color:#7f8792;font-weight:800;white-space:nowrap}}
.tz-tools{{display:flex;justify-content:center;gap:4px}}
.tz-btn,.content-filter>summary,.credits-link{{border:1px solid #30353d;background:#121419;color:#9aa2ad;border-radius:999px;padding:4px 8px;font-size:10px;font-weight:700;cursor:pointer;line-height:1.2}}
.tz-btn:hover,.content-filter>summary:hover,.credits-link:hover{{color:#fff;border-color:#555e6b}}
.credits-link{{text-decoration:none}}
.credits-link[aria-current="page"]{{color:#fff;background:#242932;border-color:#5d6673}}
.tz-btn.active{{color:#fff;background:#242932;border-color:#5d6673}}
.content-filter{{position:relative}}
.content-filter>summary{{list-style:none;user-select:none;min-width:52px;text-align:center}}
.content-filter>summary::after{{content:" ▾";color:#6f7782;font-size:9px}}
.content-filter[open]>summary::after{{content:" ▴"}}
.content-filter>summary::-webkit-details-marker{{display:none}}
.content-filter[open]>summary{{color:#fff;background:#242932;border-color:#5d6673}}
.content-menu{{position:fixed;top:auto;right:auto;bottom:auto;left:auto;width:min(640px,calc(100vw - 20px));max-height:min(440px,calc(100vh - 20px));overflow:auto;padding:10px;background:#15171b;border:1px solid #343a43;border-radius:10px;box-shadow:0 10px 28px rgba(0,0,0,.38);text-align:left;z-index:40;overscroll-behavior:contain;visibility:hidden;opacity:0;pointer-events:none}}\n.content-menu.positioned{{visibility:visible;opacity:1;pointer-events:auto}}
.content-menu-head{{position:sticky;top:-10px;z-index:2;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 4px 8px;background:#15171b;border-bottom:1px solid #292e35;margin:-10px 0 6px}}
.content-menu-title{{font-size:10px;font-weight:800;color:#c6cbd2;text-transform:uppercase;letter-spacing:.05em}}
.content-all-btn{{border:0;background:transparent;color:#9fb7d7;font-size:10px;font-weight:800;cursor:pointer;padding:3px 4px}}
.content-all-btn:hover{{color:#fff}}
.content-grid{{display:grid;grid-template-columns:repeat(2,minmax(285px,1fr));gap:2px 10px}}
.content-option{{display:flex;align-items:center;gap:7px;padding:6px 7px;border-radius:6px;font-size:11px;color:#c3c8cf;cursor:pointer;min-width:max-content}}
.content-option:hover{{background:#1d2025;color:#fff}}
.content-option input{{margin:0;accent-color:#d7aa42;flex:0 0 auto}}
.content-option span{{white-space:nowrap}}
.level-filter-option{{min-width:0}}
.level-filter-option small{{font-size:inherit;color:var(--muted)}}
@media(max-width:380px){{.level-filter-option small{{display:block}}}}
.preference-footer{{display:flex;align-items:center;justify-content:center;gap:3px;flex-wrap:wrap;margin:14px 0 2px}}
.preference-note{{color:#69717c;font-size:9px;line-height:1.3}}
.preference-note.saved{{color:#77897b}}
.preference-note.failed{{color:#b89278}}
.delete-cookie{{padding:0;border:0;background:none;color:#929daa;font:inherit;font-size:9px;text-decoration:underline;text-underline-offset:2px;cursor:pointer}}
.delete-cookie[hidden]{{display:none}}
.delete-cookie:hover,.delete-cookie:focus-visible{{color:#fff}}
h2{{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--gold);margin:16px 2px 8px}}
#activity-view>h2{{text-transform:none}}
.event-card{{display:grid;grid-template-columns:82px 1fr 150px;align-items:center;gap:8px;min-height:62px;padding:8px 12px;margin:7px 0;background:var(--panel);border:1px solid var(--line);border-radius:12px}}
.event-card:hover{{background:var(--panel2)}}
.time{{font:800 15px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}}
.event-line,.mini-line,.title-row{{display:flex;align-items:baseline;min-width:0;white-space:nowrap;overflow:hidden}}
.name{{font-size:16px;font-weight:800;max-width:62%;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 1 auto}}
.event-sep{{flex:0 0 auto;margin:0 .34em;color:#69717c;font-weight:400}}
.inline-location{{color:#aeb5bf;font-size:12px;font-weight:500;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto}}
.flash{{display:inline-block;margin-right:5px;line-height:1;vertical-align:-.08em;font-family:"Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif;flex:0 0 auto}}
.badges{{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:5px}}
.badge{{display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;font-size:10px;font-weight:800;line-height:1;border:1px solid #3a4048}}
.badge.heat{{color:hsl(var(--h) 86% 76%);border-color:hsl(var(--h) 55% 38%);background:hsl(var(--h) 45% 16% / .8)}}
.content-badge{{color:hsl(var(--content-h) 78% 78%);border-color:hsl(var(--content-h) 42% 38%);background:hsl(var(--content-h) 34% 16% / .72)}}
.content-unknown{{color:#aeb5bf;border-color:#4b515a;background:#1a1d21}}
.level-na{{color:#a7adb6;background:#171a1f}}
.wiki,.announcement,.info-link{{font-size:10px;color:#aab1bb;text-decoration:none;margin-left:2px}}
.wiki:hover,.announcement:hover,.info-link:hover{{text-decoration:underline;color:#fff}}
.announcement{{color:#d5b76f}}
.info-link{{color:#a7c5de}}
.wp{{border:1px solid #424751;background:#101216;color:#fff;border-radius:9px;padding:10px 8px;cursor:pointer;font:800 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace}}
.wp:hover{{border-color:#69717e;background:#151820}}
.waypoint-missing{{font-size:10px;color:var(--muted)}}
.sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}}
.extra-block{{margin:8px 0 4px;padding:4px 10px;background:#131519;border:1px solid #242931;border-radius:10px}}
.mini-card{{display:grid;grid-template-columns:78px 1fr auto 128px;gap:7px;align-items:center;padding:5px 4px;border-top:1px solid #252a31;min-height:42px}}
.mini-card:first-of-type{{border-top:0}}
.mini-time{{font:800 11px ui-monospace,SFMono-Regular,Consolas,monospace}}
.mini-info{{min-width:0}}
.mini-info b{{font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%;flex:0 1 auto}}
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
.all-info .event-title{{font-size:12px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%;flex:0 1 auto}}
.all-info .inline-location{{font-size:10px}}
.all-badges{{display:flex;gap:4px;align-items:center;flex-wrap:wrap}}
.all-badges .badge{{font-size:9px;padding:3px 5px}}
.all-wp{{padding:8px 6px;font-size:10px}}
.farm-note{{margin:0;padding:0 12px 8px;color:var(--muted);font-size:10px}}
.farm-list{{padding:0 9px 8px}}
.farm-card{{display:grid;grid-template-columns:minmax(0,1fr) auto 128px;gap:8px;align-items:center;min-height:40px;padding:5px 4px;border-top:1px solid #252a31}}
.farm-card[hidden],#farm-options[hidden]{{display:none}}
.farm-name{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}}
.farm-name span{{color:var(--muted);font-size:10px}}
.farm-links{{display:flex;align-items:center;gap:5px}}
.farm-wp{{padding:8px 6px;font-size:10px}}
.credits-view{{max-width:720px;margin:18px auto 30px}}
.credits-view h1{{font-size:20px;margin:14px 2px 6px}}
.credits-view p{{font-size:12px;color:var(--muted);line-height:1.5;margin:0 2px 10px}}
.credits-view h2{{margin-top:20px}}
.back-link{{border:0;background:transparent;padding:0;color:#a7c5de;text-decoration:none;font:inherit;font-size:11px;cursor:pointer}}
.back-link:hover{{color:#fff;text-decoration:underline}}
.credit-list{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}
.credit-list li{{display:flex;flex-direction:column;gap:3px;padding:9px 11px;background:var(--panel);border:1px solid var(--line);border-radius:9px;min-width:0}}
.credit-list a{{color:#c7d9ef;text-decoration:none;font-size:11px;font-weight:800}}
.credit-list a:hover{{color:#fff;text-decoration:underline}}
.credit-list span,.credits-view .credits-note{{color:var(--muted);font-size:10px}}
.credits-view .credits-note{{margin-top:16px}}
#credits:target{{display:block}}
body:has(#credits:target) #activity-view{{display:none}}
.empty{{padding:20px;text-align:center;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px}}
@media(max-width:760px){{
  .app{{padding:10px}}
  .event-card{{grid-template-columns:64px 1fr;grid-template-areas:'time info' 'wp wp';min-height:56px}}
  .time{{grid-area:time}}
  .info{{grid-area:info;min-width:0}}
  .wp{{grid-area:wp;width:100%}}
  .name{{max-width:52%}}
  .mini-card{{grid-template-columns:58px 1fr}}
  .mini-badges{{grid-column:2}}
  .mini-wp{{grid-column:1/3;width:100%}}
  .all-card{{grid-template-columns:50px 1fr}}
  .all-badges{{grid-column:2}}
  .all-wp{{grid-column:1/3;width:100%}}
  .farm-card{{grid-template-columns:minmax(0,1fr) auto}}
  .farm-wp{{grid-column:1/3;width:100%}}
  .header-tools{{gap:6px}}
  .control-group+.control-group{{padding-left:7px}}
  .content-menu{{width:calc(100vw - 16px)}}
  .content-grid{{grid-template-columns:1fr}}
  .credit-list{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="app">
<header>
  <div id="clock"></div>
  <div class="header-tools" aria-label="Display controls">
    <div class="control-group">
      <span class="control-label">Time:</span>
      <div id="time-zone-tools" class="tz-tools">
        <button type="button" class="tz-btn" data-tz-mode="local" title="Device time">Local</button>
        <button type="button" class="tz-btn" data-tz-mode="server" title="GW2 server time · UTC">Server · UTC</button>
      </div>
    </div>
    <div class="control-group">
      <span class="control-label">Content:</span>
      <details id="content-filter" class="content-filter" data-ui-state-key="content-filter">
        <summary id="content-filter-summary" title="Choose which content and levels to show">All</summary>
        <div id="content-filter-menu" class="content-menu"></div>
      </details>
    </div>
    <div class="control-group">
      <a id="credits-link" class="credits-link" href="#credits" title="Sources and thanks">Credits</a>
    </div>
  </div>
  <div id="data-status" class="status-note"{'' if notice else ' hidden'}>{html.escape(notice)}</div>
</header>

<main id="activity-view">
<h2 title="Suggestions from known start times and community plans">NOW · Live activity right now</h2>
<div id="now-top">{section(active, False)}</div>
<div id="now-more">{expandable(active_more, False)}</div>

<h2 title="Suggestions from upcoming starts and community plans">UP NEXT · Starting soon</h2>
<div id="next-top">{section(upcoming, True)}</div>
<div id="next-extra">{extras(upcoming_extra, True)}</div>
<div id="next-more">{expandable(upcoming_more, True)}</div>
{farm_options_html()}
<footer class="preference-footer"><span id="preference-note" class="preference-note" role="status">Preferences will be saved in a single cookie.</span><button id="delete-preferences" class="delete-cookie" type="button" title="Remove saved preferences from this browser" hidden>Delete cookie</button></footer>
</main>
{credits_html()}
<span id="copy-status" class="sr-only" role="status"></span>
</div>

<script id="gw2-snapshot" type="application/json">{snapshot_json}</script>
<script>
const initialSnapshot=JSON.parse(document.getElementById("gw2-snapshot").textContent);
const currentEngine=initialSnapshot.engine;
let EVENT_DATA=initialSnapshot.events;
const CONTENT_OPTIONS={content_options_json};
const UPCOMING_HORIZON_MS={int(cfg.get("upcoming_horizon_minutes", 120))}*60*1000;
const NOW_LIMIT={int(cfg.get("now_limit", 3))};
const NEXT_LIMIT={int(cfg.get("next_limit", 3))};
const EXTRA_LIMIT={int(cfg.get("extra_limit", 2))};
const PRESTART_MS=5*60*1000;
let lastPublishedAtMs=initialSnapshot.generated_at_ms;
let SOURCE_NOTICE=initialSnapshot.notice;

let localZone="";
try {{ localZone=Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }} catch(e) {{}}
const utcLikeZones=new Set(["UTC","Etc/UTC","GMT","Etc/GMT"]);
const localDistinct=Boolean(localZone && !utcLikeZones.has(localZone));

const PREF_COOKIE_KEY="gw2action_prefs";
const LEGACY_TIME_KEY="gw2action-time-mode";
const LEGACY_CONTENT_KEY="gw2action-disabled-content-v1";
const knownContentIds=new Set(CONTENT_OPTIONS.map(item=>item.id));
let preferenceSaveState="idle";
let suppressTransientUiState=false;

function cookiePath() {{
  const path=window.location.pathname || "/";
  if(path.endsWith("/")) return path;
  const slash=path.lastIndexOf("/");
  return slash>=0 ? path.slice(0,slash+1) : "/";
}}
function rawCookie(name) {{
  const prefix=`${{name}}=`;
  let raw="";
  try {{ raw=document.cookie; }} catch(e) {{ return ""; }}
  for(const part of raw.split(";")) {{
    const trimmed=part.trim();
    if(trimmed.startsWith(prefix)) return trimmed.slice(prefix.length);
  }}
  return "";
}}
function ownCookieNames() {{
  const names=new Set();
  for(const part of document.cookie.split(";")) {{
    const name=part.trim().split("=",1)[0];
    if(/^gw2action(?:_|-)/.test(name)) names.add(name);
  }}
  return names;
}}
function syncDeleteCookieButton() {{
  const button=document.getElementById("delete-preferences");
  if(!button) return;
  try {{ button.hidden=ownCookieNames().size===0; }} catch(e) {{ button.hidden=true; }}
}}
function readPreferenceCookie() {{
  const raw=rawCookie(PREF_COOKIE_KEY);
  if(!raw) return null;
  try {{
    const parsed=JSON.parse(decodeURIComponent(raw));
    return parsed && (parsed.v===1 || parsed.v===2) ? parsed : null;
  }} catch(e) {{ return null; }}
}}
function legacyPreferences() {{
  let time="";
  let disabled=[];
  try {{
    time=localStorage.getItem(LEGACY_TIME_KEY) || "";
    const parsed=JSON.parse(localStorage.getItem(LEGACY_CONTENT_KEY) || "[]");
    if(Array.isArray(parsed)) disabled=parsed;
  }} catch(e) {{}}
  return {{time,disabled}};
}}

let lastObservedPreferenceCookie=rawCookie(PREF_COOKIE_KEY);
const cookiePrefs=readPreferenceCookie();
if(cookiePrefs) preferenceSaveState="saved";
const oldPrefs=cookiePrefs ? {{time:"",disabled:[]}} : legacyPreferences();
let timeMode="server";
const requestedTime=cookiePrefs?.time || oldPrefs.time;
if(requestedTime==="local" && localDistinct) timeMode="local";
else if(requestedTime==="server") timeMode="server";
else if(localDistinct) timeMode="local";

function selectedZone() {{ return timeMode==="local" && localDistinct ? localZone : "UTC"; }}

let disabledContent=new Set();
const storedDisabled=cookiePrefs?.disabled ?? oldPrefs.disabled;
if(Array.isArray(storedDisabled)) disabledContent=new Set(storedDisabled.filter(id=>knownContentIds.has(id)));
let showLevel80=typeof cookiePrefs?.level80==="boolean" ? cookiePrefs.level80 : true;

function updatePreferenceNote() {{
  const note=document.getElementById("preference-note");
  syncDeleteCookieButton();
  if(!note) return;
  note.classList.remove("saved","failed");
  if(preferenceSaveState==="saved") {{
    note.textContent="Preferences saved in a single cookie.";
    note.classList.add("saved");
  }} else if(preferenceSaveState==="failed") {{
    note.textContent="Preferences could not be saved in this browser.";
    note.classList.add("failed");
  }} else if(preferenceSaveState==="deleted") {{
    note.textContent="Saved preferences removed.";
    note.classList.add("saved");
  }} else if(preferenceSaveState==="external-deleted") {{
    note.textContent="Preference cookie removed in this browser.";
  }} else if(preferenceSaveState==="unreadable") {{
    note.textContent="Saved preferences could not be read.";
    note.classList.add("failed");
  }} else if(preferenceSaveState==="clear-failed") {{
    note.textContent="Could not confirm all preferences were removed.";
    note.classList.add("failed");
  }} else {{
    note.textContent="Preferences will be saved in a single cookie.";
  }}
}}
function savePreferences() {{
  suppressTransientUiState=false;
  const payload={{v:2,time:timeMode,disabled:[...disabledContent].sort(),level80:showLevel80}};
  const encoded=encodeURIComponent(JSON.stringify(payload));
  const secure=window.location.protocol==="https:" ? "; Secure" : "";
  let saved=false;
  try {{
    document.cookie=`${{PREF_COOKIE_KEY}}=${{encoded}}; Max-Age=31536000; Path=${{cookiePath()}}; SameSite=Lax${{secure}}`;
    saved=rawCookie(PREF_COOKIE_KEY)===encoded;
  }} catch(e) {{}}
  lastObservedPreferenceCookie=rawCookie(PREF_COOKIE_KEY);
  preferenceSaveState=saved ? "saved" : "failed";
  if(saved) {{
    try {{
      localStorage.removeItem(LEGACY_TIME_KEY);
      localStorage.removeItem(LEGACY_CONTENT_KEY);
    }} catch(e) {{}}
  }}
  updatePreferenceNote();
  return saved;
}}
function refreshPreferenceControls() {{
  clockFmt=clockFormatter();eventTimeFmt=eventTimeFormatter();
  updateTimeZoneControls();buildContentFilter();updatePreferenceNote();tickClock();renderLive(true);
}}
function syncExternalPreferenceChange() {{
  const current=rawCookie(PREF_COOKIE_KEY);
  if(current===lastObservedPreferenceCookie) {{syncDeleteCookieButton();return;}}
  const previous=lastObservedPreferenceCookie;
  lastObservedPreferenceCookie=current;
  const prefs=readPreferenceCookie();
  if(prefs) {{
    timeMode=prefs.time==="server" ? "server" : localDistinct ? "local" : "server";
    disabledContent=new Set((Array.isArray(prefs.disabled)?prefs.disabled:[]).filter(id=>knownContentIds.has(id)));
    showLevel80=typeof prefs.level80==="boolean"?prefs.level80:true;
    preferenceSaveState="saved";
    refreshPreferenceControls();
  }} else if(previous || current) {{
    timeMode=localDistinct?"local":"server";
    disabledContent.clear();showLevel80=true;
    preferenceSaveState=current?"unreadable":"external-deleted";
    if(!current) {{
      // An old localStorage preference or a pending page refresh must not
      // recreate settings after the browser has removed the cookie.
      try {{localStorage.removeItem(LEGACY_TIME_KEY);localStorage.removeItem(LEGACY_CONTENT_KEY);}} catch(e) {{}}
      try {{sessionStorage.removeItem(TRANSIENT_UI_KEY);}} catch(e) {{}}
    }}
    refreshPreferenceControls();
  }} else updatePreferenceNote();
}}
function deleteStoredPreferences() {{
  suppressTransientUiState=true;
  let cookiesCleared=true;
  try {{
    const ownNames=ownCookieNames();
    const paths=new Set(["/"]);
    let parent="";
    for(const part of cookiePath().split("/").filter(Boolean)) {{
      parent+="/"+part;
      paths.add(parent);paths.add(parent+"/");
    }}
    for(const name of ownNames) for(const path of paths) for(const domain of ["",`; Domain=${{window.location.hostname}}`]) {{
      document.cookie=`${{name}}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=${{path}}${{domain}}; SameSite=Lax`;
    }}
    cookiesCleared=ownCookieNames().size===0;
  }} catch(e) {{ cookiesCleared=false; }}
  function clearOwnKeys(storage) {{
    try {{
      const keys=[];
      for(let i=0;i<storage.length;i++) {{
        const key=storage.key(i);
        if(key && /^gw2action(?:_|-)/.test(key)) keys.push(key);
      }}
      for(const key of keys) storage.removeItem(key);
      return keys.every(key=>storage.getItem(key)===null);
    }} catch(e) {{ return false; }}
  }}
  const localCleared=clearOwnKeys(localStorage);
  const sessionCleared=clearOwnKeys(sessionStorage);
  lastObservedPreferenceCookie=rawCookie(PREF_COOKIE_KEY);
  timeMode=localDistinct?"local":"server";
  disabledContent.clear();showLevel80=true;
  preferenceSaveState=cookiesCleared && localCleared && sessionCleared ? "deleted":"clear-failed";
  refreshPreferenceControls();
  return preferenceSaveState==="deleted";
}}
function contentEnabled(event) {{
  // Unknown categories stay visible by design so new content is never silently hidden.
  if(event.content_id==="unknown") return true;
  const contentAllowed=!disabledContent.has(event.content_id);
  const levelAllowed=showLevel80 || !event.level80_content;
  return contentAllowed && levelAllowed;
}}

function updateTimeZoneControls() {{
  const tools=document.getElementById("time-zone-tools");
  if(!tools) return;
  tools.hidden=false;
  const localButton=tools.querySelector('[data-tz-mode="local"]');
  if(localButton) {{
    localButton.hidden=!localDistinct;
    localButton.title=localDistinct ? `Device time · ${{localZone}}` : "Device time";
  }}
  const serverButton=tools.querySelector('[data-tz-mode="server"]');
  if(serverButton) serverButton.title="GW2 server time · UTC";
  tools.querySelectorAll(".tz-btn").forEach(btn=>{{
    const active=btn.dataset.tzMode===timeMode;
    btn.classList.toggle("active",active);
    btn.setAttribute("aria-pressed",active?"true":"false");
  }});
}}

function updateContentSummary() {{
  const summary=document.getElementById("content-filter-summary");
  if(!summary) return;
  const total=CONTENT_OPTIONS.length;
  const enabled=total-disabledContent.size;
  const base=disabledContent.size===0 ? "All" : `${{enabled}}/${{total}}`;
  summary.textContent=showLevel80 ? base : `${{base}} · Lvl 80 off`;
  summary.title="Choose which content and levels to show";
  updateFarmVisibility();
}}

function updateFarmVisibility() {{
  const block=document.getElementById("farm-options");
  if(!block) return;
  let visible=0;
  for(const row of block.querySelectorAll("[data-farm-content-id]")) {{
    row.hidden=!showLevel80 || disabledContent.has(row.dataset.farmContentId);
    if(!row.hidden) visible++;
  }}
  block.hidden=visible===0;
  const count=document.getElementById("farm-count");
  if(count) count.textContent=`(${{visible}})`;
}}

function buildContentFilter() {{
  const menu=document.getElementById("content-filter-menu");
  if(!menu) return;
  const rows=CONTENT_OPTIONS.map(item=>{{
    const help=item.hint?` title="${{esc(item.hint)}}"`:"";
    return `<label class="content-option"${{help}}><input type="checkbox" data-content-id="${{esc(item.id)}}"${{help}} ${{disabledContent.has(item.id)?"":"checked"}}><span>${{esc(item.short)}} · ${{esc(item.label)}}</span></label>`;
  }}).join("");
  const level80=`<label class="content-option level-filter-option" title="Useful when leveling alts"><input type="checkbox" data-level80 ${{showLevel80?"checked":""}}><span>Level 80 Events <small>· can be hidden while leveling alts</small></span></label>`;
  menu.innerHTML=`<div class="content-menu-head"><span class="content-menu-title">Expansions & Content</span><button type="button" class="content-all-btn" data-content-all title="Show all filters">Show all</button></div><div class="content-grid">${{rows}}${{level80}}</div>`;
  updateContentSummary();
}}

function positionContentMenu() {{
  const filter=document.getElementById("content-filter");
  const summary=document.getElementById("content-filter-summary");
  const menu=document.getElementById("content-filter-menu");
  if(!filter?.open || !summary || !menu) return;

  const rect=summary.getBoundingClientRect();
  const viewportWidth=document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight=document.documentElement.clientHeight || window.innerHeight;
  const margin=8;
  const gap=6;
  const width=Math.max(0,Math.min(640,viewportWidth-(margin*2)));

  menu.style.width=`${{width}}px`;
  menu.style.right="auto";
  menu.style.bottom="auto";

  // Popovers are centered horizontally in the visible viewport.
  const centeredLeft=Math.max(margin,Math.round((viewportWidth-width)/2));
  menu.style.left=`${{centeredLeft}}px`;

  const desiredHeight=Math.min(menu.scrollHeight,440);
  const below=viewportHeight-rect.bottom-gap-margin;
  const above=rect.top-gap-margin;
  const placeBelow=below>=Math.min(desiredHeight,240) || below>=above;
  const available=Math.max(0,Math.min(440,viewportHeight-margin*2,placeBelow?below:above));
  menu.style.maxHeight=`${{available}}px`;

  if(placeBelow) {{
    menu.style.top=`${{Math.min(viewportHeight-margin-available,Math.max(margin,rect.bottom+gap))}}px`;
  }} else {{
    const top=Math.max(margin,rect.top-gap-available);
    menu.style.top=`${{top}}px`;
  }}

  // Reveal only after the final viewport-centered position is known.
  menu.classList.add("positioned");
}}

function closeContentMenu() {{
  const filter=document.getElementById("content-filter");
  const menu=document.getElementById("content-filter-menu");
  if(menu) menu.classList.remove("positioned");
  if(filter?.open) filter.open=false;
}}

const TRANSIENT_UI_KEY="gw2action_ui_state_v3:"+window.location.pathname;
const detailsState={{}};

function focusKey() {{
  const el=document.activeElement;
  if(el?.matches("input[data-content-id]")) return "content:"+el.dataset.contentId;
  if(el?.matches("input[data-level80]")) return "level80";
  if(el?.matches("[data-content-all]")) return "show-all";
  if(el?.matches("[data-tz-mode]")) return "time:"+el.dataset.tzMode;
  if(el?.matches("summary")) return "details:"+(el.parentElement.dataset.uiStateKey || "");
  return "";
}}
function restoreFocus(key) {{
  const controls=[...document.querySelectorAll("input[data-content-id],input[data-level80],[data-content-all],[data-tz-mode],details[data-ui-state-key]>summary")];
  const target=controls.find(el=>
    key==="content:"+el.dataset.contentId ||
    (key==="level80" && el.matches("input[data-level80]")) ||
    (key==="show-all" && el.hasAttribute("data-content-all")) ||
    key==="time:"+el.dataset.tzMode ||
    (el.matches("summary") && key==="details:"+el.parentElement.dataset.uiStateKey));
  target?.focus({{preventScroll:true}});
}}
document.addEventListener("toggle",ev=>{{
  const el=ev.target;
  if(el?.isConnected && el.matches?.("details[data-ui-state-key]")) detailsState[el.dataset.uiStateKey]=el.open;
}},true);

function collectDetailsState() {{
  const open={{...detailsState}};
  document.querySelectorAll("details[data-ui-state-key]").forEach(item=>{{
    const key=item.dataset.uiStateKey;
    if(key) open[key]=Boolean(item.open);
  }});
  Object.assign(detailsState,open);
  return open;
}}

function stashTransientUiState() {{
  if(suppressTransientUiState) return;
  const menu=document.getElementById("content-filter-menu");
  const state={{
    v:3,
    at:Date.now(),
    detailsOpen:collectDetailsState(),
    contentScrollTop:Number(menu?.scrollTop || 0),
    pageScrollY:Number(window.scrollY || 0),
    focused:focusKey(),
    preferences:{{time:timeMode,disabled:[...disabledContent],level80:showLevel80,state:preferenceSaveState,cookie:rawCookie(PREF_COOKIE_KEY)}}
  }};
  try {{ sessionStorage.setItem(TRANSIENT_UI_KEY,JSON.stringify(state)); }} catch(e) {{}}
}}

function restoreTransientUiState() {{
  let state=null;
  try {{
    const raw=sessionStorage.getItem(TRANSIENT_UI_KEY);
    sessionStorage.removeItem(TRANSIENT_UI_KEY);
    if(raw) state=JSON.parse(raw);
  }} catch(e) {{ state=null; }}

  if(!state || state.v!==3 || !Number.isFinite(state.at) || Date.now()-state.at<0 || Date.now()-state.at>90000) return;

  const pref=state.preferences;
  // A browser deletion between pagehide and reload must not revive stale
  // preferences from sessionStorage, even if this tab had shown "saved".
  if(pref && (pref.state!=="saved" || (pref.cookie && pref.cookie===rawCookie(PREF_COOKIE_KEY)))) {{
    timeMode=pref.time==="local" && localDistinct ? "local" : "server";
    disabledContent=new Set((Array.isArray(pref.disabled)?pref.disabled:[]).filter(id=>knownContentIds.has(id)));
    showLevel80=typeof pref.level80==="boolean"?pref.level80:true;
    if(["idle","saved","failed"].includes(pref.state)) preferenceSaveState=pref.state;
    clockFmt=clockFormatter();eventTimeFmt=eventTimeFormatter();
    updateTimeZoneControls();buildContentFilter();updatePreferenceNote();tickClock();
  }}

  if(state.detailsOpen && typeof state.detailsOpen==="object") {{
    Object.assign(detailsState,state.detailsOpen);
    document.querySelectorAll("details[data-ui-state-key]").forEach(item=>{{
      const key=item.dataset.uiStateKey;
      if(key && Object.prototype.hasOwnProperty.call(state.detailsOpen,key)) {{
        item.open=Boolean(state.detailsOpen[key]);
      }}
    }});
  }}
  renderLive(true);

  const filter=document.getElementById("content-filter");
  const menu=document.getElementById("content-filter-menu");

  if(filter?.open) {{
    positionContentMenu();
    if(menu && Number.isFinite(state.contentScrollTop)) {{
      menu.scrollTop=Math.max(0,state.contentScrollTop);
    }}
  }}

  // Restore page position after expanded/collapsed sections have settled.
  if(Number.isFinite(state.pageScrollY)) {{
    requestAnimationFrame(()=>requestAnimationFrame(()=>
      window.scrollTo({{top:Math.max(0,state.pageScrollY),left:0,behavior:"auto"}})
    ));
  }}
  restoreFocus(state.focused || "");
}}

function clockFormatter() {{
  const timeZone=selectedZone();
  const weekday=new Intl.DateTimeFormat("en-US",{{timeZone,weekday:"long"}});
  const date=new Intl.DateTimeFormat("en-US",{{timeZone,month:"long",day:"numeric"}});
  const time=new Intl.DateTimeFormat("en-GB",{{timeZone,hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}});
  return {{format(now){{return `${{weekday.format(now)}} · ${{date.format(now)}} · ${{time.format(now)}}`;}}}};
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
function waypointButton(e,cls="") {{
  if(!e.waypoint) return '<span class="waypoint-missing" title="See the Wiki link for directions">Waypoint unavailable</span>';
  return `<button class="wp ${{cls}}" data-copy="${{esc(e.waypoint)}}" title="Copy: ${{esc(e.waypoint_label)}}" aria-label="Copy: ${{esc(e.waypoint_label)}}">${{esc(e.waypoint)}}</button>`;
}}
function topCard(e) {{
  return `<article class="event-card"><div class="time">${{formatEventTime(e.start)}}</div><div class="info"><div class="event-line"><span class="name" title="${{esc(e.event)}}">${{e.flash_html}}${{esc(e.event)}}</span><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="${{esc(e.location)}}">${{esc(e.location)}}</span></div><div class="badges">${{e.priority_html}}${{e.level_html}}${{e.content_html}}${{e.links_html}}</div></div>${{waypointButton(e,"")}}</article>`;
}}
function miniCard(e) {{
  return `<div class="mini-card"><span class="mini-time">${{formatEventTime(e.start)}}</span><div class="mini-info"><span class="mini-line">${{e.flash_html}}<b title="${{esc(e.event)}}">${{esc(e.event)}}</b><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="${{esc(e.location)}}">${{esc(e.location)}}</span></span></div><div class="mini-badges">${{e.priority_html}}${{e.level_html}}${{e.content_html}}${{e.links_html}}</div>${{waypointButton(e,"mini-wp")}}</div>`;
}}
function compactCard(e) {{
  return `<div class="all-card"><span class="all-time">${{formatEventTime(e.start)}}</span><div class="all-info"><span class="title-row">${{e.flash_html}}<b class="event-title" title="${{esc(e.event)}}">${{esc(e.event)}}</b><span class="event-sep" aria-hidden="true">·</span><span class="inline-location" title="${{esc(e.location)}}">${{esc(e.location)}}</span></span></div><div class="all-badges">${{e.priority_html}}${{e.level_html}}${{e.content_html}}${{e.links_html}}</div>${{waypointButton(e,"all-wp")}}</div>`;
}}

function byScore(a,b) {{ return (b.score-a.score)||(Date.parse(a.start)-Date.parse(b.start))||a.event.localeCompare(b.event); }}
function byTime(a,b) {{ return (Date.parse(a.start)-Date.parse(b.start))||(b.score-a.score)||a.event.localeCompare(b.event); }}
function pickUpcomingExtras(rest) {{
  const spotlight=rest.filter(e=>e.special).sort(byTime);
  const picked=spotlight.slice(0,EXTRA_LIMIT);
  const keys=new Set(picked.map(e=>e.key));
  if(picked.length<EXTRA_LIMIT) {{
    const fallback=rest.filter(e=>!keys.has(e.key)).sort(byScore);
    picked.push(...fallback.slice(0,EXTRA_LIMIT-picked.length));
  }}
  return picked;
}}
function layoutAt(nowMs) {{
  const visible=EVENT_DATA.filter(contentEnabled);
  const active=visible.filter(e=>Date.parse(e.active_from)<=nowMs && nowMs<Date.parse(e.active_until));
  const upcoming=visible.filter(e=>{{const start=Date.parse(e.start);return (nowMs+PRESTART_MS)<start && start<=(nowMs+UPCOMING_HORIZON_MS);}});
  const activeTop=[...active].sort(byScore).slice(0,NOW_LIMIT).sort(byTime);
  const activeTopKeys=new Set(activeTop.map(e=>e.key));
  const activeMore=active.filter(e=>!activeTopKeys.has(e.key)).sort(byTime);
  const upcomingTop=[...upcoming].sort(byScore).slice(0,NEXT_LIMIT).sort(byTime);
  const upcomingTopKeys=new Set(upcomingTop.map(e=>e.key));
  const upcomingRest=upcoming.filter(e=>!upcomingTopKeys.has(e.key));
  const upcomingExtra=pickUpcomingExtras(upcomingRest).sort(byTime);
  const extraKeys=new Set(upcomingExtra.map(e=>e.key));
  const upcomingMore=upcomingRest.filter(e=>!extraKeys.has(e.key)).sort(byTime);
  return {{activeTop,activeMore,upcomingTop,upcomingExtra,upcomingMore}};
}}
function emptyBlock() {{
  const filtersActive=disabledContent.size>0 || !showLevel80;
  const message=filtersActive ? "No events match the selected filters." : "No scheduled events in this time window.";
  return `<div class="empty">${{message}}</div>`;
}}
function detailsBlock(label,items,stateKey) {{
  if(!items.length) return "";
  return `<details class="all-block" data-ui-state-key="${{esc(stateKey)}}"><summary>${{label}} <span>(${{items.length}})</span></summary><div class="all-list">${{items.map(compactCard).join("")}}</div></details>`;
}}
let lastLayoutSignature="";
const lastSectionMarkup=new Map();
function updateLiveSection(id,markup,wasOpen) {{
  if(lastSectionMarkup.get(id)===markup) return;
  const el=document.getElementById(id);
  if(!el) return;
  el.innerHTML=markup;
  lastSectionMarkup.set(id,markup);
  if(typeof wasOpen==="boolean") {{
    const details=el.querySelector("details");
    if(details) details.open=wasOpen;
  }}
}}
function renderLive(force=false) {{
  const dataStatus=document.getElementById("data-status");
  if(dataStatus) {{
    const delayed=Date.now()>lastPublishedAtMs+15*60*1000;
    dataStatus.textContent=delayed ? "Updates are delayed; upcoming events may be incomplete." : SOURCE_NOTICE;
    dataStatus.hidden=!dataStatus.textContent;
  }}
  const openState=collectDetailsState();
  const nowMoreOpen=Boolean(openState["now-more"]);
  const nextMoreOpen=Boolean(openState["next-more"]);
  const groups=layoutAt(Date.now());
  const signature=JSON.stringify([groups.activeTop,groups.activeMore,groups.upcomingTop,groups.upcomingExtra,groups.upcomingMore,timeMode,[...disabledContent].sort(),showLevel80]);
  if(!force && signature===lastLayoutSignature) return;
  if(!force && document.activeElement?.closest?.("#now-top,#now-more,#next-top,#next-extra,#next-more")) return;
  const focused=focusKey();
  lastLayoutSignature=signature;
  updateLiveSection("now-top",groups.activeTop.length?groups.activeTop.map(topCard).join(""):emptyBlock());
  updateLiveSection("now-more",detailsBlock("More Current Activity",groups.activeMore,"now-more"),nowMoreOpen);
  updateLiveSection("next-top",groups.upcomingTop.length?groups.upcomingTop.map(topCard).join(""):emptyBlock());
  updateLiveSection("next-extra",groups.upcomingExtra.length?`<div class="extra-block">${{groups.upcomingExtra.map(miniCard).join("")}}</div>`:"");
  updateLiveSection("next-more",detailsBlock("More Upcoming Activity · Next 2 Hours",groups.upcomingMore,"next-more"),nextMoreOpen);
  restoreFocus(focused);
}}

document.getElementById("time-zone-tools")?.addEventListener("click",ev=>{{
  const btn=ev.target.closest(".tz-btn");
  if(!btn) return;
  const mode=btn.dataset.tzMode;
  if(mode==="local" && !localDistinct) return;
  if(mode!=="local" && mode!=="server") return;
  timeMode=mode;savePreferences();clockFmt=clockFormatter();eventTimeFmt=eventTimeFormatter();updateTimeZoneControls();tickClock();renderLive(true);
}});

document.getElementById("content-filter-menu")?.addEventListener("change",ev=>{{
  const levelInput=ev.target.closest('input[data-level80]');
  if(levelInput) {{
    showLevel80=Boolean(levelInput.checked);
    savePreferences();updateContentSummary();renderLive(true);
    return;
  }}
  const input=ev.target.closest('input[data-content-id]');
  if(!input) return;
  const id=input.dataset.contentId;
  if(!knownContentIds.has(id)) return;
  if(input.checked) disabledContent.delete(id);
  else disabledContent.add(id);
  savePreferences();updateContentSummary();renderLive(true);
}});

document.getElementById("content-filter-menu")?.addEventListener("click",ev=>{{
  const all=ev.target.closest("[data-content-all]");
  if(!all) return;
  const focused=focusKey();
  const scrollTop=document.getElementById("content-filter-menu").scrollTop;
  disabledContent.clear();showLevel80=true;savePreferences();buildContentFilter();renderLive(true);
  positionContentMenu();
  document.getElementById("content-filter-menu").scrollTop=scrollTop;
  restoreFocus(focused);
}});
document.getElementById("delete-preferences")?.addEventListener("click",deleteStoredPreferences);
window.addEventListener("focus",syncExternalPreferenceChange);
window.addEventListener("pageshow",syncExternalPreferenceChange);
document.addEventListener("visibilitychange",()=>{{if(!document.hidden) syncExternalPreferenceChange();}});
document.addEventListener("focusout",()=>renderLive(false));

const contentFilter=document.getElementById("content-filter");
contentFilter?.addEventListener("toggle",()=>{{
  const menu=document.getElementById("content-filter-menu");
  if(contentFilter.open) {{
    if(menu) menu.classList.remove("positioned");
    positionContentMenu();
  }} else if(menu) {{
    menu.classList.remove("positioned");
  }}
}});
document.addEventListener("pointerdown",ev=>{{
  if(contentFilter?.open && !contentFilter.contains(ev.target)) closeContentMenu();
}});
document.addEventListener("keydown",ev=>{{
  if(ev.key==="Escape" && contentFilter?.open) {{
    closeContentMenu();
    document.getElementById("content-filter-summary")?.focus();
  }}
}});
window.addEventListener("resize",()=>{{if(contentFilter?.open) positionContentMenu();}});
window.addEventListener("scroll",()=>{{if(contentFilter?.open) positionContentMenu();}},true);

document.querySelector(".app")?.addEventListener("click",async ev=>{{
  const btn=ev.target.closest(".wp");
  if(!btn) return;
  const value=btn.dataset.copy;
  try {{await navigator.clipboard.writeText(value);const old=btn.textContent;btn.textContent=`✓ ${{value}}`;setTimeout(()=>{{btn.textContent=old;}},900);}} catch(e) {{
    const range=document.createRange();range.selectNodeContents(btn);
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    const status=document.getElementById("copy-status");
    if(status) status.textContent="Automatic copy unavailable. Press Ctrl+C or Cmd+C to copy the selected waypoint.";
  }}
}});

let activityScrollY=0;
let showingCredits=false;
let creditsOpenedFromActivity=false;
document.getElementById("credits-link")?.addEventListener("click",()=>{{
  activityScrollY=window.scrollY;
  creditsOpenedFromActivity=true;
}});
document.getElementById("back-to-activity")?.addEventListener("click",()=>{{
  if(window.location.hash==="#credits" && creditsOpenedFromActivity) {{
    history.back();
    return;
  }}
  history.replaceState(null,"",window.location.pathname+window.location.search);
  syncRoute();
}});
function syncRoute() {{
  const credits=window.location.hash==="#credits";
  const activity=document.getElementById("activity-view");
  const page=document.getElementById("credits");
  if(!activity || !page) return;
  activity.hidden=credits;
  page.hidden=!credits;
  const link=document.getElementById("credits-link");
  if(credits) {{
    link?.setAttribute("aria-current","page");
    closeContentMenu();
    requestAnimationFrame(()=>window.scrollTo({{top:0,left:0,behavior:"auto"}}));
  }} else {{
    link?.removeAttribute("aria-current");
    if(showingCredits) requestAnimationFrame(()=>window.scrollTo({{top:activityScrollY,left:0,behavior:"auto"}}));
  }}
  showingCredits=credits;
}}
window.addEventListener("hashchange",syncRoute);

updateTimeZoneControls();buildContentFilter();updatePreferenceNote();tickClock();renderLive(true);restoreTransientUiState();setInterval(tickClock,1000);
syncRoute();
window.addEventListener("pagehide",stashTransientUiState);
// Exact local category transition, independent of Pages publication latency.
setInterval(()=>{{syncExternalPreferenceChange();renderLive(false);}},2000);

if(window.location.search){{history.replaceState(null,"",window.location.pathname+window.location.hash);}}
const versionMeta=document.querySelector('meta[name="gw2-page-version"]');
let currentVersion=versionMeta.content;
let updateInFlight=false;
let lastPagesAtMs=lastPublishedAtMs;
let lastStateCheckAt=0;
async function loadPage(url){{
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),12000);
  try{{
    const response=await fetch(url,{{cache:"no-store",signal:controller.signal}});
    return response.ok ? new DOMParser().parseFromString(await response.text(),"text/html") : null;
  }}finally{{clearTimeout(timeout);}}
}}
function applyUpdate(page,fromState=false){{
  const version=page.querySelector('meta[name="gw2-page-version"]')?.content;
  const snapshotText=page.getElementById("gw2-snapshot")?.textContent;
  if(!version || !snapshotText) return;
  const snapshot=JSON.parse(snapshotText);
  if(snapshot.format!==1 || snapshot.page_version!==version ||
     !Array.isArray(snapshot.events) || !snapshot.events.length || typeof snapshot.notice!=="string" ||
     !Number.isFinite(snapshot.generated_at_ms) || snapshot.generated_at_ms> Date.now()+5*60*1000) return;
  if(!fromState) lastPagesAtMs=Math.max(lastPagesAtMs,snapshot.generated_at_ms);
  if(snapshot.generated_at_ms<lastPublishedAtMs) return;
  if(snapshot.engine!==currentEngine){{
    if(fromState || window.location.hash==="#credits" || document.querySelector("details[open]")) return;
    stashTransientUiState();
    const next=new URL(window.location.href);
    next.searchParams.set("_",Date.now().toString());
    window.location.replace(next.toString());
    return;
  }}
  lastPublishedAtMs=snapshot.generated_at_ms;
  if(version!==currentVersion){{
    EVENT_DATA=snapshot.events;
    currentVersion=version;
    versionMeta.content=version;
  }}
  SOURCE_NOTICE=snapshot.notice;
  renderLive(false);
}}
async function checkForUpdate(){{
  if(updateInFlight || document.hidden) return;
  updateInFlight=true;
  try{{
    const u=new URL(window.location.pathname,window.location.origin);u.searchParams.set("_",Date.now().toString());
    const page=await loadPage(u.toString());
    if(page) applyUpdate(page);
  }}catch(e){{}}
  try{{
    // Pages can report a successful deploy while still serving an older artifact.
    if(Date.now()>lastPagesAtMs+3*60*1000 && Date.now()>lastStateCheckAt+60*1000){{
      lastStateCheckAt=Date.now();
      const u=new URL("https://raw.githubusercontent.com/Rhacco/now/gw2-action-state/worker-gw2a/index.html");
      u.searchParams.set("_",Date.now().toString());
      const page=await loadPage(u.toString());
      if(page) applyUpdate(page,true);
    }}
  }}catch(e){{}}finally{{updateInFlight=false;}}
}}
setInterval(checkForUpdate,20000);
document.addEventListener("visibilitychange",()=>{{if(!document.hidden) checkForUpdate();}});
if(Date.now()>lastPagesAtMs+3*60*1000) checkForUpdate();
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
        "waypoints": parse_waypoints,
        "waypoints_desert": parse_waypoints,
        "event_levels": parse_event_levels,
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
        if SOURCE_REGIONS.get(name, cfg.get("region", "EU")) != cfg.get("region", "EU"):
            values[name] = None
            continue
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
    apply_ninja_waypoints(cands, values.get("ninja"))
    def community_value(name: str) -> Any:
        age = source_age_minutes(state, name, now)
        if age is not None and age > max(180, int(ttl.get(name, 30)) * 3):
            return None
        return values.get(name)

    apply_community(
        cands,
        now,
        cfg.get("region", "EU"),
        community_value("metasheet"),
        community_value("hardstuck"),
        community_value("ttwurm"),
        community_value("dcap"),
        community_value("gw2community"),
        community_value("vip"),
        community_value("choya"),
    )
    apply_metadata(cands, cfg, values)
    apply_fast_context(cands, values.get("fast"))

    active, upcoming, active_extra, upcoming_extra, active_more, upcoming_more = choose(
        cands, cfg, now
    )
    client_events = client_event_pool(cands, cfg, now)

    notice = user_status_notice(state, ttl, now, cfg.get("region", "EU"))

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
