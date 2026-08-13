# iMessage chat leaderboard

Local macOS tool. It reads your Messages database **read-only** and builds screenshotable leaderboards for **group chats and 1:1s**.

Nothing is uploaded. Chats, contacts, and reports stay on your machine.

**Requires:** macOS, the Messages app, Python 3.9+.

---

## Quickstart

```bash
git clone https://github.com/NiamBashambu/imessage_analyzer.git
cd imessage_analyzer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` at least:

```
YOUR_NAME=Alex
TIMEZONE=America/Los_Angeles
KEYWORDS=lol,lmao,omg,fr,bet
```

`TARGET_GROUP_CHATS` is only needed for the CLI batch mode. With the app, you pick chats in the browser.

### 1. Full Disk Access

Required or `chat.db` cannot be read.

**System Settings → Privacy & Security → Full Disk Access** → enable the app that runs Python (**Terminal**, **iTerm**, or **Cursor**). Quit and reopen that app after toggling it.

### 2. Launch the app

```bash
python app.py
```

Browser opens [http://127.0.0.1:5050/](http://127.0.0.1:5050/).

- Tabs: **Group chats** / **One-on-one** (newest activity first, like Messages)
- Search by name
- Click a chat to analyze
- **Options**: year / date range, keywords, CSV/JSON export

Reports open under `/report/...`. Use **Pick chat** in the nav to choose another.

The first run may ask for **Contacts** access — allow it so numbers become names.

Change the port with `PORT=5050` in `.env` if needed.

---

## What the picker does

| Behavior | Detail |
| --- | --- |
| **Spam / filtered** | Drops chats Apple marked Filtered Unknown Senders (`is_filtered`) |
| **Unnamed groups** | Hides room threads with no display name (avoids fake “1:1s” with many people) |
| **1:1 only** | Real DMs: Instant Message style, exactly one handle, not a `chat…` group id |
| **Short codes** | Skips 4–6 digit blast / OTP-style SMS |
| **Merge 1:1s** | Same Contacts person (phone + email) → one entry; analysis combines both threads |
| **Merge groups** | Same display name → one entry (iMessage often creates a second room id for the same named group) |

Unresolved Contacts still show as phone/email until you allow Contacts or add an `aliases.json` mapping.

---

## What you get

One folder per chat under `output/`. Main row: Messages, Received, Given, Pace, When. Everything else is under **More stats**. Open **Key** on any page for how to read the numbers.

| Main | What it ranks |
| --- | --- |
| **Messages** | Texts sent (best screenshot page) |
| **Received / Given** | Tapbacks got vs tapbacks given |
| **Pace** | Messages per day |
| **When** | Hour of day |

| More stats | What it ranks |
| --- | --- |
| **Haha / Hearts / Likes / Huh** | 😂, ❤️, 👍, ❓ received |
| **Magnet / Balance** | Tapbacks per message, given ÷ received |
| **Streaks / Presence / Peaks / Recent / Rambles / Bursts** | Daily run, days active, biggest day, last 30 days, back-to-back, 3+ in a row |
| **Openers / Closers / Kickoffs / Replies** | First/last of day, restart after silence, fastest median reply |
| **Nights / Mornings / Weekends / Week** | Late-night, 5–10am, Sat–Sun, weekday |
| **Links / Emoji** | URLs, emoji volume |
| **Veterans / Ghosts** | Longest tenure, days since last text |
| **Keywords** | Who said each word (set `KEYWORDS` or Options) |

Names come from macOS Contacts. Nicknames like Mike / Mikey / Michael are merged. If two different people in the same chat share a first name, labels become **First L.** (or **First Last** if the initial still clashes). Reports show a **Group** or **1:1** badge.

---

## Setup reference

| Variable | Required | What to put |
| --- | --- | --- |
| `YOUR_NAME` | Yes | How you appear on the leaderboard |
| `TIMEZONE` | Yes | [IANA zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `KEYWORDS` | No | Words to rank (comma-separated) |
| `EXPORT` | No | `csv,json` (default), `csv`, `json`, or `none` |
| `TARGET_GROUP_CHATS` | CLI only | Names from `python analyze.py --list` |
| `YEAR` / `SINCE` / `UNTIL` | No | Date filters for CLI (app uses Options) |
| `PORT` | No | App port (default `5050`) |
| `DB_PATH` | No | Default `~/Library/Messages/chat.db` |
| `ALIASES_FILE` | No | Path to phone/email → name overrides |

Copy `.env.example` → `.env`. Never commit `.env`.

### Override a name Contacts missed

```bash
cp aliases.example.json aliases.json
```

```json
{
  "+15551234567": "Sam",
  "friend@icloud.com": "Jordan"
}
```

### Rebuild name cache

```bash
rm -f data/name_cache.json data/contacts_dump.json
python app.py
```

---

## Batch / CLI (optional)

For scripting or analyzing several chats at once:

```bash
python analyze.py --list
```

Put names in `TARGET_GROUP_CHATS` (groups and 1:1s), then:

```bash
python analyze.py
open output/index.html
```

### Date filters

```bash
python analyze.py --year 2025
python analyze.py --since 2024-06-01 --until 2025-12-31
```

Or set `YEAR` / `SINCE` / `UNTIL` in `.env`. In the app, use **Options**.

### Exports (CSV / JSON)

By default each run writes:

| Path | Contents |
| --- | --- |
| `output/<chat>/stats.csv` | Per-person stats for that chat |
| `output/<chat>/stats.json` | Same + keyword boards |
| `output/exports/members.csv` | All chats combined |
| `output/exports/chats.csv` | Chat-level summary |
| `output/exports/all.json` | Combined JSON |

```bash
python analyze.py --export csv
python analyze.py --export none
```

### Useful flags

```bash
python analyze.py --list
python analyze.py --gcs "Family Group,Sam"
python analyze.py --year 2025
python analyze.py --since 2024-01-01 --until 2024-12-31
python analyze.py --keywords "lol,bet,down bad"
python analyze.py --export csv,json
```

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `cannot read chat.db` | Grant Full Disk Access to the app running Python, then quit and reopen it. |
| App won’t open chats | Same FDA rule — Cursor and Terminal are separate apps. |
| `No chats set` (CLI) | Use `app.py`, or set `TARGET_GROUP_CHATS` / `--gcs`. |
| 1:1 shows a phone number | Allow Contacts, or map it in `aliases.json`. |
| Duplicate group names | Same-named rooms are merged automatically; restart `app.py` after pulling updates. |
| Spam / random people in 1:1 | Filtered Unknown Senders, short codes, and unnamed groups are excluded; restart the app. |
| Date filter empty | Widen the range or clear Options / env filters. |
| Stale picker list | Restart `python app.py` (no auto-reload unless you restart). |

---

## Privacy

Never commit `.env`, `aliases.json`, `data/*cache*`, `output/`, or any `chat.db`. `.gitignore` already covers these.

---

## Layout

```
app.py                  local web app (pick Group vs 1:1)
analyze.py              CLI + shared analyze/render pipeline
src/name_mapper.py      Contacts + nickname merging
.env.example            copy to .env
aliases.example.json    sample name overrides
data/                   local Contacts dump + name cache (gitignored)
output/                 generated reports (wiped each analyze)
```
