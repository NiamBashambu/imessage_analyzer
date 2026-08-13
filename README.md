# iMessage group chat leaderboard

Local macOS tool. It reads your Messages database **read-only** and writes a static HTML site for any group chats you pick.

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

**2. Find your group chat names**

```bash
python analyze.py --list
```

Copy the names **exactly** as printed (spelling and spaces). Only chats with a name in Messages.app show up — rename untitled groups there first if needed.

**3. Edit `.env`** (required before the next step)

```
YOUR_NAME=Alex
TIMEZONE=America/Los_Angeles
TARGET_GROUP_CHATS=Family Group,Roommates
KEYWORDS=lol,lmao,omg,fr,bet
```

No quotes around the values. Comma-separated chat names from `--list`.

| Variable | What to put |
| --- | --- |
| `YOUR_NAME` | How you appear on the leaderboard |
| `TIMEZONE` | [IANA zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), e.g. `America/New_York` |
| `TARGET_GROUP_CHATS` | Names from `--list`, comma-separated |
| `KEYWORDS` | Optional words to rank. Leave the samples, change them, or blank the line to hide that page |

**4. Run and open**

```bash
python analyze.py
open output/index.html
```

The first run may ask for **Contacts** access. Allow it so phone numbers become names.

To screenshot a page without the nav: File → Print → Save as PDF (or print).

---

## What you get

One folder per group chat. The main row is five pages; everything else is under **More stats**. Open **Key** on any page for how to read the numbers.

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
| **Streaks / Presence / Peaks / Recent / Rambles** | Daily run, days active, biggest day, last 30 days, back-to-back |
| **Openers / Closers / Nights / Mornings / Weekends / Week** | First/last of day, late-night, 5–10am, Sat–Sun, weekday |
| **Veterans / Ghosts** | Longest tenure, days since last text |
| **Keywords** | Who said each word (set `KEYWORDS` in `.env`) |

Names come from macOS Contacts. Nicknames like Mike / Mikey / Michael are merged. Country codes like `+1` are ignored when matching phones.

---

## Optional

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

Then rerun `python analyze.py`.

### CLI flags

These override `.env` for one run:

```bash
python analyze.py --list
python analyze.py --gcs "Family Group,Roommates"
python analyze.py --keywords "lol,bet,down bad"
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
| `No group chats set` | Put names in `TARGET_GROUP_CHATS` or pass `--gcs`. Run `--list` first. |
| `no chat named …` | Names must match `--list` / Messages.app (spelling and spaces). Untitled groups do not appear until you name them in Messages. |
| Everyone is “Unknown” / raw numbers | Allow Contacts on first run. Add misses to `aliases.json`. |
| Keywords page missing | Set `KEYWORDS` in `.env` or pass `--keywords`. |
| Times look wrong | Set `TIMEZONE` to your IANA zone. Optional `TIMEZONE_LABEL` if you want a custom label. |
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
    given.html
    …
```
