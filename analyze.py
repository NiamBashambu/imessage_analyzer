#!/usr/bin/env python3
"""Group chat leaderboard — screenshotable pages for messages and tapbacks."""

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz
from dotenv import load_dotenv
from jinja2 import Template

sys.path.insert(0, str(Path(__file__).parent))
from src.name_mapper import (
    NameMapper,
    canonical_first_name,
    build_chat_labeler,
    label_with_last_initial,
    label_with_last_name,
    normalize_phone,
    person_identity_key,
)

# 2000–2007 are tapbacks; 3000s are removals
TAPBACK_RANGE = range(2000, 2008)
REMOVE_RANGE = range(3000, 3010)
REACT_LABEL = {
    2000: "loved",
    2001: "liked",
    2002: "disliked",
    2003: "haha",
    2004: "emphasized",
    2005: "questioned",
    2006: "haha",  # newer iOS tapback, treated as haha
    2007: "other",
}

EXPORT_FIELDS = [
    "name",
    "message_count",
    "percentage",
    "avg_per_day",
    "tapbacks_got",
    "tapbacks_given",
    "tapbacks_per_100",
    "given_per_100",
    "balance",
    "haha_got",
    "haha_given",
    "loved_got",
    "liked_got",
    "days_active",
    "streak",
    "peak_day",
    "days_opened",
    "days_closed",
    "night_pct",
    "morning_pct",
    "weekend_pct",
    "kickoffs",
    "bursts",
    "followups",
    "questions",
    "links",
    "emoji_count",
    "reply_median_min",
    "reply_count",
    "recent",
    "peak_hour",
    "peak_weekday",
    "first_label",
    "last_label",
]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def hour_label(h):
    if h == 0:
        return "12am"
    if h < 12:
        return f"{h}am"
    if h == 12:
        return "12pm"
    return f"{h - 12}pm"


def _parse_ymd(raw, label):
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{label} must be YYYY-MM-DD (got {raw!r})")


class Config:
    def __init__(self, args=None):
        load_dotenv()
        if args is None:
            args = argparse.Namespace(
                db=None, outdir=None, gcs=None, keywords=None,
                year=None, since=None, until=None, export=None,
            )
        self.db_path = self._expand(args.db or os.getenv("DB_PATH", "~/Library/Messages/chat.db"))
        self.output_dir = self._expand(args.outdir or os.getenv("OUTPUT_DIR", "./output"))
        self.aliases_file = self._expand(os.getenv("ALIASES_FILE", "./aliases.json"))
        self.cache_file = self._expand("./data/name_cache.json")
        self.contacts_dump = self._expand("./data/contacts_dump.json")
        self.your_name = os.getenv("YOUR_NAME", "You")
        tz_name = os.getenv("TIMEZONE", "America/Los_Angeles")
        try:
            self.tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            print(f"Error: unknown TIMEZONE {tz_name!r}. Use an IANA name like America/New_York.", file=sys.stderr)
            sys.exit(1)
        self.tz_label = os.getenv("TIMEZONE_LABEL") or _friendly_tz_label(tz_name, self.tz)
        raw = getattr(args, "gcs", None) or os.getenv("TARGET_CHATS") or os.getenv("TARGET_GROUP_CHATS", "")
        self.target_gcs = [g.strip() for g in raw.split(",") if g.strip()]
        kw_raw = getattr(args, "keywords", None)
        if kw_raw is None:
            kw_raw = os.getenv("KEYWORDS", "")
        self.keywords = [w.strip() for w in str(kw_raw).split(",") if w.strip()]
        # App mode: absolute /report URLs + link back to picker
        self.app_mode = bool(getattr(args, "app_mode", False))
        self.public_prefix = (getattr(args, "public_prefix", None) or "/report").rstrip("/") or "/report"
        # In the web app, date filters come only from the form (not leftover .env YEAR)
        self.since, self.until, self.filter_label = self._parse_date_filter(
            args, allow_env=not self.app_mode
        )
        export_raw = getattr(args, "export", None)
        if export_raw is None:
            export_raw = os.getenv("EXPORT", "csv,json")
        self.export_formats = self._parse_export(export_raw)

    @classmethod
    def for_app(cls, *, year=None, since=None, until=None, keywords=None, export=None):
        """Config for the local web app (picker-driven, no TARGET_GROUP_CHATS required)."""
        args = argparse.Namespace(
            db=None, outdir=None, gcs="", keywords=keywords,
            year=year, since=since, until=until, export=export,
            app_mode=True, public_prefix="/report",
        )
        return cls(args)

    def apply_runtime_filters(self, *, year=None, since=None, until=None, keywords=None, export=None):
        """Override date/keyword/export for one analyze request. Raises ValueError on bad input."""
        if keywords is not None:
            self.keywords = [w.strip() for w in str(keywords).split(",") if w.strip()]
        if export is not None:
            self.export_formats = self._parse_export(export)
        args = argparse.Namespace(year=year, since=since, until=until)
        # Clear env-based filter if request passes explicit empties via sentinel
        self.since, self.until, self.filter_label = self._parse_date_filter(args, allow_env=False)

    def _expand(self, p):
        return Path(p).expanduser().resolve()

    def _parse_export(self, raw):
        if raw is None:
            return {"csv", "json"}
        text = str(raw).strip().lower()
        if text in ("", "none", "off", "false", "0"):
            return set()
        parts = {p.strip() for p in text.split(",") if p.strip()}
        bad = parts - {"csv", "json"}
        if bad:
            raise ValueError(f"unknown EXPORT format(s): {', '.join(sorted(bad))} (use csv, json, or none)")
        return parts

    def _parse_date_filter(self, args, allow_env=True):
        year = getattr(args, "year", None)
        if year is None and allow_env:
            year_env = os.getenv("YEAR", "").strip()
            year = int(year_env) if year_env else None
        elif year is not None and year != "":
            year = int(year)
        else:
            year = None

        since_raw = getattr(args, "since", None)
        until_raw = getattr(args, "until", None)
        if allow_env:
            since_raw = since_raw or os.getenv("SINCE", "").strip() or None
            until_raw = until_raw or os.getenv("UNTIL", "").strip() or None
        else:
            since_raw = (since_raw or "").strip() or None
            until_raw = (until_raw or "").strip() or None

        since = until = None
        if year is not None:
            if since_raw or until_raw:
                raise ValueError("use YEAR alone, or SINCE/UNTIL — not both")
            since = self.tz.localize(datetime(int(year), 1, 1, 0, 0, 0))
            until = self.tz.localize(datetime(int(year) + 1, 1, 1, 0, 0, 0))
            return since, until, str(int(year))

        if since_raw:
            since = self.tz.localize(_parse_ymd(since_raw, "SINCE/--since"))
        if until_raw:
            day = _parse_ymd(until_raw, "UNTIL/--until")
            until = self.tz.localize(day + timedelta(days=1))

        if since and until and since >= until:
            raise ValueError("SINCE must be before UNTIL")

        if not since and not until:
            return None, None, None

        def fmt(ts):
            return ts.strftime("%b %-d, %Y")

        if since and until:
            label = f"{fmt(since)} – {fmt(until - timedelta(seconds=1))}"
        elif since:
            label = f"from {fmt(since)}"
        else:
            label = f"through {fmt(until - timedelta(seconds=1))}"
        return since, until, label


def _friendly_tz_label(tz_name, tz):
    if tz_name in ("America/Los_Angeles", "US/Pacific"):
        return "Pacific Time"
    if tz_name in ("America/New_York", "US/Eastern"):
        return "Eastern Time"
    if tz_name in ("America/Chicago", "US/Central"):
        return "Central Time"
    if tz_name in ("America/Denver", "US/Mountain"):
        return "Mountain Time"
    return tz.tzname(datetime.now())


URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
# Broad emoji / symbol ranges used in iMessage
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)
SILENCE_HOURS = 2  # gap that counts as a new conversation kickoff
REPLY_MAX_HOURS = 12  # ignore gaps longer than this as "replies"


def get_connection(db_path):
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("SELECT 1")
        return conn
    except sqlite3.OperationalError:
        print("Error: cannot read chat.db. Grant Full Disk Access to your terminal.", file=sys.stderr)
        sys.exit(1)


def load_chat_catalog(conn):
    """All chats with message counts and last activity (iMessage-style recency)."""
    return pd.read_sql_query(
        """
        SELECT c.ROWID AS chat_id,
               c.display_name AS display_name,
               c.chat_identifier AS chat_identifier,
               c.style AS style,
               COALESCE(c.is_filtered, 0) AS is_filtered,
               COALESCE(c.is_archived, 0) AS is_archived,
               COUNT(cmj.message_id) AS messages,
               MAX(m.date) AS last_date,
               (
                 SELECT COUNT(*) FROM chat_handle_join chj
                 WHERE chj.chat_id = c.ROWID
               ) AS handle_count,
               (
                 SELECT h.id FROM chat_handle_join chj
                 JOIN handle h ON h.ROWID = chj.handle_id
                 WHERE chj.chat_id = c.ROWID
                 LIMIT 1
               ) AS peer_id
        FROM chat c
        JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        JOIN message m ON m.ROWID = cmj.message_id
        WHERE COALESCE(c.is_filtered, 0) = 0
        GROUP BY c.ROWID
        HAVING messages > 0
        ORDER BY last_date DESC
        """,
        conn,
    )


def _looks_like_shortcode(identifier):
    """US-style short codes / blast SMS (usually spam or OTP), not a real person."""
    s = str(identifier or "").strip()
    if not s or "@" in s:
        return False
    digits = "".join(c for c in s if c.isdigit())
    return digits == s.replace("+", "").replace("-", "").replace(" ", "") and 4 <= len(digits) <= 6


def is_dm_row(row):
    """
    True only for real iMessage/SMS 1:1 threads.

    - style 45 = Instant Message (1:1)
    - exactly one handle on the chat
    - not a group room id (chat…)
    - not short-code / blast SMS
    - not Filtered Unknown Senders (is_filtered already excluded in catalog)
    """
    style = row.get("style")
    ident = str(row.get("chat_identifier") or "")
    peer = row.get("peer_id") or ident
    handles = int(row.get("handle_count") or 0)
    if style != 45:
        return False
    if handles != 1:
        return False
    if ident.startswith("chat"):
        return False
    if _looks_like_shortcode(peer) or _looks_like_shortcode(ident):
        return False
    return True


def is_group_row(row):
    """Named group chats only — skip unnamed rooms that would look like junk."""
    style = row.get("style")
    name = (row.get("display_name") or "").strip()
    if not name:
        return False
    # Prefer style 43 (group), but allow named non-DM chats
    if style == 45:
        return False
    ident = str(row.get("chat_identifier") or "")
    handles = int(row.get("handle_count") or 0)
    # Unnamed multi-person rooms are already excluded by empty name.
    # Drop 0-handle leftovers.
    if handles < 2 and not ident.startswith("chat"):
        # Odd named 1-handle non-DM — skip
        return False
    return True


def dm_labels(mapper, identifier):
    """Return (list_label, match_keys, resolved_full_name)."""
    ident = str(identifier or "").strip()
    resolved = mapper.resolve(ident) if ident else None
    first = canonical_first_name(resolved) if resolved else None
    keys = set()
    if ident:
        keys.add(ident.lower())
    if resolved:
        keys.add(resolved.lower())
        keys.add(canonical_first_name(resolved).lower())
        keys.add(person_identity_key(resolved))
    if first:
        keys.add(first.lower())
    label = first or resolved or ident or "Unknown"
    return label, keys, resolved


def contact_merge_key(mapper, identifier):
    """Same Contacts person → same key (merges phone + email threads)."""
    ident = str(identifier or "").strip()
    if not ident or ident.lower() == "nan":
        return None
    resolved = mapper.resolve(ident)
    if resolved:
        return "contact:" + person_identity_key(resolved)
    if "@" in ident:
        return "email:" + ident.lower()
    digits = normalize_phone(ident)
    if digits:
        return "phone:" + digits
    return "id:" + ident.lower()


def build_chat_index(conn, mapper, tz=None):
    """
    Catalog with Group vs 1:1 lists.

    1:1 threads that resolve to the same Contacts person (phone + email) are merged.
    Groups with the same display name are merged (iMessage often creates a new
    room id for the same named group).
    Both lists are sorted by last message time (newest first), like Messages.app.
    """
    catalog = load_chat_catalog(conn)
    group_buckets = {}
    dm_buckets = {}

    for _, row in catalog.iterrows():
        last_raw = row["last_date"]
        if is_dm_row(row):
            ident = row["peer_id"] or row["chat_identifier"]
            if ident is not None and (isinstance(ident, float) and pd.isna(ident)):
                ident = row["chat_identifier"]
            label, keys, resolved = dm_labels(mapper, ident)
            mkey = contact_merge_key(mapper, ident)
            if mkey is None:
                continue
            chat_id = int(row["chat_id"])
            msgs = int(row["messages"])
            ident_s = str(ident).strip() if ident is not None and str(ident) != "nan" else ""
            if mkey in dm_buckets:
                entry = dm_buckets[mkey]
                entry["chat_ids"].append(chat_id)
                entry["messages"] += msgs
                entry["keys"] |= keys
                if ident_s and ident_s not in entry["identifiers"]:
                    entry["identifiers"].append(ident_s)
                if last_raw is not None and not (isinstance(last_raw, float) and pd.isna(last_raw)):
                    if entry.get("last_date_raw") is None or last_raw > entry["last_date_raw"]:
                        entry["last_date_raw"] = last_raw
                        entry["chat_id"] = chat_id  # newest thread as primary
                if resolved and (
                    not entry.get("resolved")
                    or len(str(resolved)) >= len(str(entry.get("resolved") or ""))
                ):
                    entry["resolved"] = resolved
                    entry["label"] = canonical_first_name(resolved)
            else:
                dm_buckets[mkey] = {
                    "chat_id": chat_id,
                    "chat_ids": [chat_id],
                    "kind": "dm",
                    "label": label,
                    "resolved": resolved,
                    "identifier": ident_s or None,
                    "identifiers": [ident_s] if ident_s else [],
                    "messages": msgs,
                    "keys": set(keys),
                    "last_date_raw": last_raw if last_raw is not None and not (isinstance(last_raw, float) and pd.isna(last_raw)) else None,
                    "merge_key": mkey,
                }
        elif is_group_row(row):
            name = (row["display_name"] or "").strip()
            gkey = name.casefold()
            chat_id = int(row["chat_id"])
            msgs = int(row["messages"])
            if gkey in group_buckets:
                entry = group_buckets[gkey]
                entry["chat_ids"].append(chat_id)
                entry["messages"] += msgs
                if last_raw is not None and not (isinstance(last_raw, float) and pd.isna(last_raw)):
                    if entry.get("last_date_raw") is None or last_raw > entry["last_date_raw"]:
                        entry["last_date_raw"] = last_raw
                        entry["chat_id"] = chat_id
                        entry["label"] = name  # prefer casing from newest thread
            else:
                group_buckets[gkey] = {
                    "chat_id": chat_id,
                    "chat_ids": [chat_id],
                    "kind": "group",
                    "label": name,
                    "messages": msgs,
                    "keys": {name.lower()},
                    "last_date_raw": last_raw if last_raw is not None and not (isinstance(last_raw, float) and pd.isna(last_raw)) else None,
                }
        # else: spam, unnamed group rooms, odd leftovers — skip

    groups = list(group_buckets.values())
    dms = list(dm_buckets.values())

    used_dm_labels = defaultdict(int)
    for dm in dms:
        used_dm_labels[dm["label"].lower()] += 1
    for dm in dms:
        if used_dm_labels[dm["label"].lower()] <= 1:
            continue
        if dm.get("resolved"):
            dm["label"] = label_with_last_initial(dm["resolved"], canonical_first_name(dm["resolved"]))
        elif dm.get("identifiers"):
            short = str(dm["identifiers"][0])
            if len(short) > 18:
                short = short[:14] + "…"
            dm["label"] = f"{dm['label']} ({short})"
        dm["keys"].add(dm["label"].lower())

    label_counts = defaultdict(int)
    for dm in dms:
        label_counts[dm["label"].lower()] += 1
    for dm in dms:
        if label_counts[dm["label"].lower()] > 1 and dm.get("resolved"):
            dm["label"] = label_with_last_name(dm["resolved"], canonical_first_name(dm["resolved"]))
            dm["keys"].add(dm["label"].lower())

    # Attach human-readable last activity when tz known
    if tz is not None:
        for c in groups + dms:
            raw = c.get("last_date_raw")
            if raw is None:
                c["last_active"] = None
                c["last_active_label"] = ""
            else:
                dt = convert_timestamp(raw, tz)
                c["last_active"] = dt
                c["last_active_label"] = format_last_active(dt, tz) if dt is not None else ""

    def sort_key(c):
        raw = c.get("last_date_raw")
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    groups.sort(key=sort_key, reverse=True)
    dms.sort(key=sort_key, reverse=True)
    return groups, dms


def format_last_active(dt, tz):
    """Short recency label similar to Messages (Today / Yesterday / date)."""
    if dt is None or pd.isna(dt):
        return ""
    now = datetime.now(tz)
    local = dt.astimezone(tz) if getattr(dt, "tzinfo", None) else tz.localize(dt.to_pydatetime() if hasattr(dt, "to_pydatetime") else dt)
    today = now.date()
    d = local.date()
    if d == today:
        return local.strftime("%-I:%M %p")
    if d == today - timedelta(days=1):
        return "Yesterday"
    if (today - d).days < 7:
        return local.strftime("%a")
    if local.year == now.year:
        return local.strftime("%b %-d")
    return local.strftime("%b %-d, %Y")


def print_chat_list(groups, dms):
    print("GROUP CHATS (newest first)")
    print(f"{'Last':>12}  {'Messages':>10}  Name")
    print("-" * 60)
    if not groups:
        print("  (none)")
    for g in groups:
        last = g.get("last_active_label") or "—"
        print(f"{last:>12}  {g['messages']:>10,}  {g['label']}")

    print("\nONE-ON-ONE (newest first · phone+email merged by Contacts)")
    print(f"{'Last':>12}  {'Messages':>10}  Name")
    print("-" * 60)
    if not dms:
        print("  (none)")
    for d in dms[:80]:
        last = d.get("last_active_label") or "—"
        extra = ""
        if len(d.get("chat_ids") or []) > 1:
            extra = f"  [{len(d['chat_ids'])} threads]"
        print(f"{last:>12}  {d['messages']:>10,}  {d['label']}{extra}")
    if len(dms) > 80:
        print(f"  … and {len(dms) - 80} more")

    print("\nCopy names into TARGET_GROUP_CHATS in .env (comma-separated).")
    print("Groups and 1:1 chats both work. Spelling must match the Name column.")
    print("Or run `python app.py` and pick a chat in the browser.")


def find_chat_by_id(conn, mapper, chat_id, tz=None):
    groups, dms = build_chat_index(conn, mapper, tz=tz)
    chat_id = int(chat_id)
    for c in groups + dms:
        ids = c.get("chat_ids") or [c["chat_id"]]
        if chat_id in ids or c["chat_id"] == chat_id:
            return c
    return None


def kind_subtitle(kind, chat_name, member_count, your_name):
    if kind == "dm":
        return "1:1", f"1:1 · {your_name} & {chat_name}"
    return "Group", f"Group · {member_count} people"


def find_chats(conn, names, mapper, tz=None):
    groups, dms = build_chat_index(conn, mapper, tz=tz)
    catalog = groups + dms
    found = {}
    for name in names:
        needle = name.strip().lower()
        if not needle:
            continue
        matches = [c for c in catalog if needle in c["keys"] or needle == c["label"].lower()]
        if not matches:
            matches = [c for c in catalog if needle in c["label"].lower()]
        if not matches:
            print(f"  Warning: no chat named {name!r}")
            close = [c for c in catalog if needle[:4] in c["label"].lower()][:5] if len(needle) >= 4 else []
            if close:
                print("    Did you mean:")
                for c in close:
                    print(f"      - {c['label']}")
            continue
        exact = [c for c in matches if needle in c["keys"] or needle == c["label"].lower()]
        pick = max(exact or matches, key=lambda c: c.get("last_date_raw") or 0)
        title = pick["label"]
        if title in found and found[title]["chat_id"] != pick["chat_id"]:
            title = f"{title} · {pick['chat_id']}"
        found[title] = {
            "chat_id": pick["chat_id"],
            "chat_ids": pick.get("chat_ids") or [pick["chat_id"]],
            "kind": pick["kind"],
        }
    return found


def convert_timestamp(value, tz):
    if pd.isna(value):
        return None
    origin = pd.Timestamp("2001-01-01")
    unit = "ns" if abs(value) > 1e12 else "s"
    dt = pd.to_datetime(value, unit=unit, origin=origin)
    return dt.tz_localize("UTC").tz_convert(tz)


def message_body(text, blob):
    """Plaintext from message.text, or a best-effort pull from attributedBody."""
    if isinstance(text, str) and text.strip():
        return text.strip()
    if blob is None or (isinstance(blob, float) and pd.isna(blob)):
        return ""
    data = blob if isinstance(blob, (bytes, bytearray)) else bytes(blob)
    start = data.find(b"NSString")
    chunk = data[start + 8:] if start >= 0 else data
    if start >= 0 and start + 14 < len(data):
        length = data[start + 13]
        piece = data[start + 14:start + 14 + length]
        if 1 <= length <= 127 and _looks_like_text(piece):
            try:
                return piece.decode("utf-8").strip()
            except UnicodeDecodeError:
                pass
    raw = chunk.decode("utf-8", errors="ignore")
    if "NSDictionary" in raw:
        raw = raw.split("NSDictionary", 1)[0]
    raw = raw.replace("\ufffc", "").replace("\ufffd", "")
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    raw = re.sub(r"^[^A-Za-z0-9@#\"'“”]+", "", raw)
    return raw.strip()


def _looks_like_text(piece):
    if not piece:
        return False
    printable = sum(32 <= b < 127 or b in (9, 10, 13) or b >= 128 for b in piece)
    return printable / len(piece) > 0.7


def compile_keywords(words):
    return [
        (w, re.compile(r"(?<!\w)" + re.escape(w) + r"(?!\w)", re.IGNORECASE))
        for w in words
    ]


def extract_guid(raw):
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if ":" in s:
        s = s.split(":", 1)[-1]
    return s.strip() or None


def load_chat(conn, chat_id, tz):
    """Load messages + tapbacks for one chat_id or a list of chat_ids (merged 1:1)."""
    if isinstance(chat_id, (list, tuple, set)):
        ids = [int(x) for x in chat_id]
    else:
        ids = [int(chat_id)]
    id_list = ",".join(str(i) for i in ids)

    messages = pd.read_sql_query(
        f"""
        SELECT h.id AS handle_id, m.is_from_me, m.date, m.guid AS message_guid,
               m.associated_message_type, m.text, m.attributedBody
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        WHERE cmj.chat_id IN ({id_list})
        """,
        conn,
    )
    messages["datetime"] = messages["date"].apply(lambda v: convert_timestamp(v, tz))
    messages = messages[messages["datetime"].dt.year >= 2005].copy()
    # Dedupe same message linked into multiple chats
    if "message_guid" in messages.columns and not messages.empty:
        messages = messages.drop_duplicates(subset=["message_guid"], keep="first")
    types = messages["associated_message_type"]
    real = messages[types.isna() | ~types.isin(list(TAPBACK_RANGE) + list(REMOVE_RANGE))].copy()
    real["body"] = [
        message_body(t, b) for t, b in zip(real["text"], real["attributedBody"])
    ]
    real = real.drop(columns=["text", "attributedBody"], errors="ignore")

    reactions = pd.read_sql_query(
        f"""
        SELECT m.associated_message_guid, m.associated_message_type,
               h.id AS reactor_handle_id, m.is_from_me AS reactor_is_me, m.date,
               m.guid AS reaction_guid
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        WHERE cmj.chat_id IN ({id_list})
          AND m.associated_message_guid IS NOT NULL
          AND m.associated_message_type BETWEEN 2000 AND 2007
        """,
        conn,
    )
    if not reactions.empty:
        reactions["datetime"] = reactions["date"].apply(lambda v: convert_timestamp(v, tz))
        if "reaction_guid" in reactions.columns:
            reactions = reactions.drop_duplicates(subset=["reaction_guid"], keep="first")
    return real, reactions


def apply_date_filter(messages, reactions, since, until):
    """Keep rows in [since, until)."""
    if since is None and until is None:
        return messages, reactions
    if not messages.empty:
        mask = pd.Series(True, index=messages.index)
        if since is not None:
            mask &= messages["datetime"] >= since
        if until is not None:
            mask &= messages["datetime"] < until
        messages = messages.loc[mask].copy()
    if reactions is not None and not reactions.empty and "datetime" in reactions.columns:
        mask = pd.Series(True, index=reactions.index)
        if since is not None:
            mask &= reactions["datetime"] >= since
        if until is not None:
            mask &= reactions["datetime"] < until
        reactions = reactions.loc[mask].copy()
    return messages, reactions


def longest_streak(dates):
    if not dates:
        return 0
    ordered = sorted(dates)
    best = cur = 1
    for i in range(1, len(ordered)):
        if (ordered[i] - ordered[i - 1]).days == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def compute_stats(messages, reactions, mapper, keywords=None):
    if messages.empty:
        return {"members": [], "chat_info": {}}

    guid_to_name = {}
    timed = []

    def blank():
        return {
            "message_count": 0,
            "tapbacks_got": 0,
            "tapbacks_given": 0,
            "haha_got": 0,
            "haha_given": 0,
            "loved_got": 0,
            "loved_given": 0,
            "liked_got": 0,
            "liked_given": 0,
            "emphasized_got": 0,
            "questioned_got": 0,
            "first": None,
            "last": None,
            "hours": defaultdict(int),
            "weekdays": defaultdict(int),
            "dates": set(),
            "day_counts": defaultdict(int),
            "night": 0,
            "morning": 0,
            "weekend": 0,
            "recent": 0,
            "days_opened": 0,
            "days_closed": 0,
            "followups": 0,
            "questions": 0,
            "links": 0,
            "emoji_count": 0,
            "emoji_msgs": 0,
            "kickoffs": 0,
            "bursts": 0,
            "reply_gaps": [],
        }

    stats = defaultdict(blank)
    hours = defaultdict(int)
    weekdays = defaultdict(int)
    skipped = 0
    end = messages["datetime"].max()
    recent_cut = end - pd.Timedelta(days=30) if pd.notna(end) else None
    kw_patterns = compile_keywords(keywords or [])
    kw_hits = defaultdict(lambda: defaultdict(int))
    kw_msgs = defaultdict(lambda: defaultdict(int))
    readable = 0

    handle_pairs = []
    for _, row in messages.iterrows():
        handle_pairs.append((row["handle_id"], bool(row["is_from_me"])))
    for _, row in reactions.iterrows():
        handle_pairs.append((row["reactor_handle_id"], bool(row["reactor_is_me"])))
    label_of = build_chat_labeler(mapper, handle_pairs)

    for _, row in messages.iterrows():
        name = label_of(row["handle_id"], bool(row["is_from_me"]))
        if not name:
            skipped += 1
            continue
        if pd.notna(row["message_guid"]):
            g = str(row["message_guid"])
            guid_to_name[g] = name
            extracted = extract_guid(g)
            if extracted:
                guid_to_name[extracted] = name
        stats[name]["message_count"] += 1
        dt = row["datetime"]
        if pd.isna(dt):
            continue
        timed.append((dt, name))
        hour = int(dt.hour)
        wd = int(dt.weekday())
        day = dt.date()
        hours[hour] += 1
        weekdays[wd] += 1
        stats[name]["hours"][hour] += 1
        stats[name]["weekdays"][wd] += 1
        stats[name]["dates"].add(day)
        stats[name]["day_counts"][day] += 1
        if hour >= 22 or hour < 5:
            stats[name]["night"] += 1
        if 5 <= hour < 10:
            stats[name]["morning"] += 1
        if wd >= 5:
            stats[name]["weekend"] += 1
        if recent_cut is not None and dt >= recent_cut:
            stats[name]["recent"] += 1
        if stats[name]["first"] is None or dt < stats[name]["first"]:
            stats[name]["first"] = dt
        if stats[name]["last"] is None or dt > stats[name]["last"]:
            stats[name]["last"] = dt
        body = row["body"] if "body" in row.index and isinstance(row["body"], str) else ""
        if body:
            readable += 1
            for word, pat in kw_patterns:
                found = pat.findall(body)
                if found:
                    kw_hits[name][word] += len(found)
                    kw_msgs[name][word] += 1
            if "?" in body:
                stats[name]["questions"] += 1
            if URL_RE.search(body):
                stats[name]["links"] += 1
            emojis = EMOJI_RE.findall(body)
            if emojis:
                stats[name]["emoji_msgs"] += 1
                stats[name]["emoji_count"] += sum(len(e) for e in emojis)

    timed.sort(key=lambda x: x[0])
    by_day = defaultdict(list)
    prev = None
    prev_dt = None
    run_len = 0
    silence = pd.Timedelta(hours=SILENCE_HOURS)
    reply_cap = pd.Timedelta(hours=REPLY_MAX_HOURS)
    for dt, name in timed:
        by_day[dt.date()].append(name)
        if prev_dt is None or (dt - prev_dt) >= silence:
            stats[name]["kickoffs"] += 1
        if name == prev:
            stats[name]["followups"] += 1
            run_len += 1
            if run_len == 3:
                stats[name]["bursts"] += 1
        else:
            if prev is not None and prev_dt is not None:
                gap = dt - prev_dt
                if pd.Timedelta(0) < gap <= reply_cap:
                    stats[name]["reply_gaps"].append(gap.total_seconds())
            run_len = 1
        prev = name
        prev_dt = dt
    for names in by_day.values():
        stats[names[0]]["days_opened"] += 1
        stats[names[-1]]["days_closed"] += 1

    matched = 0
    for _, row in reactions.iterrows():
        kind = REACT_LABEL.get(int(row["associated_message_type"]) if pd.notna(row["associated_message_type"]) else -1, "other")
        giver = label_of(row["reactor_handle_id"], bool(row["reactor_is_me"]))
        if giver:
            stats[giver]["tapbacks_given"] += 1
            if kind == "haha":
                stats[giver]["haha_given"] += 1
            elif kind == "loved":
                stats[giver]["loved_given"] += 1
            elif kind == "liked":
                stats[giver]["liked_given"] += 1

        key = extract_guid(row["associated_message_guid"])
        raw = str(row["associated_message_guid"]) if pd.notna(row["associated_message_guid"]) else None
        receiver = guid_to_name.get(key) or guid_to_name.get(raw)
        if not receiver:
            continue
        matched += 1
        stats[receiver]["tapbacks_got"] += 1
        if kind == "haha":
            stats[receiver]["haha_got"] += 1
        elif kind == "loved":
            stats[receiver]["loved_got"] += 1
        elif kind == "liked":
            stats[receiver]["liked_got"] += 1
        elif kind == "emphasized":
            stats[receiver]["emphasized_got"] += 1
        elif kind == "questioned":
            stats[receiver]["questioned_got"] += 1

    print(f"  Skipped {skipped} unnamed messages")
    print(f"  Matched {matched}/{len(reactions)} tapbacks to a sender")
    if keywords:
        print(f"  Readable text on {readable:,}/{len(messages):,} messages")

    total = sum(s["message_count"] for s in stats.values())
    start = messages["datetime"].min()
    chat_days = (end - start).days + 1 if pd.notna(start) and pd.notna(end) else 1
    members = []
    for name, s in stats.items():
        n = s["message_count"]
        if n == 0 and s["tapbacks_given"] == 0:
            continue
        span = 1
        if s["first"] is not None and s["last"] is not None:
            span = max((s["last"] - s["first"]).days + 1, 1)
        peak_hour = max(range(24), key=lambda h: s["hours"].get(h, 0)) if s["hours"] else 0
        peak_wd = max(range(7), key=lambda d: s["weekdays"].get(d, 0)) if s["weekdays"] else 0
        days_active = len(s["dates"])
        peak_day = max(s["day_counts"].values()) if s["day_counts"] else 0
        got, given = s["tapbacks_got"], s["tapbacks_given"]
        members.append({
            "name": name,
            "message_count": n,
            "percentage": (n / total * 100) if total else 0,
            "avg_per_day": n / span if n else 0,
            "tapbacks_got": got,
            "tapbacks_given": given,
            "tapbacks_per_100": (got / n * 100) if n else 0,
            "given_per_100": (given / n * 100) if n else 0,
            "balance": (given / got) if got else (given if given else 0),
            "haha_got": s["haha_got"],
            "haha_given": s["haha_given"],
            "haha_per_100": (s["haha_got"] / n * 100) if n else 0,
            "loved_got": s["loved_got"],
            "loved_given": s["loved_given"],
            "loved_per_100": (s["loved_got"] / n * 100) if n else 0,
            "liked_got": s["liked_got"],
            "liked_given": s["liked_given"],
            "emphasized_got": s["emphasized_got"],
            "peak_hour": hour_label(peak_hour),
            "peak_weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][peak_wd],
            "night_pct": (s["night"] / n * 100) if n else 0,
            "morning_pct": (s["morning"] / n * 100) if n else 0,
            "weekend_pct": (s["weekend"] / n * 100) if n else 0,
            "night_count": s["night"],
            "morning_count": s["morning"],
            "weekend_count": s["weekend"],
            "liked_per_100": (s["liked_got"] / n * 100) if n else 0,
            "questioned_got": s["questioned_got"],
            "questioned_per_100": (s["questioned_got"] / n * 100) if n else 0,
            "tenure_days": (end - s["first"]).days + 1 if s["first"] is not None and pd.notna(end) else 0,
            "silent_days": (end - s["last"]).days if s["last"] is not None and pd.notna(end) else 0,
            "first_label": s["first"].strftime("%b %-d, %Y") if s["first"] is not None else "—",
            "last_label": s["last"].strftime("%b %-d, %Y") if s["last"] is not None else "—",
            "days_active": days_active,
            "presence": (days_active / chat_days * 100) if chat_days else 0,
            "streak": longest_streak(s["dates"]),
            "peak_day": peak_day,
            "days_opened": s["days_opened"],
            "days_closed": s["days_closed"],
            "open_pct": (s["days_opened"] / len(by_day) * 100) if by_day else 0,
            "close_pct": (s["days_closed"] / len(by_day) * 100) if by_day else 0,
            "followups": s["followups"],
            "followup_pct": (s["followups"] / n * 100) if n else 0,
            "recent": s["recent"],
            "recent_pct": (s["recent"] / n * 100) if n else 0,
            "questions": s["questions"],
            "question_pct": (s["questions"] / n * 100) if n else 0,
            "links": s["links"],
            "link_pct": (s["links"] / n * 100) if n else 0,
            "emoji_count": s["emoji_count"],
            "emoji_msgs": s["emoji_msgs"],
            "emoji_per_100": (s["emoji_count"] / n * 100) if n else 0,
            "kickoffs": s["kickoffs"],
            "kickoff_pct": (s["kickoffs"] / n * 100) if n else 0,
            "bursts": s["bursts"],
            "burst_pct": (s["bursts"] / n * 100) if n else 0,
            "reply_count": len(s["reply_gaps"]),
            "reply_median_min": (
                float(pd.Series(s["reply_gaps"]).median() / 60.0) if s["reply_gaps"] else None
            ),
        })

    max_hour = max(hours.values()) if hours else 1
    hour_bars = []
    for h in range(24):
        count = hours.get(h, 0)
        hour_bars.append({
            "label": hour_label(h),
            "count": count,
            "height": round(count / max_hour * 100, 1) if max_hour else 0,
            "show_label": h % 3 == 0,
        })
    wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    max_wd = max(weekdays.values()) if weekdays else 1
    weekday_bars = [
        {
            "label": wd_names[d],
            "count": weekdays.get(d, 0),
            "height": round(weekdays.get(d, 0) / max_wd * 100, 1) if max_wd else 0,
            "show_label": True,
        }
        for d in range(7)
    ]

    keyword_boards = []
    for word in keywords or []:
        rows = []
        total_hits = total_kw_msgs = 0
        for m in members:
            hits = kw_hits[m["name"]].get(word, 0)
            uses = kw_msgs[m["name"]].get(word, 0)
            total_hits += hits
            total_kw_msgs += uses
            item = dict(m)
            item["kw_hits"] = hits
            item["kw_msgs"] = uses
            item["kw_per_100"] = (hits / m["message_count"] * 100) if m["message_count"] else 0
            rows.append(item)
        keyword_boards.append({
            "word": word,
            "total": total_hits,
            "messages": total_kw_msgs,
            "members": ranked(rows, "kw_hits"),
        })

    return {
        "members": members,
        "hours": hour_bars,
        "weekdays": weekday_bars,
        "keyword_boards": keyword_boards,
        "chat_info": {
            "total_messages": total,
            "member_count": len(members),
            "start_date": start,
            "end_date": end,
            "total_days": chat_days,
        },
    }


# id, label, file, one-line hint
CORE_PAGES = [
    ("messages", "Messages", None, "Who sent the most texts"),
    ("received", "Received", "received", "Tapbacks on their messages"),
    ("given", "Given", "given", "Tapbacks they put on others"),
    ("pace", "Pace", "pace", "Average messages per day"),
    ("when", "When", "when", "What hour they text"),
]

EXTRA_GROUPS = [
    ("Tapbacks", [
        ("haha", "Haha", "haha", "😂 received"),
        ("hearts", "Hearts", "hearts", "❤️ received"),
        ("likes", "Likes", "likes", "👍 received"),
        ("huh", "Huh", "huh", "❓ received"),
        ("magnet", "Magnet", "magnet", "Tapbacks per message they sent"),
        ("balance", "Balance", "balance", "Given ÷ received"),
    ]),
    ("Consistency", [
        ("streaks", "Streaks", "streaks", "Longest run of daily texts"),
        ("presence", "Presence", "presence", "Distinct days they showed up"),
        ("peaks", "Peaks", "peaks", "Most messages in one day"),
        ("recent", "Recent", "recent", "Last 30 days"),
        ("rambles", "Rambles", "rambles", "Back-to-back texts"),
        ("bursts", "Bursts", "bursts", "Streaks of 3+ texts in a row"),
    ]),
    ("Timing", [
        ("openers", "Openers", "openers", "First text of each day"),
        ("closers", "Closers", "closers", "Last text of each day"),
        ("kickoffs", "Kickoffs", "kickoffs", f"First text after {SILENCE_HOURS}h+ silence"),
        ("replies", "Replies", "replies", "Fastest median reply time"),
        ("nights", "Nights", "nights", "Share sent 10pm–5am"),
        ("mornings", "Mornings", "mornings", "Share sent 5–10am"),
        ("weekends", "Weekends", "weekends", "Share sent Sat–Sun"),
        ("week", "Week", "week", "Messages by weekday"),
    ]),
    ("Content", [
        ("links", "Links", "links", "Texts with a URL"),
        ("emoji", "Emoji", "emoji", "Emoji characters sent"),
    ]),
    ("Tenure", [
        ("veterans", "Veterans", "veterans", "Longest time in the chat"),
        ("ghosts", "Ghosts", "ghosts", "Days since their last text"),
    ]),
]


def page_filename(file_id):
    return "index.html" if file_id is None else f"{file_id}.html"


def all_page_tuples(keywords=None):
    pages = [(k, label, fid) for k, label, fid, _hint in CORE_PAGES]
    for _group, items in EXTRA_GROUPS:
        pages.extend((k, label, fid) for k, label, fid, _hint in items)
    if keywords:
        pages.append(("keywords", "Keywords", "keywords"))
    return pages


def nav_structure(keywords=None):
    core = [
        {"id": k, "label": label, "file": fid, "hint": hint, "key": f"{k}_url"}
        for k, label, fid, hint in CORE_PAGES
    ]
    extra = []
    for group, items in EXTRA_GROUPS:
        extra.append({
            "label": group,
            "links": [
                {"id": k, "label": label, "file": fid, "hint": hint, "key": f"{k}_url"}
                for k, label, fid, hint in items
            ],
        })
    if keywords:
        extra.append({
            "label": "Words",
            "links": [{
                "id": "keywords",
                "label": "Keywords",
                "file": "keywords",
                "hint": "Who said each word you picked",
                "key": "keywords_url",
            }],
        })
    return core, extra


def chat_urls(folder, pages, relative=False):
    urls = {}
    for key, _label, file_id in pages:
        name = page_filename(file_id)
        urls[f"{key}_url"] = name if relative else f"{folder}/{name}"
    return urls


def ranked(members, key, ascending=False):
    def sort_key(m):
        v = m.get(key)
        if v is None:
            return (1, 0)
        return (0, v if ascending else -v)

    rows = sorted(members, key=sort_key)
    values = [m[key] for m in rows if m.get(key) is not None]
    if ascending:
        best = min(values) if values else 1
    else:
        best = max(values) if values else 1
    out = []
    for i, m in enumerate(rows, 1):
        item = dict(m)
        item["rank"] = i
        v = m.get(key)
        if v is None or not best:
            item["bar_pct"] = 0
        elif ascending:
            item["bar_pct"] = (best / v * 100) if v else 0
        else:
            item["bar_pct"] = (v / best * 100) if best else 0
        out.append(item)
    return out


def analyze_chats(conn, mapper, chat_map, config, quiet=False):
    """
    Load, filter, score, and render chats.

    chat_map: {display_name: chat_id} or {display_name: {"chat_id": int, "chat_ids": [...], "kind": ...}}
    Returns (index_path, exports, all_data).
    """
    all_data = {}
    for name, spec in chat_map.items():
        if isinstance(spec, dict):
            chat_ids = spec.get("chat_ids") or [spec["chat_id"]]
            kind = spec.get("kind") or "group"
        else:
            chat_ids = [int(spec)]
            kind = "group"
        if not quiet:
            print(f"\n{name}")
            if len(chat_ids) > 1:
                print(f"  Merged {len(chat_ids)} threads (phone/email)")
        messages, reactions = load_chat(conn, chat_ids, config.tz)
        before = len(messages)
        messages, reactions = apply_date_filter(messages, reactions, config.since, config.until)
        if not quiet:
            if config.filter_label:
                print(f"  {len(messages):,}/{before:,} messages in range, {len(reactions):,} tapbacks")
            else:
                print(f"  {len(messages):,} messages, {len(reactions):,} tapbacks")
        if messages.empty:
            if not quiet:
                print("  Skipping — no messages in this date range")
            continue
        payload = compute_stats(messages, reactions, mapper, config.keywords)
        badge, subtitle = kind_subtitle(
            kind, name, payload["chat_info"]["member_count"], config.your_name
        )
        payload["chat_info"]["kind"] = kind
        payload["chat_info"]["kind_badge"] = badge
        payload["chat_info"]["kind_subtitle"] = subtitle
        all_data[name] = payload
        if not quiet:
            print(f"  {payload['chat_info']['member_count']} people · {badge}")

    if not all_data:
        raise ValueError("No chats had messages in the selected range.")
    out, exports = render(all_data, config)
    return out, exports, all_data


def wipe_output(output_dir):
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def _json_safe(value):
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def members_table(chat_name, members):
    rows = []
    for m in members:
        row = {"chat": chat_name}
        for key in EXPORT_FIELDS:
            row[key] = _json_safe(m.get(key))
        rows.append(row)
    return rows


def write_exports(all_data, config):
    if not config.export_formats:
        return []
    export_dir = config.output_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    written = []
    all_rows = []
    chat_rows = []

    for chat_name, payload in all_data.items():
        folder = slug(chat_name)
        chat_dir = config.output_dir / folder
        chat_dir.mkdir(parents=True, exist_ok=True)
        info = payload.get("chat_info", {})
        members = payload.get("members", [])
        rows = members_table(chat_name, members)
        all_rows.extend(rows)
        chat_rows.append({
            "chat": chat_name,
            "folder": folder,
            "total_messages": info.get("total_messages", 0),
            "member_count": info.get("member_count", 0),
            "start_date": _json_safe(info.get("start_date")),
            "end_date": _json_safe(info.get("end_date")),
            "filter": config.filter_label,
        })

        payload_out = {
            "chat": chat_name,
            "filter": config.filter_label,
            "timezone": config.tz_label,
            "chat_info": {k: _json_safe(v) for k, v in info.items()},
            "members": [{k: _json_safe(m.get(k)) for k in EXPORT_FIELDS} for m in members],
            "keywords": [
                {
                    "word": b["word"],
                    "total": b["total"],
                    "messages": b["messages"],
                    "members": [
                        {
                            "name": m["name"],
                            "kw_hits": m.get("kw_hits", 0),
                            "kw_msgs": m.get("kw_msgs", 0),
                            "kw_per_100": _json_safe(m.get("kw_per_100")),
                        }
                        for m in b.get("members", [])
                    ],
                }
                for b in payload.get("keyword_boards", [])
            ],
        }

        if "csv" in config.export_formats:
            path = chat_dir / "stats.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["chat"] + EXPORT_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            written.append(path)
        if "json" in config.export_formats:
            path = chat_dir / "stats.json"
            path.write_text(json.dumps(payload_out, indent=2))
            written.append(path)

    if "csv" in config.export_formats:
        path = export_dir / "members.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["chat"] + EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(all_rows)
        written.append(path)
        path = export_dir / "chats.csv"
        fields = ["chat", "folder", "total_messages", "member_count", "start_date", "end_date", "filter"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(chat_rows)
        written.append(path)
    if "json" in config.export_formats:
        path = export_dir / "all.json"
        path.write_text(json.dumps({
            "filter": config.filter_label,
            "timezone": config.tz_label,
            "generated": datetime.now(config.tz).isoformat(),
            "chats": chat_rows,
            "members": all_rows,
        }, indent=2))
        written.append(path)
    return written


def render(all_data, config):
    wipe_output(config.output_dir)
    css_dir = config.output_dir / "css"
    css_dir.mkdir(parents=True, exist_ok=True)
    (css_dir / "style.css").write_text(CSS)
    nav = all_page_tuples(config.keywords)
    core_meta, extra_meta = nav_structure(config.keywords)
    core_ids = {item["id"] for item in core_meta}

    chats = []
    for chat_name, payload in all_data.items():
        folder = slug(chat_name)
        info = payload["chat_info"]
        if config.app_mode:
            messages_url = f"{config.public_prefix}/{folder}/index.html"
            urls = {
                f"{key}_url": (
                    f"{config.public_prefix}/{folder}/{page_filename(file_id)}"
                )
                for key, _label, file_id in nav
            }
        else:
            messages_url = f"{folder}/index.html"
            urls = chat_urls(folder, nav, relative=False)
        chats.append({
            "name": chat_name,
            "folder": folder,
            "messages_url": messages_url,
            **urls,
            "total_messages": info["total_messages"],
            "member_count": info["member_count"],
            "start_date": info["start_date"],
            "end_date": info["end_date"],
            "kind": info.get("kind", "group"),
            "kind_badge": info.get("kind_badge", "Group"),
            "kind_subtitle": info.get("kind_subtitle", ""),
            "csv_url": (
                f"{config.public_prefix}/{folder}/stats.csv" if config.app_mode else f"{folder}/stats.csv"
            ),
            "json_url": (
                f"{config.public_prefix}/{folder}/stats.json" if config.app_mode else f"{folder}/stats.json"
            ),
        })

    generated = datetime.now(config.tz)
    tz_label = config.tz_label
    home_href = "/" if config.app_mode else None
    home_label = "Pick chat" if config.app_mode else "All chats"
    css_page = f"{config.public_prefix}/css/style.css" if config.app_mode else "../css/style.css"
    css_index = f"{config.public_prefix}/css/style.css" if config.app_mode else "css/style.css"
    pages = {
        "messages": {
            "page": "messages", "file": None, "title": "Messages sent", "kicker": "Volume",
            "blurb": "Who sent the most texts. Bar length is relative to the #1 person.",
            "value_key": "message_count", "value_fmt": "int",
            "share_key": "percentage", "share_suffix": "% of all messages",
            "simple": True, "cols": [],
        },
        "received": {
            "page": "received", "file": "received", "title": "Tapbacks received", "kicker": "Incoming",
            "blurb": "Tapbacks on their messages, including Haha, Love, Like, and !! . Per 100 = received ÷ messages sent × 100.",
            "value_key": "tapbacks_got", "value_fmt": "int",
            "share_key": "tapbacks_per_100", "share_suffix": "tapbacks / 100 messages they sent",
            "cols": [
                ("tapbacks_got", "int", "Tapbacks received"),
                ("tapbacks_per_100", "num", "Per 100 messages"),
                ("haha_got", "int", "Haha 😂"),
                ("loved_got", "int", "Love ❤️"),
                ("liked_got", "int", "Like 👍"),
                ("emphasized_got", "int", "Emphasized !!"),
            ],
        },
        "given": {
            "page": "given", "file": "given", "title": "Tapbacks given", "kicker": "Outgoing",
            "blurb": "Tapbacks they put on other people’s messages, including Haha.",
            "value_key": "tapbacks_given", "value_fmt": "int",
            "share_key": "given_per_100", "share_suffix": "given / 100 messages they sent",
            "cols": [
                ("tapbacks_given", "int", "Tapbacks given"),
                ("given_per_100", "num", "Given / 100 msgs"),
                ("haha_given", "int", "Haha given"),
                ("loved_given", "int", "Love given"),
                ("liked_given", "int", "Like given"),
            ],
        },
        "haha": {
            "page": "haha", "file": "haha", "title": "Haha received", "kicker": "Funny",
            "blurb": "😂 / Haha tapbacks on their messages. Per 100 = hahas received ÷ messages they sent × 100.",
            "value_key": "haha_got", "value_fmt": "int",
            "share_key": "haha_per_100", "share_suffix": "hahas / 100 messages they sent",
            "cols": [
                ("haha_got", "int", "Haha received"),
                ("haha_per_100", "num", "Haha / 100 msgs"),
                ("haha_given", "int", "Haha given"),
            ],
        },
        "hearts": {
            "page": "hearts", "file": "hearts", "title": "Love received", "kicker": "Hearts",
            "blurb": "❤️ tapbacks on their messages.",
            "value_key": "loved_got", "value_fmt": "int",
            "share_key": "loved_per_100", "share_suffix": "loves / 100 messages they sent",
            "cols": [
                ("loved_got", "int", "Love received"),
                ("loved_per_100", "num", "Love / 100 msgs"),
                ("loved_given", "int", "Love given"),
            ],
        },
        "magnet": {
            "page": "magnet", "file": "magnet", "title": "Tapback magnet", "kicker": "Engagement",
            "blurb": "Who gets the most tapbacks per message they send. High = fewer texts, more reactions.",
            "value_key": "tapbacks_per_100", "value_fmt": "num",
            "share_key": "tapbacks_got", "share_suffix": "tapbacks received", "share_fmt": "int",
            "cols": [
                ("tapbacks_per_100", "num", "Tapbacks / 100 msgs"),
                ("tapbacks_got", "int", "Tapbacks received"),
                ("message_count", "int", "Messages sent"),
            ],
        },
        "balance": {
            "page": "balance", "file": "balance", "title": "Give vs get", "kicker": "Balance",
            "blurb": "Tapbacks given ÷ tapbacks received. Over 1.0 means they react more than they get reacted to.",
            "value_key": "balance", "value_fmt": "num",
            "share_key": "tapbacks_given", "share_suffix": "tapbacks given", "share_fmt": "int",
            "cols": [
                ("balance", "num", "Given / received"),
                ("tapbacks_given", "int", "Given"),
                ("tapbacks_got", "int", "Received"),
            ],
        },
        "pace": {
            "page": "pace", "file": "pace", "title": "Messages per day", "kicker": "Pace",
            "blurb": f"Average texts per day from their first message in this chat to their last. {tz_label}.",
            "value_key": "avg_per_day", "value_fmt": "num",
            "share_key": "percentage", "share_suffix": "% of chat",
            "cols": [
                ("avg_per_day", "num", "Messages / day"),
                ("message_count", "int", "Total messages"),
                ("percentage", "pct", "Share of chat"),
            ],
        },
        "streaks": {
            "page": "streaks", "file": "streaks", "title": "Longest daily streak", "kicker": "Consistency",
            "blurb": "Longest run of consecutive calendar days with at least one message.",
            "value_key": "streak", "value_fmt": "int",
            "share_key": "days_active", "share_suffix": "days active", "share_fmt": "int",
            "cols": [
                ("streak", "int", "Longest streak (days)"),
                ("days_active", "int", "Days active"),
                ("presence", "pct", "Share of chat lifespan"),
            ],
        },
        "presence": {
            "page": "presence", "file": "presence", "title": "Days present", "kicker": "Show up",
            "blurb": "How many distinct days they sent something, and what % of the chat’s lifespan that is.",
            "value_key": "days_active", "value_fmt": "int",
            "share_key": "presence", "share_suffix": "% of chat days",
            "cols": [
                ("days_active", "int", "Days active"),
                ("presence", "pct", "% of chat lifespan"),
                ("streak", "int", "Longest streak"),
            ],
        },
        "peaks": {
            "page": "peaks", "file": "peaks", "title": "Biggest single day", "kicker": "Peaks",
            "blurb": "Most messages they sent in any one calendar day.",
            "value_key": "peak_day", "value_fmt": "int",
            "share_key": "avg_per_day", "share_suffix": "per day on average",
            "cols": [
                ("peak_day", "int", "Max in one day"),
                ("avg_per_day", "num", "Usual / day"),
                ("message_count", "int", "Total messages"),
            ],
        },
        "openers": {
            "page": "openers", "file": "openers", "title": "Who starts the day", "kicker": "Openers",
            "blurb": "Who sent the first message on each calendar day.",
            "value_key": "days_opened", "value_fmt": "int",
            "share_key": "open_pct", "share_suffix": "% of days",
            "cols": [
                ("days_opened", "int", "Days opened"),
                ("open_pct", "pct", "% of days"),
                ("days_closed", "int", "Days closed"),
            ],
        },
        "closers": {
            "page": "closers", "file": "closers", "title": "Who gets the last word", "kicker": "Closers",
            "blurb": "Who sent the last message on each calendar day.",
            "value_key": "days_closed", "value_fmt": "int",
            "share_key": "close_pct", "share_suffix": "% of days",
            "cols": [
                ("days_closed", "int", "Days closed"),
                ("close_pct", "pct", "% of days"),
                ("days_opened", "int", "Days opened"),
            ],
        },
        "nights": {
            "page": "nights", "file": "nights", "title": "Night owls", "kicker": "Late",
            "blurb": f"Share of their messages sent between 10pm and 5am ({tz_label}).",
            "value_key": "night_pct", "value_fmt": "num",
            "share_key": "night_count", "share_suffix": "night messages", "share_fmt": "int",
            "cols": [
                ("night_pct", "pct", "% at night"),
                ("night_count", "int", "Night messages"),
                ("peak_hour", "text", "Busiest hour"),
            ],
        },
        "weekends": {
            "page": "weekends", "file": "weekends", "title": "Weekend texters", "kicker": "Sat–Sun",
            "blurb": "Share of their messages sent on Saturday or Sunday.",
            "value_key": "weekend_pct", "value_fmt": "num",
            "share_key": "weekend_count", "share_suffix": "weekend messages", "share_fmt": "int",
            "cols": [
                ("weekend_pct", "pct", "% weekend"),
                ("weekend_count", "int", "Weekend messages"),
                ("peak_weekday", "text", "Busiest weekday"),
            ],
        },
        "when": {
            "page": "when", "file": "when", "title": "When people text", "kicker": "Hour",
            "blurb": f"Hourly volume for the whole chat, {tz_label}.",
            "value_key": "message_count", "value_fmt": "int",
            "share_key": "percentage", "share_suffix": "% of chat",
            "show_hours": True,
            "cols": [
                ("peak_hour", "text", f"Busiest hour ({tz_label})"),
                ("night_pct", "pct", "% night"),
                ("message_count", "int", "Messages"),
            ],
        },
        "week": {
            "page": "week", "file": "week", "title": "Day of week", "kicker": "Weekly",
            "blurb": "Messages by weekday for the whole chat.",
            "value_key": "message_count", "value_fmt": "int",
            "share_key": "percentage", "share_suffix": "% of chat",
            "show_weekdays": True,
            "cols": [
                ("peak_weekday", "text", "Busiest day"),
                ("weekend_pct", "pct", "% weekend"),
                ("message_count", "int", "Messages"),
            ],
        },
        "mornings": {
            "page": "mornings", "file": "mornings", "title": "Early birds", "kicker": "5–10am",
            "blurb": f"Share of their messages sent between 5am and 10am ({tz_label}).",
            "value_key": "morning_pct", "value_fmt": "num",
            "share_key": "morning_count", "share_suffix": "morning messages", "share_fmt": "int",
            "cols": [
                ("morning_pct", "pct", "% morning"),
                ("morning_count", "int", "Morning messages"),
                ("peak_hour", "text", "Busiest hour"),
            ],
        },
        "likes": {
            "page": "likes", "file": "likes", "title": "Likes received", "kicker": "Thumbs up",
            "blurb": "👍 tapbacks on their messages.",
            "value_key": "liked_got", "value_fmt": "int",
            "share_key": "liked_per_100", "share_suffix": "likes / 100 messages they sent",
            "cols": [
                ("liked_got", "int", "Likes received"),
                ("liked_per_100", "num", "Likes / 100 msgs"),
                ("liked_given", "int", "Likes given"),
            ],
        },
        "huh": {
            "page": "huh", "file": "huh", "title": "Questioned", "kicker": "??",
            "blurb": "❓ tapbacks on their messages — the ones people didn’t get.",
            "value_key": "questioned_got", "value_fmt": "int",
            "share_key": "questioned_per_100", "share_suffix": "?? / 100 messages they sent",
            "cols": [
                ("questioned_got", "int", "Questioned"),
                ("questioned_per_100", "num", "?? / 100 msgs"),
                ("message_count", "int", "Messages sent"),
            ],
        },
        "veterans": {
            "page": "veterans", "file": "veterans", "title": "Longest in the chat", "kicker": "Veterans",
            "blurb": "Days from their first message to the latest message in this chat.",
            "value_key": "tenure_days", "value_fmt": "int",
            "share_key": "days_active", "share_suffix": "days they actually texted", "share_fmt": "int",
            "cols": [
                ("tenure_days", "int", "Days since first text"),
                ("first_label", "text", "First message"),
                ("days_active", "int", "Days active"),
            ],
        },
        "ghosts": {
            "page": "ghosts", "file": "ghosts", "title": "Days since last text", "kicker": "Ghosts",
            "blurb": "Who has gone quiet. Ranked by days since their last message in this chat.",
            "value_key": "silent_days", "value_fmt": "int",
            "share_key": "recent", "share_suffix": "messages in last 30 days", "share_fmt": "int",
            "cols": [
                ("silent_days", "int", "Days silent"),
                ("last_label", "text", "Last message"),
                ("recent", "int", "Last 30 days"),
            ],
        },
        "recent": {
            "page": "recent", "file": "recent", "title": "Last 30 days", "kicker": "Now",
            "blurb": "Messages in the 30 days before the latest message in this chat.",
            "value_key": "recent", "value_fmt": "int",
            "share_key": "recent_pct", "share_suffix": "% of their all-time messages",
            "cols": [
                ("recent", "int", "Last 30 days"),
                ("recent_pct", "pct", "% of their total"),
                ("message_count", "int", "All-time"),
            ],
        },
        "rambles": {
            "page": "rambles", "file": "rambles", "title": "Back-to-back texts", "kicker": "Rambles",
            "blurb": "Messages sent immediately after themselves — they kept talking before anyone else did.",
            "value_key": "followups", "value_fmt": "int",
            "share_key": "followup_pct", "share_suffix": "% of their messages",
            "cols": [
                ("followups", "int", "Follow-up texts"),
                ("followup_pct", "pct", "% of their messages"),
                ("message_count", "int", "Total messages"),
            ],
        },
        "bursts": {
            "page": "bursts", "file": "bursts", "title": "Triple-text bursts", "kicker": "Bursts",
            "blurb": "How often they sent 3 or more messages in a row before anyone else replied.",
            "value_key": "bursts", "value_fmt": "int",
            "share_key": "burst_pct", "share_suffix": "bursts / 100 messages",
            "cols": [
                ("bursts", "int", "Bursts of 3+"),
                ("followups", "int", "All follow-ups"),
                ("message_count", "int", "Messages"),
            ],
        },
        "kickoffs": {
            "page": "kickoffs", "file": "kickoffs", "title": "Who restarts the chat", "kicker": "Kickoffs",
            "blurb": f"First message after {SILENCE_HOURS}+ hours of silence in the thread.",
            "value_key": "kickoffs", "value_fmt": "int",
            "share_key": "kickoff_pct", "share_suffix": "kickoffs / 100 messages",
            "cols": [
                ("kickoffs", "int", "Kickoffs"),
                ("days_opened", "int", "Days opened"),
                ("message_count", "int", "Messages"),
            ],
        },
        "replies": {
            "page": "replies", "file": "replies", "title": "Fastest replies", "kicker": "Speed",
            "blurb": f"Median minutes to reply after someone else texted (gaps under {REPLY_MAX_HOURS} hours). Lower is faster.",
            "value_key": "reply_median_min", "value_fmt": "num",
            "share_key": "reply_count", "share_suffix": "timed replies", "share_fmt": "int",
            "ascending": True,
            "cols": [
                ("reply_median_min", "num", "Median reply (min)"),
                ("reply_count", "int", "Timed replies"),
                ("message_count", "int", "Messages"),
            ],
        },
        "links": {
            "page": "links", "file": "links", "title": "Link droppers", "kicker": "URLs",
            "blurb": "Messages that include a link (http/https or www).",
            "value_key": "links", "value_fmt": "int",
            "share_key": "link_pct", "share_suffix": "% of their messages",
            "cols": [
                ("links", "int", "Link messages"),
                ("link_pct", "pct", "% of messages"),
                ("message_count", "int", "Messages"),
            ],
        },
        "emoji": {
            "page": "emoji", "file": "emoji", "title": "Emoji volume", "kicker": "Emoji",
            "blurb": "Total emoji characters sent. Per 100 = emoji chars ÷ messages × 100.",
            "value_key": "emoji_count", "value_fmt": "int",
            "share_key": "emoji_per_100", "share_suffix": "emoji chars / 100 messages",
            "cols": [
                ("emoji_count", "int", "Emoji characters"),
                ("emoji_msgs", "int", "Messages with emoji"),
                ("emoji_per_100", "num", "Per 100 messages"),
            ],
        },
    }

    page_t = Template(PAGE_TMPL)
    for chat_name, payload in all_data.items():
        folder = slug(chat_name)
        chat_dir = config.output_dir / folder
        chat_dir.mkdir(parents=True, exist_ok=True)
        local_chats = []
        for c in chats:
            same = c["folder"] == folder
            if config.app_mode:
                prefix = config.public_prefix
                urls = {
                    f"{key}_url": (
                        page_filename(file_id) if same
                        else f"{prefix}/{c['folder']}/{page_filename(file_id)}"
                    )
                    for key, _label, file_id in nav
                }
                urls["messages_url"] = "index.html" if same else f"{prefix}/{c['folder']}/index.html"
            else:
                urls = chat_urls(c["folder"], nav, relative=True) if same else {
                    f"{key}_url": f"../{c['folder']}/{page_filename(file_id)}"
                    for key, _label, file_id in nav
                }
            local_chats.append({**c, **urls})
        core_nav = [
            {**item, "href": page_filename(item["file"])}
            for item in core_meta
        ]
        extra_groups = [
            {
                "label": group["label"],
                "links": [{**item, "href": page_filename(item["file"])} for item in group["links"]],
            }
            for group in extra_meta
        ]
        page_kwargs = dict(
            chat_name=chat_name,
            chat_info=payload["chat_info"],
            chats=local_chats,
            core_nav=core_nav,
            extra_groups=extra_groups,
            core_ids=core_ids,
            hours=payload.get("hours", []),
            weekdays=payload.get("weekdays", []),
            css_href=css_page,
            home_href=home_href or "../index.html",
            home_label=home_label,
            tz_label=tz_label,
            filter_label=config.filter_label,
            generated=generated,
        )
        for spec in pages.values():
            members = ranked(
                payload["members"],
                spec["value_key"],
                ascending=spec.get("ascending", False),
            )
            filename = page_filename(spec.get("file"))
            html = page_t.render(
                **page_kwargs,
                page=spec["page"],
                title=spec["title"],
                kicker=spec["kicker"],
                blurb=spec["blurb"],
                members=members,
                value_key=spec["value_key"],
                value_fmt=spec["value_fmt"],
                share_key=spec["share_key"],
                share_suffix=spec["share_suffix"],
                share_fmt=spec.get("share_fmt"),
                cols=spec.get("cols", []),
                simple=spec.get("simple", False),
                show_hours=spec.get("show_hours", False),
                show_weekdays=spec.get("show_weekdays", False),
                show_keywords=False,
                keyword_boards=[],
                page_is_extra=spec["page"] not in core_ids,
                extra_current=next(
                    (item["label"] for g in extra_groups for item in g["links"] if item["id"] == spec["page"]),
                    None,
                ),
            )
            (chat_dir / filename).write_text(html)
        if config.keywords:
            html = page_t.render(
                **page_kwargs,
                page="keywords",
                title="Keyword search",
                kicker="Words",
                blurb="How often each person said these words. Case-insensitive, whole-word match. Change the list with KEYWORDS in .env or --keywords.",
                members=[],
                value_key="kw_hits",
                value_fmt="int",
                share_key="kw_per_100",
                share_suffix="uses / 100 messages",
                share_fmt=None,
                cols=[],
                simple=True,
                show_hours=False,
                show_weekdays=False,
                show_keywords=True,
                keyword_boards=payload.get("keyword_boards", []),
                page_is_extra=True,
                extra_current="Keywords",
            )
            (chat_dir / "keywords.html").write_text(html)

    group_chats = [c for c in chats if c.get("kind") != "dm"]
    dm_chats = [c for c in chats if c.get("kind") == "dm"]
    index = Template(INDEX_TMPL).render(
        chats=chats,
        group_chats=group_chats,
        dm_chats=dm_chats,
        core_nav=core_meta,
        extra_groups=extra_meta,
        css_href=css_index,
        home_href=home_href or "index.html",
        home_label=home_label,
        app_mode=config.app_mode,
        report_index_href=(
            f"{config.public_prefix}/index.html" if config.app_mode else "index.html"
        ),
        tz_label=tz_label,
        filter_label=config.filter_label,
        export_formats=sorted(config.export_formats),
        generated=generated,
    )
    path = config.output_dir / "index.html"
    path.write_text(index)
    exports = write_exports(all_data, config)
    return path, exports


CSS = """
:root {
  --ink: #1a1a1a;
  --muted: #6b6b6b;
  --line: #e6e1d8;
  --bg: #f3efe7;
  --card: #fffcf7;
  --accent: #2c4a3e;
  --gold: #b0892c;
  --silver: #7d7d7d;
  --bronze: #a15c2d;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.4;
}

.wrap { max-width: 760px; margin: 0 auto; padding: 40px 22px 80px; }

header {
  border-bottom: 3px double var(--ink);
  padding-bottom: 18px;
  margin-bottom: 16px;
}
.kicker {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.72rem;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 700;
  margin-bottom: 6px;
}
header h1 {
  font-size: 2.35rem;
  letter-spacing: -0.03em;
  font-weight: 700;
  line-height: 1.05;
}
.page-title {
  font-size: 1.15rem;
  margin-top: 8px;
  font-style: italic;
  color: var(--accent);
}
.meta {
  margin-top: 10px;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--muted);
  font-size: 0.82rem;
}
.blurb {
  margin-top: 10px;
  color: var(--muted);
  font-size: 0.95rem;
  max-width: 58ch;
}

.nav, .subnav {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
}
.nav { margin: 16px 0 8px; }
.subnav {
  position: sticky;
  top: 0;
  z-index: 40;
  margin: 0 -8px 28px;
  padding: 10px 8px 12px;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
}

.nav a, .subnav > a, .subnav summary {
  color: var(--ink);
  text-decoration: none;
  border: 1px solid var(--ink);
  padding: 5px 11px;
  font-size: 0.72rem;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  background: var(--card);
  cursor: pointer;
  list-style: none;
}
.subnav summary::-webkit-details-marker { display: none; }
.subnav summary::after { content: " ▾"; font-size: 0.65rem; }
.subnav summary:focus { outline: none; }
.subnav summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.nav a.active, .nav a:hover,
.subnav > a.active, .subnav > a:hover,
.subnav summary.active, .subnav details[open] > summary {
  background: var(--ink);
  color: var(--card);
}

.menu { position: static; }
.menu-panel {
  position: absolute;
  z-index: 50;
  top: 100%;
  left: 8px;
  right: 8px;
  width: auto;
  max-height: min(70vh, 32rem);
  overflow-y: auto;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  background: var(--card);
  border: 1px solid var(--ink);
  border-top: 0;
  padding: 12px 14px 14px;
}
.menu-group + .menu-group { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--line); }
.menu-label {
  font-size: 0.62rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--muted);
  font-weight: 700;
  margin-bottom: 6px;
}
.menu-panel a {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  border: 0;
  padding: 6px 0;
  text-transform: none;
  letter-spacing: 0;
  font-size: 0.92rem;
  font-weight: 650;
  background: transparent;
  color: var(--ink);
  width: 100%;
  text-decoration: none;
}
.menu-panel a small {
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 500;
  text-align: right;
  flex: 1 1 auto;
}
.menu-panel a span { flex: 0 0 auto; }
.menu-panel a:hover, .menu-panel a.active {
  background: transparent;
  color: var(--accent);
}
.menu-panel a.active small { color: var(--accent); }

.key-panel p { margin: 0 0 10px; font-size: 0.92rem; line-height: 1.4; }
.key-panel ul {
  margin: 0;
  padding-left: 16px;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.45;
}
.key-panel li { margin: 4px 0; }
.key-panel strong { color: var(--ink); }

.hours {
  margin: 8px 0 36px;
  padding: 16px 14px 12px;
  background: var(--card);
  border: 1px solid var(--line);
}
.hours-plot {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  height: 200px;
}
.hour-col {
  flex: 1;
  height: 100%;
  display: flex;
  align-items: flex-end;
}
.hour-bar {
  width: 100%;
  background: var(--accent);
  border-radius: 2px 2px 0 0;
}
.hour-bar.is-empty { height: 0 !important; }
.hours-axis {
  display: flex;
  gap: 5px;
  margin-top: 8px;
  border-top: 1px solid var(--line);
  padding-top: 6px;
}
.hours-axis span {
  flex: 1;
  text-align: center;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.68rem;
  color: var(--muted);
}

.row {
  display: grid;
  grid-template-columns: 44px 1fr;
  gap: 12px;
  padding: 14px 0 16px;
  border-bottom: 1px solid var(--line);
}
.row:last-of-type { border-bottom: 3px double var(--ink); }

.rank {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  font-size: 1.25rem;
  padding-top: 4px;
}
.rank.g { color: var(--gold); }
.rank.s { color: var(--silver); }
.rank.b { color: var(--bronze); }

.who { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.name { font-size: 1.45rem; font-weight: 700; letter-spacing: -0.02em; min-width: 0; overflow-wrap: break-word; }
.hero-wrap { text-align: right; flex: 0 0 auto; }
.hero {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-variant-numeric: tabular-nums;
  font-size: 1.45rem;
  font-weight: 750;
}
.share {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 600;
  margin-top: 2px;
}

.bar { height: 6px; background: #e8e2d6; margin: 10px 0 0; }
.bar > span { display: block; height: 100%; background: var(--accent); }

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 8px 12px;
  margin-top: 12px;
}
.stat {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  border-top: 1px solid var(--line);
  padding-top: 6px;
}
.stat .n { font-size: 1rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat .l {
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-top: 2px;
}

.card {
  background: var(--card);
  border: 1px solid var(--ink);
  padding: 18px 20px;
  margin-bottom: 12px;
}
.card h2 { font-size: 1.25rem; margin-bottom: 4px; }
.card-top {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
}
.card-top h2 { margin: 0; }
.badge {
  display: inline-block;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  border: 1px solid var(--ink);
  padding: 2px 6px;
  vertical-align: middle;
  margin-right: 4px;
}
.badge.group { background: var(--ink); color: var(--card); }
.badge.dm { background: var(--card); color: var(--accent); border-color: var(--accent); }
.section-label {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 28px 0 12px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 6px;
}
.nav a .badge { margin-right: 6px; }
.card p {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--muted);
  font-size: 0.82rem;
  margin-bottom: 12px;
}
.links { display: flex; flex-wrap: wrap; gap: 8px; }
.links a {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--ink);
  text-decoration: none;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  border-bottom: 1px solid var(--ink);
  padding-bottom: 1px;
}

footer {
  margin-top: 36px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--muted);
  font-size: 0.72rem;
}

@media (max-width: 600px) {
  .stats { grid-template-columns: 1fr 1fr; }
  .name, .hero { font-size: 1.2rem; }
  header h1 { font-size: 1.8rem; }
  .who { flex-wrap: wrap; }
  .hours-plot { height: 140px; gap: 3px; }
  .menu-panel a { flex-wrap: wrap; gap: 2px 12px; }
  .menu-panel a small { text-align: left; flex-basis: 100%; }
}

.kw { margin: 8px 0 40px; }
.kw h2 {
  font-size: 1.55rem;
  letter-spacing: -0.02em;
  font-weight: 700;
}
.kw-meta {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  color: var(--muted);
  font-size: 0.82rem;
  margin: 2px 0 8px;
}
.kw .row:last-of-type { border-bottom: 1px solid var(--line); }

.index-more { margin-top: 10px; }
.index-more summary {
  cursor: pointer;
  list-style: none;
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink);
}
.index-more summary::-webkit-details-marker { display: none; }
.index-more summary::after { content: " ▾"; }
.index-more[open] summary::after { content: " ▴"; }
.index-more .menu-panel {
  position: static;
  left: auto;
  right: auto;
  top: auto;
  width: 100%;
  max-height: none;
  overflow: visible;
  margin-top: 10px;
  border-top: 1px solid var(--ink);
}

@media print {
  body { background: #fff; }
  .nav, .subnav, footer, .blurb { display: none; }
  .wrap { padding: 12px; max-width: none; }
  header { margin-bottom: 12px; }
}
"""

PAGE_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ chat_name }} · {{ title }}</title>
  <link rel="stylesheet" href="{{ css_href }}">
</head>
<body>
  <div class="wrap">
    <header>
      <div class="kicker">{{ kicker }}</div>
      <h1>{{ chat_name }}</h1>
      <div class="page-title">{{ title }}</div>
      <div class="meta">
        {% if chat_info.kind_subtitle %}{{ chat_info.kind_subtitle }} · {% endif %}
        {{ "{:,}".format(chat_info.total_messages) }} messages
        {% if chat_info.kind != 'dm' %} · {{ chat_info.member_count }} people{% endif %}
        {% if chat_info.start_date and chat_info.end_date %}
        · {{ chat_info.start_date.strftime('%b %-d, %Y') }} – {{ chat_info.end_date.strftime('%b %-d, %Y') }}
        {% endif %}
        · {{ tz_label }}
        {% if filter_label %} · filter {{ filter_label }}{% endif %}
      </div>
      <p class="blurb">{{ blurb }}</p>
    </header>

    <nav class="nav">
      <a href="{{ home_href }}">{{ home_label }}</a>
      {% for c in chats %}
      <a href="{{ c.messages_url }}" class="{{ 'active' if c.name == chat_name else '' }}">
        <span class="badge {{ 'dm' if c.kind == 'dm' else 'group' }}">{{ c.kind_badge }}</span>
        {{ c.name }}
      </a>
      {% endfor %}
    </nav>
    <nav class="subnav">
      {% for item in core_nav %}
      <a href="{{ item.href }}" class="{{ 'active' if page == item.id else '' }}">{{ item.label }}</a>
      {% endfor %}
      <details class="menu">
        <summary class="{{ 'active' if page_is_extra else '' }}">{{ extra_current or 'More stats' }}</summary>
        <div class="menu-panel">
          {% for group in extra_groups %}
          <div class="menu-group">
            <div class="menu-label">{{ group.label }}</div>
            {% for item in group.links %}
            <a href="{{ item.href }}" class="{{ 'active' if page == item.id else '' }}">
              <span>{{ item.label }}</span>
              <small>{{ item.hint }}</small>
            </a>
            {% endfor %}
          </div>
          {% endfor %}
        </div>
      </details>
      <details class="menu key">
        <summary>Key</summary>
        <div class="menu-panel key-panel">
          <p><strong>{{ title }}.</strong> {{ blurb }}</p>
          <ul>
            <li><strong>1 / 2 / 3</strong> are gold, silver, bronze.</li>
            <li>The bar is relative to whoever is #1 on this page, not a % of the whole chat.</li>
            <li><strong>Per 100 messages</strong> = this stat ÷ texts they sent × 100.</li>
            <li><strong>Tapbacks</strong> are iMessage reactions: Love, Like, Haha, !!, ??.</li>
            <li><strong>Received</strong> = reactions on their texts. <strong>Given</strong> = reactions they left.</li>
          </ul>
        </div>
      </details>
    </nav>

    {% if show_hours or show_weekdays %}
    {% set bars = hours if show_hours else weekdays %}
    <div class="hours">
      <div class="hours-plot">
        {% for h in bars %}
        <div class="hour-col" title="{{ h.label }} · {{ '{:,}'.format(h.count) }} messages">
          <div class="hour-bar{% if h.count == 0 %} is-empty{% endif %}" style="height: {{ h.height }}%"></div>
        </div>
        {% endfor %}
      </div>
      <div class="hours-axis">
        {% for h in bars %}
        <span>{% if h.show_label %}{{ h.label }}{% endif %}</span>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    {% if show_keywords %}
    {% for board in keyword_boards %}
    <section class="kw">
      <h2>{{ board.word }}</h2>
      <div class="kw-meta">{{ "{:,}".format(board.total) }} uses · {{ "{:,}".format(board.messages) }} messages</div>
      {% for m in board.members %}
      <div class="row">
        <div class="rank {{ 'g' if m.rank==1 else 's' if m.rank==2 else 'b' if m.rank==3 else '' }}">{{ m.rank }}</div>
        <div>
          <div class="who">
            <div class="name">{{ m.name }}</div>
            <div class="hero-wrap">
              <div class="hero">{{ "{:,}".format(m.kw_hits) }}</div>
              <div class="share">{{ "%.1f"|format(m.kw_per_100) }} uses / 100 messages</div>
            </div>
          </div>
          <div class="bar"><span style="width: {{ '%.1f'|format(m.bar_pct) }}%"></span></div>
        </div>
      </div>
      {% endfor %}
    </section>
    {% endfor %}
    {% endif %}

    {% for m in members %}
    <div class="row">
      <div class="rank {{ 'g' if m.rank==1 else 's' if m.rank==2 else 'b' if m.rank==3 else '' }}">{{ m.rank }}</div>
      <div>
        <div class="who">
          <div class="name">{{ m.name }}</div>
          <div class="hero-wrap">
            <div class="hero">
              {% set v = m[value_key] %}
              {% if v is none %}—
              {% elif value_fmt == 'int' %}{{ "{:,}".format(v) }}
              {% else %}{{ "%.1f"|format(v) }}{% endif %}
            </div>
            <div class="share">
              {% if share_fmt == 'int' %}{{ "{:,}".format(m[share_key]|int) }} {{ share_suffix }}
              {% elif share_key == 'percentage' %}{{ "%.1f"|format(m.percentage) }} {{ share_suffix }}
              {% elif m[share_key] is none %}— {{ share_suffix }}
              {% else %}{{ "%.1f"|format(m[share_key]) }} {{ share_suffix }}{% endif %}
            </div>
          </div>
        </div>
        <div class="bar"><span style="width: {{ '%.1f'|format(m.bar_pct) }}%"></span></div>
        {% if not simple %}
        <div class="stats">
          {% for key, fmt, label in cols %}
          <div class="stat">
            <div class="n">
              {% set cv = m[key] %}
              {% if cv is none %}—
              {% elif fmt == 'int' %}{{ "{:,}".format(cv) }}
              {% elif fmt == 'pct' %}{{ "%.1f"|format(cv) }}%
              {% elif fmt == 'text' %}{{ cv }}
              {% else %}{{ "%.1f"|format(cv) }}{% endif %}
            </div>
            <div class="l">{{ label }}</div>
          </div>
          {% endfor %}
        </div>
        {% endif %}
      </div>
    </div>
    {% endfor %}

    <footer>Generated {{ generated.strftime('%b %-d, %Y %-I:%M %p %Z') }} · print this page to screenshot without nav</footer>
  </div>
  <script>
    (function () {
      var menus = Array.prototype.slice.call(document.querySelectorAll(".menu"));
      menus.forEach(function (d) {
        d.addEventListener("toggle", function () {
          if (d.open) {
            menus.forEach(function (other) {
              if (other !== d) other.open = false;
            });
          }
        });
      });
      document.addEventListener("click", function (e) {
        menus.forEach(function (d) {
          if (d.open && !d.contains(e.target)) d.open = false;
        });
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          menus.forEach(function (d) { d.open = false; });
        }
      });
    })();
  </script>
</body>
</html>
"""

INDEX_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Chats</title>
  <link rel="stylesheet" href="{{ css_href }}">
</head>
<body>
  <div class="wrap">
    <header>
      <div class="kicker">Leaderboard</div>
      <h1>Chats</h1>
      <div class="meta">{{ tz_label }}{% if filter_label %} · filter {{ filter_label }}{% endif %} · groups and 1:1 · tapbacks include Haha 😂, Love, Like, and !!{% if export_formats %} · exports: {{ export_formats|join(', ') }}{% endif %}</div>
    </header>
    <nav class="nav">
      {% if app_mode %}<a href="{{ home_href }}">{{ home_label }}</a>{% endif %}
      <a href="{{ report_index_href }}" class="active">Reports</a>
      {% for c in chats %}
      <a href="{{ c.messages_url }}">
        <span class="badge {{ 'dm' if c.kind == 'dm' else 'group' }}">{{ c.kind_badge }}</span>
        {{ c.name }}
      </a>
      {% endfor %}
    </nav>

    {% macro chat_card(c) %}
    <div class="card">
      <div class="card-top">
        <span class="badge {{ 'dm' if c.kind == 'dm' else 'group' }}">{{ c.kind_badge }}</span>
        <h2>{{ c.name }}</h2>
      </div>
      <p>{{ "{:,}".format(c.total_messages) }} messages{% if c.kind != 'dm' %} · {{ c.member_count }} people{% endif %}
         {% if c.start_date and c.end_date %}
         · {{ c.start_date.strftime('%b %Y') }} – {{ c.end_date.strftime('%b %Y') }}
         {% endif %}
         {% if c.kind_subtitle %} · {{ c.kind_subtitle }}{% endif %}</p>
      <div class="links core-links">
        {% for item in core_nav %}
        <a href="{{ c[item.key] }}">{{ item.label }}</a>
        {% endfor %}
        {% if 'csv' in export_formats %}
        <a href="{{ c.csv_url }}">CSV</a>
        {% endif %}
        {% if 'json' in export_formats %}
        <a href="{{ c.json_url }}">JSON</a>
        {% endif %}
      </div>
      <details class="index-more">
        <summary>More stats</summary>
        <div class="menu-panel">
          {% for group in extra_groups %}
          <div class="menu-group">
            <div class="menu-label">{{ group.label }}</div>
            {% for item in group.links %}
            <a href="{{ c[item.key] }}">
              <span>{{ item.label }}</span>
              <small>{{ item.hint }}</small>
            </a>
            {% endfor %}
          </div>
          {% endfor %}
        </div>
      </details>
    </div>
    {% endmacro %}

    {% if group_chats %}
    <h3 class="section-label">Group chats</h3>
    {% for c in group_chats %}{{ chat_card(c) }}{% endfor %}
    {% endif %}

    {% if dm_chats %}
    <h3 class="section-label">One-on-one</h3>
    {% for c in dm_chats %}{{ chat_card(c) }}{% endfor %}
    {% endif %}

    {% if not group_chats and not dm_chats %}
    {% for c in chats %}{{ chat_card(c) }}{% endfor %}
    {% endif %}

    <footer>Generated {{ generated.strftime('%b %-d, %Y %-I:%M %p %Z') }}{% if export_formats %} · bulk exports in exports/{% endif %}</footer>
  </div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Build a local HTML leaderboard from your macOS Messages chats (groups and 1:1)."
    )
    parser.add_argument("--db", help="Path to chat.db (default: from .env or ~/Library/Messages/chat.db)")
    parser.add_argument("--gcs", help='Comma-separated chat names (groups or 1:1), e.g. "Family,Alex"')
    parser.add_argument("--outdir", help="Output folder (default: ./output)")
    parser.add_argument("--keywords", help='Comma-separated words to rank, e.g. "lol,bet,fr" (or set KEYWORDS in .env)')
    parser.add_argument("--year", type=int, help="Only include messages from this calendar year")
    parser.add_argument("--since", help="Only include messages on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", help="Only include messages on/before this date (YYYY-MM-DD)")
    parser.add_argument(
        "--export",
        help="Export formats: csv, json, csv,json, or none (default: csv,json / EXPORT in .env)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_chats",
        help="Print group and 1:1 chat names from the database, then exit",
    )
    args = parser.parse_args()
    try:
        config = Config(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    conn = get_connection(config.db_path)

    mapper = NameMapper(
        config.aliases_file,
        config.cache_file,
        config.contacts_dump,
        config.your_name,
    )

    if args.list_chats:
        groups, dms = build_chat_index(conn, mapper, tz=config.tz)
        conn.close()
        if not groups and not dms:
            print("No chats found.")
            return
        print_chat_list(groups, dms)
        return

    if not config.target_gcs:
        print("No chats set. Put names in TARGET_GROUP_CHATS in .env, or pass --gcs.", file=sys.stderr)
        print("Or run `python app.py` to pick a chat in the browser.", file=sys.stderr)
        print("Run `python analyze.py --list` to see groups and 1:1 chats.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    print("Chat leaderboard")
    print("=" * 60)
    print(f"Timezone: {config.tz_label}")
    if config.filter_label:
        print(f"Filter: {config.filter_label}")
    if config.keywords:
        print("Keywords: " + ", ".join(config.keywords))
    if config.export_formats:
        print("Exports: " + ", ".join(sorted(config.export_formats)))

    chats = find_chats(conn, config.target_gcs, mapper, tz=config.tz)
    if not chats:
        print("No matching chats found. Run `python analyze.py --list` to see names.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    try:
        out, exports, _ = analyze_chats(conn, mapper, chats, config)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.close()
    print(f"\nWrote {out}")
    if exports:
        print(f"Exports ({len(exports)} files), including output/exports/")


if __name__ == "__main__":
    main()
