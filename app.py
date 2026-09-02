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
    get_chat_date_range,
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
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      border-bottom: 3px double var(--ink);
      padding-bottom: 18px;
      margin-bottom: 20px;
    }
    .header-copy { min-width: 0; flex: 1; }
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
      position: relative;
      flex: 0 0 auto;
      margin: 0;
    }
    .header-actions {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      flex: 0 0 auto;
    }
    details.options summary {
      cursor: pointer;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      list-style: none;
      border: 1px solid var(--ink);
      background: var(--card);
      padding: 8px 12px;
      user-select: none;
      white-space: nowrap;
    }
    details.options summary::-webkit-details-marker { display: none; }
    details.options[open] summary {
      background: var(--ink);
      color: var(--card);
    }
    .opts-panel {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      z-index: 40;
      width: min(360px, calc(100vw - 44px));
      border: 1px solid var(--ink);
      background: var(--card);
      padding: 12px 14px;
      box-shadow: 0 10px 28px rgba(26, 26, 26, 0.12);
    }
    .opts {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px 14px;
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
    .export-actions {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-top: 4px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .export-actions .btn {
      display: block;
      text-align: center;
      border: 1px solid var(--ink);
      background: var(--ink);
      color: var(--card);
      padding: 9px 12px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-decoration: none;
      cursor: default;
    }
    .export-actions .btn.secondary {
      background: var(--card);
      color: var(--ink);
    }
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
    .modal-backdrop {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(26, 26, 26, 0.45);
      z-index: 100;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      width: min(480px, 100%);
      background: var(--card);
      border: 1px solid var(--ink);
      padding: 22px 22px 18px;
      box-shadow: 0 16px 40px rgba(26, 26, 26, 0.18);
    }
    .modal h2 {
      font-size: 1.55rem;
      letter-spacing: -0.02em;
      margin-bottom: 4px;
    }
    .modal .modal-meta {
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 0.78rem;
      color: var(--muted);
      margin-bottom: 16px;
    }
    .presets {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 16px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .presets button {
      border: 1px solid var(--ink);
      background: var(--card);
      color: var(--ink);
      padding: 6px 10px;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .presets button.active {
      background: var(--ink);
      color: var(--card);
    }
    .range-block {
      margin-bottom: 18px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .range-labels {
      display: flex;
      justify-content: space-between;
      font-size: 0.82rem;
      font-weight: 650;
      margin-bottom: 10px;
    }
    .range-labels span:last-child { color: var(--muted); font-weight: 500; }
    .range-track {
      position: relative;
      height: 36px;
      margin: 0 2px;
    }
    .range-track input[type="range"] {
      position: absolute;
      left: 0;
      right: 0;
      width: 100%;
      margin: 0;
      pointer-events: none;
      -webkit-appearance: none;
      appearance: none;
      background: transparent;
      height: 36px;
    }
    .range-track input[type="range"]::-webkit-slider-thumb {
      pointer-events: auto;
      -webkit-appearance: none;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: var(--ink);
      border: 2px solid var(--card);
      cursor: pointer;
    }
    .range-track input[type="range"]::-moz-range-thumb {
      pointer-events: auto;
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: var(--ink);
      border: 2px solid var(--card);
      cursor: pointer;
    }
    .range-track::before {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      top: 50%;
      height: 4px;
      margin-top: -2px;
      background: var(--line);
      border-radius: 2px;
    }
    .range-summary {
      margin-top: 8px;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .modal-actions button {
      border: 1px solid var(--ink);
      background: var(--card);
      color: var(--ink);
      padding: 9px 14px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .modal-actions button.primary {
      background: var(--ink);
      color: var(--card);
    }
    .modal-actions button:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    @media (max-width: 600px) {
      header { flex-wrap: wrap; }
      .header-actions { margin-left: auto; }
      .opts { grid-template-columns: 1fr; }
      h1 { font-size: 1.8rem; }
      .row { grid-template-columns: 64px 1fr; }
      .count { grid-column: 2; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <form id="analyze-form" method="post" action="{{ url_for('analyze') }}">
      <input type="hidden" name="chat_id" id="chat_id" value="">
      <input type="hidden" name="since" id="since" value="">
      <input type="hidden" name="until" id="until" value="">

      <header>
        <div class="header-copy">
          <div class="kicker">Local · read-only</div>
          <h1>Pick a chat</h1>
          <div class="meta">{{ your_name }} · {{ tz_label }} · {{ group_count }} groups · {{ dm_count }} one-on-ones · newest first</div>
        </div>
        <div class="header-actions">
          <details class="options" id="options-panel">
            <summary>Options</summary>
            <div class="opts-panel">
              <div class="opts">
                <label class="span2">Keywords (comma-separated)
                  <input type="text" name="keywords" value="{{ keywords }}">
                </label>
              </div>
              <p class="hint">Date range is set per chat when you pick one.</p>
            </div>
          </details>
          <details class="options" id="export-panel">
            <summary>Export</summary>
            <div class="opts-panel">
              <div class="opts">
                <label class="span2">Format
                  <select name="export" id="export-format">
                    <option value="csv,json" {% if export == 'csv,json' %}selected{% endif %}>CSV + JSON</option>
                    <option value="csv" {% if export == 'csv' %}selected{% endif %}>CSV only</option>
                    <option value="json" {% if export == 'json' %}selected{% endif %}>JSON only</option>
                    <option value="none" {% if export == 'none' %}selected{% endif %}>None</option>
                  </select>
                </label>
              </div>
              <div class="export-actions">
                <span class="btn">Pick a chat to analyze</span>
              </div>
              <p class="hint">Choosing a chat writes the files, then use <strong>Export</strong> on the report to download CSV/JSON.</p>
            </div>
          </details>
        </div>
      </header>

      {% with messages = get_flashed_messages() %}
      {% if messages %}
      {% for m in messages %}<div class="flash">{{ m }}</div>{% endfor %}
      {% endif %}
      {% endwith %}

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
    </form>

    <div class="modal-backdrop" id="date-modal">
      <div class="modal" role="dialog" aria-labelledby="modal-title">
        <h2 id="modal-title">Date range</h2>
        <div class="modal-meta" id="modal-meta">Loading…</div>
        <div class="presets" id="presets">
          <button type="button" data-preset="all" class="active">All time</button>
          <button type="button" data-preset="7">Last 7 days</button>
          <button type="button" data-preset="30">Last 30 days</button>
          <button type="button" data-preset="90">Last 90 days</button>
          <button type="button" data-preset="365">Last year</button>
          <button type="button" data-preset="ytd">This year</button>
        </div>
        <div class="range-block">
          <div class="range-labels">
            <span id="range-start-label">—</span>
            <span id="range-end-label">—</span>
          </div>
          <div class="range-track">
            <input type="range" id="range-start" min="0" max="0" value="0">
            <input type="range" id="range-end" min="0" max="0" value="0">
          </div>
          <div class="range-summary" id="range-summary">All messages in this chat.</div>
        </div>
        <div class="modal-actions">
          <button type="button" id="modal-cancel">Cancel</button>
          <button type="button" class="primary" id="modal-analyze" disabled>Analyze</button>
        </div>
      </div>
    </div>
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
      var sinceInput = document.getElementById("since");
      var untilInput = document.getElementById("until");
      var busy = document.getElementById("busy");
      var active = "group";
      var optionsPanel = document.getElementById("options-panel");
      var exportPanel = document.getElementById("export-panel");
      var modal = document.getElementById("date-modal");
      var modalTitle = document.getElementById("modal-title");
      var modalMeta = document.getElementById("modal-meta");
      var presets = document.getElementById("presets");
      var rangeStart = document.getElementById("range-start");
      var rangeEnd = document.getElementById("range-end");
      var rangeStartLabel = document.getElementById("range-start-label");
      var rangeEndLabel = document.getElementById("range-end-label");
      var rangeSummary = document.getElementById("range-summary");
      var modalCancel = document.getElementById("modal-cancel");
      var modalAnalyze = document.getElementById("modal-analyze");
      var rangeCache = {};
      var currentRange = null;
      var activePreset = "all";

      if (optionsPanel && exportPanel) {
        optionsPanel.addEventListener("toggle", function () {
          if (optionsPanel.open) exportPanel.open = false;
        });
        exportPanel.addEventListener("toggle", function () {
          if (exportPanel.open) optionsPanel.open = false;
        });
      }

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

      function parseYmd(s) {
        var p = s.split("-");
        return new Date(+p[0], +p[1] - 1, +p[2]);
      }
      function formatYmd(d) {
        return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
      }
      function formatPretty(d) {
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
      }
      function addDays(d, n) {
        var x = new Date(d.getTime());
        x.setDate(x.getDate() + n);
        return x;
      }
      function daysBetween(a, b) {
        var ms = Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate());
        return Math.round(ms / 86400000);
      }
      function dayToDate(minDate, offset) {
        return addDays(minDate, offset);
      }

      function setPresetActive(name) {
        activePreset = name;
        presets.querySelectorAll("button").forEach(function (btn) {
          btn.classList.toggle("active", btn.getAttribute("data-preset") === name);
        });
      }

      function updateRangeUI() {
        if (!currentRange) return;
        var minDate = parseYmd(currentRange.min_date);
        var maxDate = parseYmd(currentRange.max_date);
        var totalDays = daysBetween(minDate, maxDate);
        var startIdx = Math.min(+rangeStart.value, +rangeEnd.value);
        var endIdx = Math.max(+rangeStart.value, +rangeEnd.value);
        rangeStart.value = startIdx;
        rangeEnd.value = endIdx;
        var startDate = dayToDate(minDate, startIdx);
        var endDate = dayToDate(minDate, endIdx);
        rangeStartLabel.textContent = formatPretty(startDate);
        rangeEndLabel.textContent = formatPretty(endDate);
        var allTime = startIdx === 0 && endIdx === totalDays;
        if (allTime) {
          rangeSummary.textContent = "All " + currentRange.messages.toLocaleString() + " messages in this chat.";
          sinceInput.value = "";
          untilInput.value = "";
        } else {
          var spanDays = endIdx - startIdx + 1;
          rangeSummary.textContent = spanDays.toLocaleString() + " day" + (spanDays === 1 ? "" : "s") + " selected.";
          sinceInput.value = formatYmd(startDate);
          untilInput.value = formatYmd(endDate);
        }
      }

      function applyPreset(name) {
        if (!currentRange) return;
        setPresetActive(name);
        var minDate = parseYmd(currentRange.min_date);
        var maxDate = parseYmd(currentRange.max_date);
        var totalDays = daysBetween(minDate, maxDate);
        var startIdx = 0;
        var endIdx = totalDays;
        if (name === "all") {
          startIdx = 0;
          endIdx = totalDays;
        } else if (name === "ytd") {
          var ytdStart = new Date(maxDate.getFullYear(), 0, 1);
          startIdx = Math.max(0, daysBetween(minDate, ytdStart));
          endIdx = totalDays;
        } else {
          var days = parseInt(name, 10);
          startIdx = Math.max(0, totalDays - days + 1);
          endIdx = totalDays;
        }
        rangeStart.value = startIdx;
        rangeEnd.value = endIdx;
        updateRangeUI();
      }

      function openModal(row) {
        var id = row.getAttribute("data-id");
        var label = row.querySelector(".name").textContent;
        chatId.value = id;
        modalTitle.textContent = label;
        modalMeta.textContent = "Choose a date range";
        modalAnalyze.disabled = true;
        rangeStart.disabled = true;
        rangeEnd.disabled = true;
        modal.classList.add("open");

        function ready(data) {
          currentRange = data;
          rangeCache[id] = data;
          var minDate = parseYmd(data.min_date);
          var maxDate = parseYmd(data.max_date);
          var totalDays = daysBetween(minDate, maxDate);
          modalMeta.textContent = (data.kind === "dm" ? "1:1" : "Group") + " · " + data.messages.toLocaleString() + " messages · " + formatPretty(minDate) + " – " + formatPretty(maxDate);
          rangeStart.min = 0;
          rangeEnd.min = 0;
          rangeStart.max = totalDays;
          rangeEnd.max = totalDays;
          rangeStart.disabled = false;
          rangeEnd.disabled = false;
          modalAnalyze.disabled = false;
          applyPreset("all");
        }

        if (rangeCache[id]) {
          ready(rangeCache[id]);
          return;
        }

        fetch("/api/chats/" + encodeURIComponent(id) + "/range")
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error(res.body.error || "Could not load date range");
            ready(res.body);
          })
          .catch(function (err) {
            modalMeta.textContent = err.message || "Could not load date range.";
          });
      }

      function closeModal() {
        modal.classList.remove("open");
        currentRange = null;
      }

      presets.addEventListener("click", function (e) {
        var btn = e.target.closest("button[data-preset]");
        if (!btn) return;
        applyPreset(btn.getAttribute("data-preset"));
      });
      rangeStart.addEventListener("input", function () {
        setPresetActive("");
        if (+rangeStart.value > +rangeEnd.value) rangeEnd.value = rangeStart.value;
        updateRangeUI();
      });
      rangeEnd.addEventListener("input", function () {
        setPresetActive("");
        if (+rangeEnd.value < +rangeStart.value) rangeStart.value = rangeEnd.value;
        updateRangeUI();
      });
      modalCancel.addEventListener("click", closeModal);
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeModal();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && modal.classList.contains("open")) closeModal();
      });
      modalAnalyze.addEventListener("click", function () {
        if (!chatId.value) return;
        closeModal();
        busy.classList.add("on");
        form.submit();
      });

      document.querySelectorAll(".row").forEach(function (row) {
        row.addEventListener("click", function () { openModal(row); });
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


@app.get("/api/chats/<int:chat_id>/range")
def api_chat_range(chat_id):
    mapper, cfg = get_mapper()
    conn = get_connection(cfg.db_path)
    try:
        chat = find_chat_by_id(conn, mapper, chat_id, tz=cfg.tz)
        if not chat:
            return jsonify({"error": "Chat not found"}), 404
        chat_ids = chat.get("chat_ids") or [chat["chat_id"]]
        min_dt, max_dt = get_chat_date_range(conn, chat_ids, cfg.tz)
        if min_dt is None or max_dt is None:
            return jsonify({"error": "No messages in this chat"}), 404
        min_local = min_dt.astimezone(cfg.tz) if getattr(min_dt, "tzinfo", None) else min_dt
        max_local = max_dt.astimezone(cfg.tz) if getattr(max_dt, "tzinfo", None) else max_dt
        return jsonify({
            "chat_id": chat_id,
            "label": chat["label"],
            "kind": chat.get("kind", "group"),
            "messages": int(chat.get("messages") or 0),
            "min_date": min_local.strftime("%Y-%m-%d"),
            "max_date": max_local.strftime("%Y-%m-%d"),
        })
    finally:
        conn.close()


@app.post("/analyze")
def analyze():
    mapper, base_cfg = get_mapper()
    chat_id = request.form.get("chat_id", "").strip()
    if not chat_id:
        flash("Pick a chat from the list.")
        return redirect(url_for("home"))

    since = request.form.get("since") or None
    until = request.form.get("until") or None
    keywords = request.form.get("keywords")
    export = request.form.get("export") or "csv,json"
    if since == "":
        since = None
    if until == "":
        until = None

    try:
        cfg = Config.for_app(
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
