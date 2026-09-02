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

`TARGET_GROUP_CHATS` is only needed for CLI batch mode. With the app, you pick chats in the browser.

### 1. Full Disk Access

Required or `chat.db` cannot be read.

**System Settings → Privacy & Security → Full Disk Access** → enable the app that runs Python (**Terminal**, **iTerm**, or **Cursor**). Quit and reopen that app after toggling it.

### 2. Launch the app

```bash
python app.py
```

Browser opens [http://127.0.0.1:5050/](http://127.0.0.1:5050/).

1. Switch **Group chats** / **One-on-one** (newest first, like Messages)
2. Search by name
3. Click a chat → choose a **date range** (presets or slider)
4. Optionally check **Compare to previous period**
5. Click **Analyze**

Top-right controls:

| Button | What it does |
| --- | --- |
| **Options** | Keywords for the Keywords page |
| **Export** | CSV/JSON format (downloads appear on the report after analyze) |
| **Dark** | Light/dark theme (saved in your browser) |

Reports open under `/report/...`. Use **Pick chat** in the nav to analyze another.

The first run may ask for **Contacts** access — allow it so numbers become names.

Change the port with `PORT=5050` in `.env` if needed.

---

## Date range & compare

When you click a chat, a modal opens (the chat list is not re-queried on every slider move):

| Control | Behavior |
| --- | --- |
| **Presets** | All time, Last 7/30/90 days, Last year, This year |
| **Slider** | Drag start/end within that chat’s full history |
| **Compare** | Current window vs previous equal-length window |

Compare examples:

- **Last 30 days** → previous 30 days before that
- **Jan–Mar 2025** → Oct–Dec 2024 (same length)
- **All time** → first half vs second half of the chat

With compare enabled, you land on the **Compare** report. Main stats pages still use your selected date range.

Chat date bounds are fetched once per chat per session (lightweight query), then cached in the browser.

---

## What the picker filters

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

One folder per chat under `output/`. Open **Key** on any page for how to read the numbers.

### Main pages

| Page | What it ranks |
| --- | --- |
| **Messages** | Texts sent (best screenshot page) |
| **Received / Given** | Tapbacks got vs tapbacks given |
| **Pace** | Messages per day |
| **When** | Hour of day |
| **Compare** | Current vs previous period (messages, tapbacks, reply time, pace) |

### More stats

| Group | Pages |
| --- | --- |
| **Tapbacks** | Haha, Hearts, Likes, Huh, Magnet, Balance |
| **Consistency** | Streaks, Presence, Peaks, Recent, Rambles, Bursts |
| **Timing** | Openers, Closers, Kickoffs, Replies, **Response**, Nights, Mornings, Weekends, Week |
| **Content** | Links, Emoji |
| **Tenure** | Veterans, Ghosts |
| **Words** | Keywords (from `KEYWORDS` or Options) |

**Response** (under Timing) breaks down reply speed: median, average, 90th percentile, and slowest reply (gaps under 12 hours after someone else texts).

Names come from macOS Contacts. Nicknames like Mike / Mikey / Michael are merged. If two people in the same chat share a first name, labels become **First L.** (or **First Last** if needed). Reports show a **Group** or **1:1** badge.

---

## Setup reference

| Variable | Required | What to put |
| --- | --- | --- |
| `YOUR_NAME` | Yes | How you appear on the leaderboard |
| `TIMEZONE` | Yes | [IANA zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| `KEYWORDS` | No | Words to rank (comma-separated) |
| `EXPORT` | No | `csv,json` (default), `csv`, `json`, or `none` |
| `TARGET_GROUP_CHATS` | CLI only | Names from `python analyze.py --list` |
| `YEAR` / `SINCE` / `UNTIL` | CLI only | Date filters for `analyze.py` (app uses the per-chat modal) |
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

### Date filters (CLI only)

```bash
python analyze.py --year 2025
python analyze.py --since 2024-06-01 --until 2025-12-31
```

Or set `YEAR` / `SINCE` / `UNTIL` in `.env`. The web app uses the per-chat date modal instead.

### Exports (CSV / JSON)

Default output:

| Path | Contents |
| --- | --- |
| `output/<chat>/stats.csv` | Per-person stats for that chat |
| `output/<chat>/stats.json` | Same + keyword boards |
| `output/exports/members.csv` | All chats combined |
| `output/exports/chats.csv` | Chat-level summary |
| `output/exports/all.json` | Combined JSON |

In the app: set format under **Export** before analyzing, then use **Export → Download CSV/JSON** on the report.

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
| Duplicate group names | Same-named rooms are merged automatically. |
| Spam / random people in 1:1 | Filtered Unknown Senders, short codes, and unnamed groups are excluded. |
| No messages in range | Widen the date slider or pick **All time**. |
| Compare page missing | Check **Compare to previous period** in the date modal before analyzing. |
| Dark mode resets | Theme is per-browser via `localStorage`; use the **Dark** button on picker or reports. |
| Stale picker / new features | Restart `python app.py` and re-analyze (reports are regenerated each run). |

---

## Privacy

Never commit `.env`, `aliases.json`, `data/*cache*`, `output/`, or any `chat.db`. `.gitignore` already covers these.

---

## Layout

```
app.py                  local web app (pick Group vs 1:1, date range, compare)
analyze.py              CLI + shared analyze/render pipeline
src/name_mapper.py      Contacts + nickname merging
.env.example            copy to .env
aliases.example.json    sample name overrides
data/                   local Contacts dump + name cache (gitignored)
output/                 generated reports (wiped each analyze)
```
