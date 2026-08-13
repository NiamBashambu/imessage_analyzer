#!/usr/bin/env python3
"""Local web app: pick a Group or 1:1 chat, analyze on demand, view the leaderboard."""

import os
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from flask import Flask, abort, flash, jsonify, redirect, render_template_string, request, send_from_directory, url_for

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from analyze import (  # noqa: E402
    Config,
    analyze_chats,
    build_chat_index,
    find_chat_by_id,
    get_connection,
    slug,
)
from src.name_mapper import NameMapper  # noqa: E402

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "imessage-local-dev")

# Shared mapper / connection state for one process
_state = {"mapper": None, "config": None}


def get_mapper():
    if _state["mapper"] is None:
        cfg = Config.for_app()
        _state["config"] = cfg
        _state["mapper"] = NameMapper(
            cfg.aliases_file,
            cfg.cache_file,
            cfg.contacts_dump,
            cfg.your_name,
        )
    return _state["mapper"], _state["config"]


HOME_TMPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>iMessage leaderboard</title>
  <style>
    :root {
      --ink: #1a1a1a;
      --muted: #6b6b6b;
      --line: #e6e1d8;
      --bg: #f3efe7;
      --card: #fffcf7;
      --accent: #2c4a3e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.4;
    }
    .wrap { max-width: 860px; margin: 0 auto; padding: 40px 22px 80px; }
    header {
      border-bottom: 3px double var(--ink);
      padding-bottom: 18px;
      margin-bottom: 20px;
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
    h1 {
      font-size: 2.35rem;
      letter-spacing: -0.03em;
      font-weight: 700;
      line-height: 1.05;
    }
    .meta {
      margin-top: 10px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      color: var(--muted);
      font-size: 0.82rem;
    }
    .flash {
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      background: #f7e4e0;
      border: 1px solid var(--ink);
      padding: 10px 12px;
      margin-bottom: 16px;
      font-size: 0.85rem;
    }
    .tabs {
      display: flex;
      gap: 6px;
      margin: 8px 0 14px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .tabs button {
      border: 1px solid var(--ink);
      background: var(--card);
      color: var(--ink);
      padding: 7px 14px;
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .tabs button.active {
      background: var(--ink);
      color: var(--card);
    }
    .search {
      width: 100%;
      border: 1px solid var(--ink);
      background: var(--card);
      padding: 10px 12px;
      font-size: 1rem;
      font-family: inherit;
      margin-bottom: 14px;
    }
    .search:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .list { border-top: 1px solid var(--line); }
    .row {
      display: grid;
      grid-template-columns: 72px 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 12px 4px;
      border-bottom: 1px solid var(--line);
      text-decoration: none;
      color: inherit;
      background: transparent;
      border-left: 0;
      border-right: 0;
      border-top: 0;
      width: 100%;
      text-align: left;
      cursor: pointer;
      font: inherit;
    }
    .row:hover { background: rgba(44, 74, 62, 0.06); }
    .row.hidden { display: none; }
    .badge {
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.62rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      border: 1px solid var(--ink);
      padding: 3px 7px;
      text-align: center;
    }
    .badge.group { background: var(--ink); color: var(--card); }
    .badge.dm { background: var(--card); color: var(--accent); border-color: var(--accent); }
    .name { font-size: 1.15rem; font-weight: 700; letter-spacing: -0.02em; }
    .sub {
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      color: var(--muted);
      margin-top: 2px;
    }
    .count {
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-variant-numeric: tabular-nums;
      font-weight: 700;
      font-size: 0.85rem;
      color: var(--muted);
      white-space: nowrap;
    }
    details.options {
      margin: 18px 0 8px;
      border: 1px solid var(--ink);
      background: var(--card);
      padding: 10px 14px;
    }
    details.options summary {
      cursor: pointer;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      list-style: none;
    }
    details.options summary::-webkit-details-marker { display: none; }
    .opts {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px 14px;
      margin-top: 12px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.82rem;
    }
    .opts label { display: flex; flex-direction: column; gap: 4px; color: var(--muted); }
    .opts input, .opts select {
      border: 1px solid var(--ink);
      background: var(--bg);
      padding: 7px 8px;
      font: inherit;
      color: var(--ink);
    }
    .opts .span2 { grid-column: 1 / -1; }
    .hint {
      margin-top: 10px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.75rem;
      color: var(--muted);
    }
    .busy {
      display: none;
      margin: 12px 0;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.85rem;
      color: var(--accent);
      font-weight: 650;
    }
    .busy.on { display: block; }
    @media (max-width: 600px) {
      .opts { grid-template-columns: 1fr; }
      h1 { font-size: 1.8rem; }
      .row { grid-template-columns: 64px 1fr; }
      .count { grid-column: 2; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="kicker">Local · read-only</div>
      <h1>Pick a chat</h1>
      <div class="meta">{{ your_name }} · {{ tz_label }} · {{ group_count }} groups · {{ dm_count }} one-on-ones · newest first</div>
    </header>

    {% with messages = get_flashed_messages() %}
    {% if messages %}
    {% for m in messages %}<div class="flash">{{ m }}</div>{% endfor %}
    {% endif %}
    {% endwith %}

    <form id="analyze-form" method="post" action="{{ url_for('analyze') }}">
      <input type="hidden" name="chat_id" id="chat_id" value="">

      <div class="tabs" role="tablist">
        <button type="button" class="active" data-tab="group" id="tab-group">Group chats</button>
        <button type="button" data-tab="dm" id="tab-dm">One-on-one</button>
      </div>

      <input class="search" type="search" id="q" placeholder="Search by name…" autocomplete="off">

      <div class="busy" id="busy">Analyzing… this can take a few seconds for large chats.</div>

      <div class="list" id="list-group">
        {% for c in groups %}
        <button type="button" class="row" data-kind="group" data-id="{{ c.chat_id }}" data-name="{{ c.label|lower }}">
          <span class="badge group">Group</span>
          <span>
            <div class="name">{{ c.label }}</div>
            <div class="sub">{{ c.last_active_label or 'Group chat' }} · {{ "{:,}".format(c.messages) }} messages</div>
          </span>
          <span class="count">{{ c.last_active_label or '—' }}</span>
        </button>
        {% else %}
        <p class="hint">No named group chats found.</p>
        {% endfor %}
      </div>

      <div class="list" id="list-dm" hidden>
        {% for c in dms %}
        <button type="button" class="row" data-kind="dm" data-id="{{ c.chat_id }}" data-name="{{ c.label|lower }}">
          <span class="badge dm">1:1</span>
          <span>
            <div class="name">{{ c.label }}</div>
            <div class="sub">
              {% if c.identifiers and c.identifiers|length > 1 %}
              {{ c.identifiers|length }} threads merged · {{ "{:,}".format(c.messages) }} messages
              {% elif c.identifier %}
              {{ c.identifier }} · {{ "{:,}".format(c.messages) }} messages
              {% else %}
              One-on-one · {{ "{:,}".format(c.messages) }} messages
              {% endif %}
            </div>
          </span>
          <span class="count">{{ c.last_active_label or '—' }}</span>
        </button>
        {% else %}
        <p class="hint">No one-on-one chats found.</p>
        {% endfor %}
      </div>

      <details class="options">
        <summary>Options</summary>
        <div class="opts">
          <label>Year only
            <input type="number" name="year" placeholder="e.g. 2025" min="2005" max="2100" value="{{ year or '' }}">
          </label>
          <label>Export
            <select name="export">
              <option value="csv,json" {% if export == 'csv,json' %}selected{% endif %}>CSV + JSON</option>
              <option value="csv" {% if export == 'csv' %}selected{% endif %}>CSV only</option>
              <option value="json" {% if export == 'json' %}selected{% endif %}>JSON only</option>
              <option value="none" {% if export == 'none' %}selected{% endif %}>None</option>
            </select>
          </label>
          <label>Since (YYYY-MM-DD)
            <input type="text" name="since" placeholder="2024-01-01" value="{{ since or '' }}">
          </label>
          <label>Until (YYYY-MM-DD)
            <input type="text" name="until" placeholder="2024-12-31" value="{{ until or '' }}">
          </label>
          <label class="span2">Keywords (comma-separated)
            <input type="text" name="keywords" value="{{ keywords }}">
          </label>
        </div>
        <p class="hint">Use Year alone, or Since/Until — not both. Leave blank for all-time.</p>
      </details>
    </form>
  </div>
  <script>
    (function () {
      var tabGroup = document.getElementById("tab-group");
      var tabDm = document.getElementById("tab-dm");
      var listGroup = document.getElementById("list-group");
      var listDm = document.getElementById("list-dm");
      var q = document.getElementById("q");
      var form = document.getElementById("analyze-form");
      var chatId = document.getElementById("chat_id");
      var busy = document.getElementById("busy");
      var active = "group";

      function showTab(kind) {
        active = kind;
        tabGroup.classList.toggle("active", kind === "group");
        tabDm.classList.toggle("active", kind === "dm");
        listGroup.hidden = kind !== "group";
        listDm.hidden = kind !== "dm";
        filterRows();
      }
      tabGroup.addEventListener("click", function () { showTab("group"); });
      tabDm.addEventListener("click", function () { showTab("dm"); });

      function filterRows() {
        var needle = (q.value || "").trim().toLowerCase();
        var list = active === "group" ? listGroup : listDm;
        list.querySelectorAll(".row").forEach(function (row) {
          var name = row.getAttribute("data-name") || "";
          row.classList.toggle("hidden", needle && name.indexOf(needle) === -1);
        });
      }
      q.addEventListener("input", filterRows);

      document.querySelectorAll(".row").forEach(function (row) {
        row.addEventListener("click", function () {
          chatId.value = row.getAttribute("data-id");
          busy.classList.add("on");
          form.submit();
        });
      });
    })();
  </script>
</body>
</html>
"""


@app.get("/")
def home():
    mapper, cfg = get_mapper()
    conn = get_connection(cfg.db_path)
    try:
        groups, dms = build_chat_index(conn, mapper, tz=cfg.tz)
    finally:
        conn.close()
    kw = ",".join(cfg.keywords) if cfg.keywords else ""
    export = ",".join(sorted(cfg.export_formats)) if cfg.export_formats else "none"
    return render_template_string(
        HOME_TMPL,
        groups=groups,
        dms=dms,
        group_count=len(groups),
        dm_count=len(dms),
        your_name=cfg.your_name,
        tz_label=cfg.tz_label,
        keywords=kw,
        export=export,
        year=os.getenv("YEAR", ""),
        since=os.getenv("SINCE", ""),
        until=os.getenv("UNTIL", ""),
    )


@app.get("/api/chats")
def api_chats():
    mapper, cfg = get_mapper()
    conn = get_connection(cfg.db_path)
    try:
        groups, dms = build_chat_index(conn, mapper, tz=cfg.tz)
    finally:
        conn.close()

    def pack(items):
        return [
            {
                "kind": c["kind"],
                "label": c["label"],
                "chat_id": c["chat_id"],
                "chat_ids": c.get("chat_ids") or [c["chat_id"]],
                "messages": c["messages"],
                "identifier": c.get("identifier"),
                "identifiers": c.get("identifiers") or [],
                "last_active_label": c.get("last_active_label") or "",
                "threads": len(c.get("chat_ids") or [c["chat_id"]]),
            }
            for c in items
        ]

    return jsonify({"groups": pack(groups), "dms": pack(dms)})


@app.post("/analyze")
def analyze():
    mapper, base_cfg = get_mapper()
    chat_id = request.form.get("chat_id", "").strip()
    if not chat_id:
        flash("Pick a chat from the list.")
        return redirect(url_for("home"))

    year = request.form.get("year") or None
    since = request.form.get("since") or None
    until = request.form.get("until") or None
    keywords = request.form.get("keywords")
    export = request.form.get("export") or "csv,json"
    if year == "":
        year = None
    if since == "":
        since = None
    if until == "":
        until = None

    try:
        cfg = Config.for_app(
            year=int(year) if year else None,
            since=since,
            until=until,
            keywords=keywords if keywords is not None else None,
            export=export,
        )
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("home"))
    except Exception as e:
        flash(str(e))
        return redirect(url_for("home"))

    conn = get_connection(cfg.db_path)
    try:
        chat = find_chat_by_id(conn, mapper, chat_id, tz=cfg.tz)
        if not chat:
            flash(f"Chat id {chat_id} not found.")
            return redirect(url_for("home"))
        chat_map = {
            chat["label"]: {
                "chat_id": chat["chat_id"],
                "chat_ids": chat.get("chat_ids") or [chat["chat_id"]],
                "kind": chat["kind"],
            }
        }
        _, _, all_data = analyze_chats(conn, mapper, chat_map, cfg, quiet=True)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("home"))
    except Exception as e:
        flash(f"Analyze failed: {e}")
        return redirect(url_for("home"))
    finally:
        conn.close()

    folder = slug(next(iter(all_data.keys())))
    return redirect(f"/report/{folder}/index.html")


@app.get("/report/<path:path>")
def report(path):
    mapper, cfg = get_mapper()
    root = cfg.output_dir
    # prevent path escape
    target = (root / path).resolve()
    if not str(target).startswith(str(root.resolve())):
        abort(404)
    if not target.exists():
        abort(404)
    return send_from_directory(root, path)


def main():
    port = int(os.getenv("PORT", "5050"))
    url = f"http://127.0.0.1:{port}/"
    print("iMessage leaderboard app")
    print("=" * 40)
    print(f"Open {url}")
    print("Grant Full Disk Access to this terminal if chat.db cannot be read.")
    Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
