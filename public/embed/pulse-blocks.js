/*
 * Pulse page blocks — turn <pulse-question> / <pulse-grid> in any web page
 * into answer fields that save to Pulse with the respondent's private link.
 *
 *   <script src="https://pulse.axiolo.com/embed/pulse-blocks.js" defer></script>
 *
 * The respondent opens the page with ?t=<token> (the link Pulse gives each
 * respondent). Answers autosave; the page shows "Saved". Without a token the
 * blocks render read-only. The markup contract (ids, rows, columns) is
 * documented in api/pulse_api/page_blocks.py — the server parses the same
 * markup when the page is synced into an engagement.
 *
 * Styling: override --pulse-accent, --pulse-border, --pulse-radius,
 * --pulse-muted on any ancestor. A grid can reuse the page's own table
 * styles with table-class="…" / wrap-class="…", label its first column with
 * row-header="…", and give a column a tooltip with tip="…". Each block gets data-pulse-state
 * ("empty" | "answered" | "saving" | "error") for page CSS to hook into.
 */
(function () {
  "use strict";
  if (window.__pulseBlocks) return;
  window.__pulseBlocks = true;

  var script = document.currentScript;
  var API =
    (script && script.getAttribute("data-api")) ||
    (script && script.src ? new URL(script.src).origin : location.origin);
  var SAVE_DELAY = 900;
  var POLL_MS = 20000;

  // ── token ────────────────────────────────────────────────────────────
  function readToken() {
    var t = new URLSearchParams(location.search).get("t");
    try {
      if (t) sessionStorage.setItem("pulse:t", t);
      else t = sessionStorage.getItem("pulse:t");
    } catch (e) {
      /* storage blocked: the URL token still works */
    }
    return t && /^[0-9a-f]{16}$/.test(t) ? t : null;
  }
  var TOKEN = readToken();

  // ── styles ───────────────────────────────────────────────────────────
  var css =
    "pulse-question,pulse-grid{display:block}" +
    "pulse-row,pulse-col{display:none}" +
    ".pulse-field{margin-top:.75rem}" +
    ".pulse-field textarea,.pulse-grid input,.pulse-grid select{font:inherit;color:inherit;" +
    "background:#fff;border:1px solid var(--pulse-border,#d4d4d4);border-radius:var(--pulse-radius,8px);" +
    "box-sizing:border-box;width:100%}" +
    ".pulse-field textarea{padding:.6rem .75rem;min-height:4.5rem;resize:vertical;line-height:1.5}" +
    ".pulse-field textarea:focus,.pulse-grid input:focus,.pulse-grid select:focus{outline:2px solid var(--pulse-accent,#2960f6);outline-offset:1px;border-color:transparent}" +
    ".pulse-field textarea:disabled,.pulse-grid input:disabled,.pulse-grid select:disabled{background:#f5f5f5;cursor:not-allowed}" +
    ".pulse-status{display:block;min-height:1.2em;margin-top:.35rem;font-size:.8rem;color:var(--pulse-muted,#737373)}" +
    "[data-pulse-state=error] .pulse-status{color:#b91c1c}" +
    ".pulse-grid-wrap{overflow-x:auto;margin-top:.75rem;-webkit-overflow-scrolling:touch}" +
    ".pulse-grid{border-collapse:collapse;width:100%;min-width:640px;font-size:.9rem}" +
    ".pulse-grid th,.pulse-grid td{border-bottom:1px solid var(--pulse-border,#e5e5e5);padding:.4rem;text-align:left;vertical-align:middle}" +
    ".pulse-grid thead th{font-weight:600;font-size:.8rem;color:var(--pulse-muted,#525252)}" +
    ".pulse-grid tbody th{font-weight:500;min-width:10rem}" +
    ".pulse-grid input,.pulse-grid select{padding:.35rem .5rem;min-width:5rem}" +
    ".pulse-badge{position:fixed;right:16px;bottom:16px;z-index:2147483000;display:flex;gap:.5rem;align-items:center;" +
    "max-width:calc(100vw - 32px);padding:.5rem .8rem;border-radius:999px;background:#111;color:#fff;" +
    "font:500 13px/1.3 system-ui,-apple-system,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.18)}" +
    ".pulse-badge-dot{width:8px;height:8px;border-radius:50%;background:#22c55e;flex:none}" +
    ".pulse-badge[data-tone=busy] .pulse-badge-dot{background:#facc15}" +
    ".pulse-badge[data-tone=warn] .pulse-badge-dot{background:#f87171}" +
    ".pulse-badge[data-tone=info] .pulse-badge-dot{background:#a3a3a3}";
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ── page badge (who's answering + overall save state) ────────────────
  var badge = null;
  function setBadge(text, tone) {
    if (!badge) {
      badge = document.createElement("div");
      badge.className = "pulse-badge";
      badge.setAttribute("role", "status");
      badge.innerHTML = '<span class="pulse-badge-dot"></span><span></span>';
      document.body.appendChild(badge);
    }
    badge.setAttribute("data-tone", tone || "ok");
    badge.lastChild.textContent = text;
  }

  var respondent = null;
  var pending = 0;
  var failed = 0;
  function refreshBadge() {
    if (!TOKEN) return;
    var who = respondent
      ? (SHARED ? "Shared answers · editing as " : "Answering as ") + respondent
      : "Your answers";
    if (failed) setBadge("Some answers didn't save. Check your connection.", "warn");
    else if (pending) setBadge(who + " · Saving…", "busy");
    else setBadge(who + " · All changes saved", "ok");
  }

  // ── API ──────────────────────────────────────────────────────────────
  function api(path, body) {
    return fetch(API + path, {
      method: body ? "POST" : "GET",
      headers: Object.assign(
        { "X-Pulse-Token": TOKEN },
        body ? { "Content-Type": "application/json" } : {},
      ),
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (res) {
      if (!res.ok) {
        var err = new Error("HTTP " + res.status);
        err.status = res.status;
        throw err;
      }
      return res.json();
    });
  }

  // ── autosave per block ───────────────────────────────────────────────
  // take() returns { value, ack }: the change to send now, and a callback
  // run with the server's reply once it's saved (so a grid can forget the
  // cells it just sent unless they were edited again meanwhile).
  function makeSaver(el, blockId, take, onSaved) {
    var timer = null;
    var inflight = false;
    var again = false;
    var status = el.querySelector(".pulse-status");

    function show(state, text) {
      el.setAttribute("data-pulse-state", state);
      if (status) status.textContent = text;
    }

    function flush(attempt) {
      clearTimeout(timer);
      timer = null;
      if (inflight) {
        again = true;
        return;
      }
      var job = take();
      inflight = true;
      again = false;
      pending++;
      refreshBadge();
      show("saving", "Saving…");
      api("/api/embed/responses", { block_id: blockId, value: job.value })
        .then(function (res) {
          pending--;
          inflight = false;
          if (el.__pulseFailed) {
            el.__pulseFailed = false;
            failed--;
          }
          if (job.ack) job.ack(res);
          var answered = res.state === "answered";
          show(answered ? "answered" : "empty", answered ? "Saved" : "");
          if (onSaved) onSaved(res);
          refreshBadge();
          if (again) flush(0);
        })
        .catch(function (err) {
          pending--;
          inflight = false;
          var retryable = !err.status || err.status >= 500 || err.status === 429;
          if (retryable && (attempt || 0) < 3) {
            setTimeout(function () {
              flush((attempt || 0) + 1);
            }, 1500 * Math.pow(2, attempt || 0));
            refreshBadge();
            return;
          }
          if (!el.__pulseFailed) {
            el.__pulseFailed = true;
            failed++;
          }
          show("error", "Couldn't save this answer. Check your connection and try again.");
          refreshBadge();
        });
    }

    return {
      schedule: function () {
        clearTimeout(timer);
        show("saving", "Saving…");
        timer = setTimeout(flush, SAVE_DELAY);
      },
      flushNow: function () {
        if (timer) flush(0);
      },
      isPending: function () {
        return !!timer || inflight;
      },
    };
  }

  var savers = [];
  window.addEventListener("beforeunload", function (e) {
    savers.forEach(function (s) {
      s.flushNow();
    });
    if (savers.some(function (s) {
      return s.isPending();
    })) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // ── shared answers ───────────────────────────────────────────────────
  var SHARED = false;
  var live = {}; // block id → applyRemote(state), for shared refreshes

  function when(iso) {
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (e) {
      return "";
    }
  }

  function showMeta(el, state) {
    if (!SHARED) return;
    var st = el.getAttribute("data-pulse-state");
    if (st === "saving" || st === "error") return;
    var status = el.querySelector(".pulse-status");
    if (!status) return;
    status.textContent =
      state && state.updated_by
        ? "Last changed by " + state.updated_by + " · " + when(state.updated_at)
        : "";
  }

  function poll() {
    if (!SHARED || document.hidden) return;
    api("/api/embed/blocks")
      .then(function (data) {
        var blocks = data.blocks || {};
        Object.keys(live).forEach(function (id) {
          if (blocks[id]) live[id](blocks[id]);
        });
      })
      .catch(function () {
        /* next poll retries */
      });
  }

  // ── blocks ───────────────────────────────────────────────────────────
  function statusLine() {
    var s = document.createElement("span");
    s.className = "pulse-status";
    s.setAttribute("aria-live", "polite");
    return s;
  }

  function renderQuestion(el, state) {
    var id = el.id;
    var wrap = document.createElement("div");
    wrap.className = "pulse-field";
    var ta = document.createElement("textarea");
    ta.rows = 3;
    ta.placeholder = el.getAttribute("placeholder") || "Type your answer…";
    ta.setAttribute(
      "aria-label",
      el.getAttribute("prompt") || el.textContent.replace(/\s+/g, " ").trim().slice(0, 200),
    );
    wrap.appendChild(ta);
    wrap.appendChild(statusLine());
    el.appendChild(wrap);
    if (!state) {
      ta.disabled = true;
      return;
    }
    function textOf(st) {
      return st && st.value && typeof st.value.text === "string" ? st.value.text : "";
    }
    ta.value = textOf(state);
    el.setAttribute("data-pulse-state", ta.value ? "answered" : "empty");
    showMeta(el, state);
    var saver = makeSaver(
      el,
      id,
      function () {
        return { value: { text: ta.value } };
      },
      function (res) {
        showMeta(el, res);
      },
    );
    savers.push(saver);
    ta.addEventListener("input", saver.schedule);
    ta.addEventListener("blur", saver.flushNow);
    live[id] = function (st) {
      // Never overwrite what this person is typing.
      if (document.activeElement === ta || saver.isPending()) return;
      var v = textOf(st);
      if (ta.value !== v) ta.value = v;
      el.setAttribute("data-pulse-state", v ? "answered" : "empty");
      showMeta(el, st);
    };
  }

  function slug(label) {
    return (
      label
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 64) || "item"
    );
  }

  function items(el, tag) {
    return Array.prototype.map.call(el.querySelectorAll(tag), function (n) {
      var label = (n.getAttribute("label") || "").replace(/\s+/g, " ").trim();
      var opts = (n.getAttribute("options") || "")
        .split("|")
        .map(function (o) {
          return o.trim();
        })
        .filter(Boolean);
      return {
        key: slug(n.getAttribute("key") || label),
        label: label,
        options: opts,
        tip: n.getAttribute("tip") || "",
      };
    });
  }

  function renderGrid(el, state) {
    var rows = items(el, "pulse-row");
    var cols = items(el, "pulse-col");
    var wrap = document.createElement("div");
    wrap.className = "pulse-grid-wrap " + (el.getAttribute("wrap-class") || "");
    var table = document.createElement("table");
    table.className = "pulse-grid " + (el.getAttribute("table-class") || "");
    table.innerHTML =
      "<thead><tr><th scope='col'></th>" +
      new Array(cols.length + 1).join("<th scope='col'></th>") +
      "</tr></thead><tbody></tbody>";
    var ths = table.querySelectorAll("thead th");
    ths[0].textContent = el.getAttribute("row-header") || "";
    cols.forEach(function (c, i) {
      ths[i + 1].textContent = c.label;
      if (c.tip) ths[i + 1].title = c.tip;
    });
    var tbody = table.querySelector("tbody");
    var inputs = {}; // "row|col" → input
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var th = document.createElement("th");
      th.scope = "row";
      th.textContent = r.label;
      tr.appendChild(th);
      cols.forEach(function (c) {
        var td = document.createElement("td");
        var input;
        if (c.options.length) {
          input = document.createElement("select");
          var blank = document.createElement("option");
          blank.value = "";
          blank.textContent = "—";
          input.appendChild(blank);
          c.options.forEach(function (o) {
            var opt = document.createElement("option");
            opt.value = o;
            opt.textContent = o;
            input.appendChild(opt);
          });
        } else {
          input = document.createElement("input");
          input.type = "text";
        }
        input.setAttribute("aria-label", r.label + ", " + c.label);
        input.disabled = !state;
        input.__pulse = { row: r.key, col: c.key };
        inputs[r.key + "|" + c.key] = input;
        td.appendChild(input);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    wrap.appendChild(table);
    el.appendChild(wrap);
    el.appendChild(statusLine());
    if (!state) return;

    var edits = {}; // "row|col" → edit version not yet confirmed saved
    var version = 0;
    function cellsOf(st) {
      return (st && st.value && st.value.cells) || {};
    }
    function fill(st, force) {
      var cells = cellsOf(st);
      Object.keys(inputs).forEach(function (k) {
        var i = inputs[k];
        if (!force && (edits[k] || document.activeElement === i)) return;
        var v = (cells[i.__pulse.row] && cells[i.__pulse.row][i.__pulse.col]) || "";
        if (i.value !== v) i.value = v;
      });
      var any = Object.keys(inputs).some(function (k) {
        return inputs[k].value;
      });
      el.setAttribute("data-pulse-state", any ? "answered" : "empty");
    }
    fill(state, true);
    showMeta(el, state);

    var saver = makeSaver(
      el,
      el.id,
      function () {
        // Send only the cells changed since the last save ("" clears one),
        // so people editing different rows don't overwrite each other.
        var sent = {};
        var cells = {};
        Object.keys(edits).forEach(function (k) {
          var i = inputs[k];
          sent[k] = edits[k];
          (cells[i.__pulse.row] = cells[i.__pulse.row] || {})[i.__pulse.col] = i.value;
        });
        return {
          value: { cells: cells },
          ack: function () {
            Object.keys(sent).forEach(function (k) {
              if (edits[k] === sent[k]) delete edits[k];
            });
          },
        };
      },
      function (res) {
        fill(res, false); // pick up other people's cells in the merged answer
        showMeta(el, res);
      },
    );
    savers.push(saver);
    Object.keys(inputs).forEach(function (k) {
      var i = inputs[k];
      i.addEventListener(i.tagName === "SELECT" ? "change" : "input", function () {
        edits[k] = ++version;
        saver.schedule();
      });
      i.addEventListener("blur", saver.flushNow);
    });
    live[el.id] = function (st) {
      if (saver.isPending()) return;
      fill(st, false);
      showMeta(el, st);
    };
  }

  function renderAll(blocks) {
    var els = document.querySelectorAll("pulse-question[id], pulse-grid[id]");
    var missing = 0;
    Array.prototype.forEach.call(els, function (el) {
      if (el.__pulseRendered) return;
      el.__pulseRendered = true;
      var state = blocks ? blocks[el.id] : null;
      if (blocks && !state) missing++;
      if (el.tagName === "PULSE-GRID") renderGrid(el, state);
      else renderQuestion(el, state);
    });
    if (missing && window.console) {
      console.warn("[pulse] " + missing + " block(s) on this page aren't synced to the engagement.");
    }
  }

  function start() {
    if (!TOKEN) {
      renderAll(null);
      setBadge("View only. Open the personal link you were sent to answer.", "info");
      return;
    }
    setBadge("Loading your answers…", "busy");
    api("/api/embed/blocks")
      .then(function (data) {
        respondent = data.respondent || null;
        SHARED = !!data.shared;
        renderAll(data.blocks || {});
        refreshBadge();
        if (SHARED) {
          // Everyone edits the same answers: pick up other people's changes.
          setInterval(poll, POLL_MS);
          document.addEventListener("visibilitychange", poll);
        }
      })
      .catch(function (err) {
        renderAll(null);
        setBadge(
          err.status === 404
            ? "This link isn't valid anymore. Ask for a new one."
            : "Couldn't load your answers. Refresh to try again.",
          "warn",
        );
      });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
