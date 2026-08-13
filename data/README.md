# Local data

These files are created on your machine and are gitignored.

| File | What it is |
| --- | --- |
| `contacts_dump.json` | One-time export of macOS Contacts (phones + emails) |
| `name_cache.json` | Identifier → resolved name after a run |

Delete both and rerun `python analyze.py` to rebuild from Contacts.
