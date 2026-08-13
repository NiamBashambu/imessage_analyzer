# iMessage chat leaderboard

Local macOS tool. It reads your Messages database **read-only** and writes a static HTML site for any chats you pick — **group chats and 1:1s**.

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

**1. Full Disk Access** (required or `chat.db` cannot be read)

System Settings → Privacy & Security → Full Disk Access → enable the app that will run Python (**Terminal**, **iTerm**, or **Cursor** — whichever you use). Quit and reopen that app after toggling it.

**2. Find your chats**

```bash
python analyze.py --list
```

You’ll see two sections:

- **GROUP CHATS** — named groups from Messages.app
- **ONE-ON-ONE** — DMs, labeled with the contact’s first name (from Contacts)

Copy names **exactly** as printed. Untitled groups won’t appear until you name them in Messages.

**3. Edit `.env`** (required before the next step)

```
YOUR_NAME=Alex
TIMEZONE=America/Los_Angeles
TARGET_GROUP_CHATS=Family Group,Roommates,Sam
KEYWORDS=lol,lmao,omg,fr,bet
```

Mix groups and 1:1 names in the same list. No quotes around the values.

| Variable | What to put |
| --- | --- |
| `YOUR_NAME` | How you appear on the leaderboard |
| `TIMEZONE` | [IANA zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), e.g. `America/New_York` |
| `TARGET_GROUP_CHATS` | Names from `--list` (groups **and** 1:1s), comma-separated |
| `KEYWORDS` | Optional words to rank. Blank the line to hide that page |

`TARGET_CHATS` works as an alias for the same setting.

**4. Run and open**

```bash
python analyze.py
open output/index.html
```

The first run may ask for **Contacts** access. Allow it so phone numbers become names (especially for 1:1 labels).

To screenshot a page without the nav: File → Print → Save as PDF (or print).

---

## What you get

One folder per chat. The main row is five pages; everything else is under **More stats**. Open **Key** on any page for how to read the numbers.

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
| **Keywords** | Who said each word (set `KEYWORDS` in `.env`) |

Names come from macOS Contacts. Nicknames like Mike / Mikey / Michael are merged. Country codes like `+1` are ignored when matching phones. If two different people in the same chat share a first name, labels become **First L.** (or **First Last** if the initial still clashes).

---

## Optional

### Date filters

Limit stats to a year or range (in your `TIMEZONE`):

```bash
python analyze.py --year 2025
python analyze.py --since 2024-06-01 --until 2025-12-31
```

Or in `.env`:

```
YEAR=2025
```

or

```
SINCE=2024-06-01
UNTIL=2025-12-31
```

Use **either** `YEAR` **or** `SINCE`/`UNTIL`, not both. The active filter is shown on every page.

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
python analyze.py --export json
python analyze.py --export none
```

Or set `EXPORT=csv,json` / `EXPORT=none` in `.env`. Index cards link to each chat’s CSV/JSON when enabled.

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

Then rerun `python analyze.py`. Useful when a 1:1 still shows a phone number in `--list`.

### CLI flags

These override `.env` for one run:

```bash
python analyze.py --list
python analyze.py --gcs "Family Group,Sam"
python analyze.py --year 2025
python analyze.py --since 2024-01-01 --until 2024-12-31
python analyze.py --keywords "lol,bet,down bad"
python analyze.py --export csv,json
python analyze.py --outdir ./output
python analyze.py --db ~/Library/Messages/chat.db
```

### Rebuild name cache

If someone still shows up wrong, delete the local cache and rerun:

```bash
rm -f data/name_cache.json data/contacts_dump.json
python analyze.py
```

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `cannot read chat.db` / Full Disk Access error | Grant FDA to the app running Python, then **quit and reopen** it. Cursor and Terminal are separate apps. |
| `No chats set` | Put names in `TARGET_GROUP_CHATS` or pass `--gcs`. Run `--list` first. |
| `no chat named …` | Names must match `--list`. For 1:1s use the contact first name shown there. Untitled groups need a name in Messages. |
| 1:1 shows a phone number | Allow Contacts, or map it in `aliases.json`. |
| Everyone is “Unknown” / raw numbers | Allow Contacts on first run. Add misses to `aliases.json`. |
| Keywords page missing | Set `KEYWORDS` in `.env` or pass `--keywords`. |
| Date filter empty | Widen `SINCE`/`UNTIL`/`YEAR`, or drop the filter. |
| No CSV/JSON | Set `EXPORT=csv,json` (default) or pass `--export csv,json`. |
| Times look wrong | Set `TIMEZONE` to your IANA zone. Optional `TIMEZONE_LABEL` for a custom label. |
| `ModuleNotFoundError` | Activate the venv and `pip install -r requirements.txt`. |

---

## Privacy

Never commit:

- `.env`
- `aliases.json`
- `data/name_cache.json` / `data/contacts_dump.json`
- `output/`
- any `chat.db`

`.gitignore` already covers these. `output/` is wiped and rebuilt on every run.

---

## Layout

```
analyze.py              entry point
src/name_mapper.py      Contacts + nickname merging
.env.example            copy to .env (gitignored)
aliases.example.json    copy to aliases.json if needed
data/                   local cache (not committed)
output/                 generated site (wiped each run)
  index.html
  css/style.css
  family_group/
    index.html          messages
    received.html
    …
  sam/                  1:1 example
    index.html
    …
```
