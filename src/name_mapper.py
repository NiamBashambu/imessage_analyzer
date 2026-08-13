"""
Resolve chat.db identifiers (phones/emails) to contact names.

Loads the macOS Contacts book once, then matches by:
- email (case-insensitive)
- phone digits (ignores +1, dashes, spaces; last-10 US match)
- nickname aliases (Mikey/Michael, etc.)
"""

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

NICKNAMES = {
    "mike": "michael",
    "mikey": "michael",
    "mick": "michael",
    "mickey": "michael",
    "micheal": "michael",
    "michale": "michael",
    "michal": "michael",
    "bob": "robert",
    "bobby": "robert",
    "rob": "robert",
    "robbie": "robert",
    "jim": "james",
    "jimmy": "james",
    "joe": "joseph",
    "joey": "joseph",
    "dan": "daniel",
    "danny": "daniel",
    "dave": "david",
    "davey": "david",
    "will": "william",
    "bill": "william",
    "billy": "william",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "alex": "alexander",
}


def digits_only(value):
    if value is None:
        return ""
    return re.sub(r"\D", "", str(value))


def normalize_phone(value):
    """Strip formatting and a leading US country code."""
    d = digits_only(value)
    if not d:
        return ""
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


def phones_match(a, b):
    da, db = normalize_phone(a), normalize_phone(b)
    if not da or not db:
        return False
    if da == db:
        return True
    # International: one is a suffix of the other (min 8 digits)
    if len(da) >= 8 and len(db) >= 8:
        return da.endswith(db) or db.endswith(da)
    return False


def canonical_first_name(name):
    """First name only, with nicknames collapsed (Mikey → Michael)."""
    if not name:
        return "Unknown"
    first = str(name).strip().split()[0]
    # Don't treat raw phones/emails as names
    if first.startswith("+") or "@" in first or first[0].isdigit():
        return first
    key = first.lower()
    return NICKNAMES.get(key, key).title()


def _last_token(full_name):
    parts = [p for p in str(full_name).strip().split() if p]
    if len(parts) < 2:
        return None
    last = parts[-1]
    # Ignore trailing punctuation
    last = last.strip(".,'\"")
    return last or None


def label_with_last_initial(full_name, first_name):
    last = _last_token(full_name)
    if last and last[0].isalpha():
        return f"{first_name} {last[0].upper()}."
    return first_name


def label_with_last_name(full_name, first_name):
    last = _last_token(full_name)
    if last and last[0].isalpha():
        return f"{first_name} {last.title()}"
    return first_name


def person_identity_key(full_name):
    """Stable person key: nickname-normalized first name + rest of name."""
    parts = [p for p in str(full_name).strip().split() if p]
    if not parts:
        return ""
    first = canonical_first_name(parts[0]).lower()
    rest = " ".join(parts[1:]).lower()
    return f"{first}|{rest}" if rest else first


def build_chat_labeler(mapper, handle_pairs):
    """
    Map (handle_id, is_from_me) → leaderboard label.

    Same person across phone/email merges via contact name (nicknames normalized).
    If two different people share a first name in this chat:
      1) First L.   (Dylan C.)
      2) First Last  if the initial still collides
    """
    person_full = {}  # person_id -> full name string (best display form)
    handle_to_person = {}

    def person_id(handle, is_me):
        if is_me:
            return "me"
        full = mapper.resolve(handle, False)
        if not full:
            return None
        return "n:" + person_identity_key(full)

    for handle, is_me in handle_pairs:
        is_me = bool(is_me)
        pid = person_id(handle, is_me)
        if pid is None:
            continue
        handle_to_person[(str(handle), is_me)] = pid
        if pid == "me":
            person_full[pid] = mapper.your_name
        else:
            full = mapper.resolve(handle, False)
            prev = person_full.get(pid)
            if prev is None:
                person_full[pid] = full
            else:
                # Prefer a spelling whose first token already matches the canonical first name
                # e.g. "Michael Evans" over "Mikey Evans"
                canon = canonical_first_name(full)
                prev_tok = str(prev).strip().split()[0].lower()
                new_tok = str(full).strip().split()[0].lower()
                if prev_tok != canon.lower() and new_tok == canon.lower():
                    person_full[pid] = full

    firsts = {pid: canonical_first_name(full) for pid, full in person_full.items()}
    first_counts = defaultdict(int)
    for first in firsts.values():
        first_counts[first.lower()] += 1

    labels = {}
    for pid, full in person_full.items():
        first = firsts[pid]
        if first_counts[first.lower()] > 1:
            labels[pid] = label_with_last_initial(full, first)
        else:
            labels[pid] = first

    label_counts = defaultdict(int)
    for lab in labels.values():
        label_counts[lab.lower()] += 1
    for pid, full in person_full.items():
        lab = labels[pid]
        if label_counts[lab.lower()] > 1:
            labels[pid] = label_with_last_name(full, firsts[pid])

    def lookup(handle, is_me):
        is_me = bool(is_me)
        pid = handle_to_person.get((str(handle), is_me))
        if pid is None:
            pid = person_id(handle, is_me)
            if pid is None:
                return None
            if pid not in labels:
                full = mapper.your_name if is_me else mapper.resolve(handle, False)
                return canonical_first_name(full) if full else None
        return labels.get(pid)

    return lookup


class NameMapper:
    def __init__(self, aliases_path, cache_path, contacts_dump_path, your_name="You"):
        self.aliases_path = Path(aliases_path)
        self.cache_path = Path(cache_path)
        self.contacts_dump_path = Path(contacts_dump_path)
        self.your_name = your_name

        self.aliases = self._load_json(self.aliases_path)
        self.cache = self._load_json(self.cache_path)

        # phone_digits -> name, email -> name
        self.phone_index = {}
        self.email_index = {}
        self._load_contacts()

        # Drop stale cache entries that are just the raw identifier
        self._scrub_failed_cache()

    def _load_json(self, path):
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(self.cache, f, indent=2)

    def _scrub_failed_cache(self):
        """Remove entries where the 'name' is still a phone/email."""
        cleaned = {}
        for ident, name in self.cache.items():
            if not name:
                continue
            if name == ident:
                continue
            if str(name).startswith("+") or (str(name)[:1].isdigit() and "@" not in str(name)):
                continue
            cleaned[ident] = name
        if cleaned != self.cache:
            self.cache = cleaned
            self._save_cache()

    def _load_contacts(self):
        """Dump Contacts once (cached) and index phones/emails."""
        dump = self._load_json(self.contacts_dump_path)
        if not dump.get("entries"):
            dump = self._fetch_contacts()
            if dump.get("entries"):
                self.contacts_dump_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.contacts_dump_path, "w") as f:
                    json.dump(dump, f, indent=2)

        for entry in dump.get("entries", []):
            name = (entry.get("name") or "").strip()
            ident = (entry.get("id") or "").strip()
            if not name or not ident:
                continue
            if "@" in ident:
                self.email_index[ident.lower()] = name
            else:
                key = normalize_phone(ident)
                if key:
                    self.phone_index[key] = name

        print(f"  Contacts index: {len(self.phone_index)} phones, {len(self.email_index)} emails")

    def _fetch_contacts(self):
        """AppleScript dump of every contact phone + email."""
        script = r'''
        tell application "Contacts"
            set output to ""
            repeat with p in people
                set n to name of p
                try
                    repeat with ph in phones of p
                        set output to output & n & tab & (value of ph as string) & linefeed
                    end repeat
                end try
                try
                    repeat with em in emails of p
                        set output to output & n & tab & (value of em as string) & linefeed
                    end repeat
                end try
            end repeat
            return output
        end tell
        '''
        print("  Loading macOS Contacts (one-time dump)...")
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            print("  Warning: Contacts dump timed out")
            return {"entries": []}
        except Exception as e:
            print(f"  Warning: Contacts dump failed: {e}")
            return {"entries": []}

        if result.returncode != 0:
            err = (result.stderr or "").strip()
            print(f"  Warning: Contacts access failed{': ' + err if err else ''}")
            print("  Grant Contacts permission to Terminal/Cursor if prompted.")
            return {"entries": []}

        entries = []
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            name, ident = line.split("\t", 1)
            name, ident = name.strip(), ident.strip()
            if name and ident:
                entries.append({"name": name, "id": ident})
        print(f"  Dumped {len(entries)} contact identifiers")
        return {"entries": entries}

    def _lookup_contacts(self, identifier):
        ident = str(identifier).strip()
        if "@" in ident:
            return self.email_index.get(ident.lower())

        phone = normalize_phone(ident)
        if phone and phone in self.phone_index:
            return self.phone_index[phone]

        # Suffix match for international numbers
        if phone and len(phone) >= 8:
            for stored, name in self.phone_index.items():
                if phones_match(phone, stored):
                    return name
        return None

    def resolve(self, identifier, is_from_me=False):
        if is_from_me:
            return self.your_name

        if identifier is None:
            return None

        # pandas NaN
        try:
            if identifier != identifier:
                return None
        except Exception:
            pass

        ident = str(identifier).strip()
        if ident == "" or ident.lower() == "none" or ident.lower() == "nan":
            return None

        # 1. User aliases (exact, then normalized phone)
        if ident in self.aliases:
            return self.aliases[ident]
        phone = normalize_phone(ident)
        for alias_key, alias_name in self.aliases.items():
            if "@" in ident and alias_key.lower() == ident.lower():
                return alias_name
            if phone and phones_match(alias_key, ident):
                return alias_name

        # 2. Good cache hits only
        if ident in self.cache:
            return self.cache[ident]

        # 3. Contacts book
        name = self._lookup_contacts(ident)
        if name:
            self.cache[ident] = name
            self._save_cache()
            return name

        # Unresolved — return None so caller can skip or show raw
        return None

    def display_name(self, identifier, is_from_me=False):
        """Canonical first name for leaderboard grouping."""
        resolved = self.resolve(identifier, is_from_me)
        if resolved:
            return canonical_first_name(resolved)
        if is_from_me:
            return self.your_name
        return None
