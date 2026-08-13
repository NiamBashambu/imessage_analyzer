# Local data

These files are created on your machine and are gitignored.

| File | What it is |
| --- | --- |
| `contacts_dump.json` | Export of macOS Contacts (phones + emails) |
| `name_cache.json` | Identifier → resolved name after a run |

Delete both and rerun to rebuild from Contacts:

```bash
rm -f data/name_cache.json data/contacts_dump.json
python app.py
# or: python analyze.py --list
```
