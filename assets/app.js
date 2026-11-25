/* Trade Query Copilot — in-browser demo.
   Python (Pyodide) runs copilot.local_analyst + copilot.guard; sql.js runs the SQL. */
(() => {
  "use strict";

  const PYODIDE = "https://cdn.jsdelivr.net/npm/pyodide@0.27.7/";
  const SQLJS = "https://cdn.jsdelivr.net/npm/sql.js@1.13.0/dist/";
  const WHEEL = "py/sqlglot-30.35.0-py3-none-any.whl";
  const CHECKS = ["single statement", "read-only", "allow-listed tables", "safe functions", "row limit"];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
  const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  const S = { ready: false, py: null, db: null, busy: false, waiting: [] };

  const SUGGEST = {
    Settlement: ["Which trades over $1M failed to settle this week, by counterparty?", "Failed trades by desk",
      "List pending trades over $2M", "Average days failing by counterparty tier"],
    Positions: ["Traded vs settled market value by account", "Which securities have unsettled quantity?",
      "Top 10 holdings by market value", "Market value by asset class"],
    Breaks: ["Open breaks by owner team", "Aged breaks over 5 days by account", "What's the largest open break?"],
    Activity: ["Top 5 counterparties by notional traded this month", "Daily notional this week",
      "Notional traded by currency this month"],
  };
  const HERO_QS = ["Which trades over $1M failed to settle this week, by counterparty?",
    "Traded vs settled market value by account", "Open breaks by owner team",
    "Top 5 counterparties by notional traded this month", "Which accounts hold NVDA?"];
  const ATTACKS = [
    ["Failed by desk", "SELECT a.desk, COUNT(*) AS failed_trades\nFROM TRADE_OPS.CORE.FACT_TRADES t\nJOIN TRADE_OPS.CORE.FACT_SETTLEMENTS s ON s.trade_id = t.trade_id\nJOIN TRADE_OPS.CORE.DIM_ACCOUNT a ON a.account_id = t.account_id\nWHERE s.settlement_status = 'FAILED'\nGROUP BY a.desk", false],
    ["No LIMIT", "SELECT trade_id, notional_usd\nFROM TRADE_OPS.CORE.FACT_TRADES\nORDER BY notional_usd DESC", false],
    ["DROP TABLE", "DROP TABLE TRADE_OPS.CORE.FACT_TRADES", true],
    ["Delete trades", "DELETE FROM TRADE_OPS.CORE.FACT_TRADES\nWHERE trade_date < '2026-11-01'", true],
    ["Sneaky second statement", "SELECT COUNT(*) FROM TRADE_OPS.CORE.FACT_TRADES;\nDROP TABLE TRADE_OPS.CORE.FACT_BREAKS", true],
    ["Account usage", "SELECT query_text, user_name\nFROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY", true],
    ["Off the allow-list", "SELECT * FROM TRADE_OPS.CORE.EMPLOYEE_COMP", true],
    ["SYSTEM$ call", "SELECT SYSTEM$ALLOWLIST()", true],
    ["Copy data out", "COPY INTO @~/dump FROM TRADE_OPS.CORE.FACT_TRADES", true],
  ];

  // ------------------------------------------------------------ utilities
  const MONEY = /(usd|notional|market_value|amount)/i;
  function fmt(v, col = "", compact = false) {
    if (v === null || v === undefined) return '<span class="null">null</span>';
    if (typeof v === "number") {
      if (/^pct|percent|rate/i.test(col)) return `${v.toFixed(1)}%`;
      if (MONEY.test(col)) {
        if (compact) {
          const a = Math.abs(v), s = v < 0 ? "−" : "";
          if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
          if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
          if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}k`;
          return `${s}$${a.toFixed(0)}`;
        }
        return (v < 0 ? "−$" : "$") + nf2.format(Math.abs(v));
      }
      return Number.isInteger(v) ? nf0.format(v) : (Math.abs(v) < 100 ? (+v.toFixed(2)).toString() : nf2.format(v));
    }
    return esc(v);
  }
  function hl(sql) {
    return esc(sql).replace(/(&#39;(?:[^&]|&(?!#39;))*?&#39;)|(\b\d+(?:\.\d+)?\b)|(\b(?:SELECT|FROM|WHERE|AND|OR|LEFT|RIGHT|INNER|JOIN|ON|AS|UNION|ALL|ORDER|BY|LIMIT|IS|NULL|NOT|IN|DESC|ASC|CASE|WHEN|THEN|ELSE|END|WITH|GROUP|HAVING|COUNT|SUM|AVG|DISTINCT|BETWEEN|NULLS|FIRST|LAST)\b)/g,
      (m, str, num, kw) => (str ? `<span class="str">${str}</span>` : num ? `<span class="num">${num}</span>` : `<span class="kw">${kw}</span>`));
  }
  function loadScript(src) {
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.async = true; s.crossOrigin = "anonymous";
      s.onload = res; s.onerror = () => rej(new Error(`couldn't load ${src}`));
      document.head.append(s);
    });
  }
  const fetchBuf = (u) => fetch(u).then((r) => { if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.arrayBuffer(); });
  const fetchJson = (u) => fetch(u).then((r) => { if (!r.ok) throw new Error(`${u}: ${r.status}`); return r.json(); });
  const tick = svg("M5 12.5 10 17l9-10"), cross = svg("M7 7l10 10M17 7 7 17");
  function svg(d, size = 12) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }

  // ------------------------------------------------------------ theme + tooltip
  function initTheme() {
    const saved = store.get("tqc.theme", null);
    if (saved) document.documentElement.dataset.theme = saved;
    else if (window.matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.dataset.theme = "dark";
    $("#theme").addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      store.set("tqc.theme", next);
    });
  }

  // ------------------------------------------------------------ engine
  function setEngine(text, pct, state) {
    $("#engineText").textContent = text;
    $("#engineBar").style.width = `${pct}%`;
    const e = $("#engine");
    e.classList.toggle("ready", state === "ready");
    e.classList.toggle("error", state === "error");
  }

  async function bootEngine() {
    try {
      setEngine("Downloading Python for the browser (Pyodide)…", 6);
      await loadScript(PYODIDE + "pyodide.js");
      setEngine("Starting Python…", 28);
      const py = await window.loadPyodide({ indexURL: PYODIDE });
      setEngine("Loading sqlglot and the copilot package…", 62);
      const [whl, pkg] = await Promise.all([fetchBuf(WHEEL), fetchBuf("py/copilot.zip")]);
      py.FS.mkdirTree("/home/pyodide/lib");
      py.unpackArchive(whl, "zip", { extractDir: "/home/pyodide/lib" });
      py.unpackArchive(pkg, "zip", { extractDir: "/home/pyodide/lib" });
      py.runPython("import sys; sys.path.insert(0, '/home/pyodide/lib')");
      py.globals.set("MODEL", JSON.stringify(S.model));
      py.globals.set("GLOSS", JSON.stringify(S.glossary));
      py.globals.set("VALUES", JSON.stringify(S.values));
      py.runPython("from copilot import web\nweb.init(MODEL, GLOSS, VALUES)");
      setEngine("Loading the warehouse snapshot…", 84);
      await loadScript(SQLJS + "sql-wasm.js");
      const SQL = await window.initSqlJs({ locateFile: (f) => SQLJS + f });
      S.db = new SQL.Database(new Uint8Array(await fetchBuf("data/trade_ops.db")));
      S.py = py;
      S.ready = true;
      const pyv = py.runPython("import sys; sys.version.split()[0]");
      setEngine(`Engine ready · Python ${pyv} in your browser · SQLite snapshot of TRADE_OPS.CORE`, 100, "ready");
      $("#pgRun").disabled = false;
      $("#pgNote").textContent = "";
      const pending = S.waiting.splice(0);
      for (const fn of pending) await fn();
    } catch (err) {
      console.error(err);
      setEngine("The in-browser engine couldn't load here. Suggested questions still work from recorded answers.", 0, "error");
      S.failed = true;
      for (const fn of S.waiting.splice(0)) await fn();
    }
  }

  function execSql(sqlite) {
    const stmt = S.db.prepare(sqlite);
    try {
      const columns = stmt.getColumnNames();
      const rows = [];
      while (stmt.step()) rows.push(stmt.get());
      return { columns, rows };
    } finally {
      stmt.free();
    }
  }

  function askLive(q) {
    const t0 = performance.now();
    S.py.globals.set("Q", q);
    const out = JSON.parse(S.py.runPython("web.ask(Q)"));
    out.source = "live";
    out.columns = []; out.rows = [];
    if (!out.sql) out.error = out.warnings[0] || "I couldn't turn that into a query.";
    else if (out.guard && !out.guard.ok) out.error = "Blocked by guardrails: " + (out.guard.checks.find((c) => !c.ok) || {}).detail;
    else {
      try { Object.assign(out, execSql(out.sqlite)); } catch (e) { out.error = String(e.message || e); }
    }
    out.elapsed_ms = +(performance.now() - t0).toFixed(1);
    return out;
  }

  // ------------------------------------------------------------ chat
  async function ask(q) {
    q = q.trim();
    if (!q || S.busy) return;
    S.busy = true;
    $("#askBtn").disabled = true;
    const thread = $("#thread");
    $(".empty-state", thread)?.remove();
    const li = document.createElement("li");
    li.className = "turn";
    li.innerHTML = `<div class="q-bubble">${esc(q)}</div><div class="a-card"><span class="thinking"><i></i><i></i><i></i></span></div>`;
    thread.append(li);
    thread.scrollTop = thread.scrollHeight;
    const run = async () => {
      let a;
      if (S.ready) {
        await sleep(reduceMotion ? 0 : 280);
        a = askLive(q);
      } else if (S.answers[q]) {
        await sleep(reduceMotion ? 0 : 350);
        a = { ...S.answers[q], source: "recorded" };
      } else if (S.failed) {
        a = { error: "The live engine isn't available in this browser. Try one of the suggested questions.", source: "recorded" };
      } else {
        $(".a-card", li).innerHTML = `<span class="thinking"><i></i><i></i><i></i></span> <span class="muted small">Waiting for the engine to finish loading…</span>`;
        S.waiting.push(run);
        return;
      }
      await renderAnswer($(".a-card", li), a);
      S.busy = false;
      $("#askBtn").disabled = false;
    };
    await run();
  }

  async function renderAnswer(card, a) {
    const guard = a.guard;
    const shownSql = guard && guard.ok ? guard.sql : (a.sql || "");
    const rows = a.rows || [], cols = a.columns || [];
    const conf = a.confidence != null && !a.error ? `confidence ${Math.round(a.confidence * 100)}%` : "";
    card.innerHTML = `
      <div class="a-meta">
        <span class="badge ${a.source === "live" ? "" : "pre"}">${a.source === "live" ? "Live · Python in your browser" : "Recorded answer"}</span>
        ${guard ? `<span class="badge ${guard.ok ? "ok" : "bad"}">${guard.ok ? "Passed guardrails" : "Blocked"}</span>` : ""}
        <span>${a.elapsed_ms != null ? `${a.elapsed_ms} ms` : ""}</span><span>${conf}</span>
      </div>
      ${(a.explanation || []).length ? `<ul class="explain">${a.explanation.map((e) => {
        const i = e.indexOf(":");
        return `<li>${i > 0 && i < 40 ? `<b>${esc(e.slice(0, i))}</b>${esc(e.slice(i))}` : esc(e)}</li>`;
      }).join("")}</ul>` : ""}
      <div class="checks"></div>
      ${shownSql ? `<div class="sqlbox"><div class="sqlbar"><span>Snowflake SQL</span>
        ${a.sqlite ? `<div class="seg" role="tablist"><button aria-selected="true" data-v="sf">Snowflake</button><button data-v="lite">SQLite</button></div>` : ""}
        <button class="icon-btn copy" type="button">Copy</button></div><pre class="sql"></pre></div>` : ""}
      <div class="result"></div>
      <div class="a-foot"></div>`;
    const chk = $(".checks", card);
    if (guard) {
      for (const c of guard.checks) {
        chk.insertAdjacentHTML("beforeend", `<span class="chk ${c.ok ? "" : "no"}" title="${esc(c.detail || "")}">${c.ok ? tick : cross}${esc(c.name)}</span>`);
        if (!reduceMotion) await sleep(90);
      }
    }
    const pre = $("pre.sql", card);
    if (pre) {
      if (reduceMotion) pre.innerHTML = hl(shownSql);
      else {
        for (let i = 0; i < shownSql.length; i += 26) {
          pre.textContent = shownSql.slice(0, i + 26);
          await sleep(12);
        }
        pre.innerHTML = hl(shownSql);
      }
      $(".copy", card).addEventListener("click", async (e) => {
        try { await navigator.clipboard.writeText(pre.textContent); e.target.textContent = "Copied"; } catch { e.target.textContent = "Select and copy"; }
        setTimeout(() => { e.target.textContent = "Copy"; }, 1400);
      });
      const seg = $(".seg", card);
      if (seg) seg.addEventListener("click", (e) => {
        const b = e.target.closest("button");
        if (!b) return;
        $$("button", seg).forEach((x) => x.setAttribute("aria-selected", x === b));
        pre.innerHTML = hl(b.dataset.v === "lite" ? a.sqlite : shownSql);
        $(".sqlbar span", card).textContent = b.dataset.v === "lite" ? "Executed here as SQLite (sqlglot transpile)" : "Snowflake SQL";
      });
    }
    const res = $(".result", card);
    if (a.error) {
      res.innerHTML = `<p class="err-line">${esc(a.error)}</p>`;
    } else if (rows.length === 1 && cols.length === 1) {
      res.innerHTML = `<div class="kpi-big"><b>${fmt(rows[0][0], cols[0])}</b><span>${esc(cols[0].replace(/_/g, " "))}</span></div>`;
    } else if (!rows.length) {
      res.innerHTML = '<p class="muted small">No rows matched.</p>';
    } else {
      const num = cols.map((c, i) => rows.every((r) => r[i] === null || typeof r[i] === "number"));
      res.innerHTML = `<div class="tbl-wrap"><table class="rows"><tr>${cols.map((c, i) => `<th class="${num[i] ? "n" : ""}">${esc(c)}</th>`).join("")}</tr>
        ${rows.slice(0, 200).map((r, k) => `<tr style="animation-delay:${Math.min(k, 12) * 28}ms">${r.map((v, i) => `<td class="${num[i] ? "n" : ""}">${fmt(v, cols[i])}</td>`).join("")}</tr>`).join("")}</table></div>`;
      chart(res, cols, rows, num);
    }
    const foot = $(".a-foot", card);
    foot.innerHTML = `${rows.length ? `<span>${rows.length} row${rows.length === 1 ? "" : "s"}</span>` : ""}`
      + (a.warnings || []).filter((w) => w !== a.error).map((w) => `<span class="warn-line">${esc(w)}</span>`).join("");
    const thread = $("#thread");
    thread.scrollTop = thread.scrollHeight;
  }

  function chart(host, cols, rows, num) {
    if (rows.length < 2 || rows.length > 25 || num[0]) return;
    const vi = num.findIndex((x, i) => x && i > 0);
    if (vi < 0) return;
    const vals = rows.map((r) => r[vi] ?? 0);
    const max = Math.max(...vals.map(Math.abs)) || 1;
    const isDate = rows.every((r) => /^\d{4}-\d{2}-\d{2}$/.test(String(r[0])));
    const box = document.createElement("div");
    if (isDate) {
      box.className = "cols";
      box.innerHTML = rows.map((r, i) => `<div class="col"><b>${fmt(vals[i], cols[vi], true)}</b><i data-h="${Math.abs(vals[i]) / max * 100}"></i><span>${esc(String(r[0]).slice(5))}</span></div>`).join("");
    } else {
      box.className = "chart";
      box.innerHTML = rows.map((r, i) => `<div class="crow"><span title="${esc(r[0])}">${esc(r[0] ?? "null")}</span><span class="cb"><i class="${vals[i] < 0 ? "neg" : ""}" data-w="${Math.abs(vals[i]) / max * 100}"></i></span><b>${fmt(vals[i], cols[vi], true)}</b></div>`).join("");
    }
    host.append(box);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      $$("i[data-w]", box).forEach((i) => { i.style.width = `${i.dataset.w}%`; });
      $$("i[data-h]", box).forEach((i) => { i.style.height = `${Math.max(2, i.dataset.h * 0.8)}%`; });
    }));
  }

  function renderSuggestions() {
    $("#suggest").innerHTML = Object.entries(SUGGEST).map(([g, qs]) => `<div class="sgroup"><h4>${g}</h4>${qs.map((q) => `<button class="sug" type="button">${esc(q)}</button>`).join("")}</div>`).join("");
    $("#suggest").addEventListener("click", (e) => {
      const b = e.target.closest(".sug");
      if (!b) return;
      if (window.innerWidth < 1100) $(".chat-pane").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
      ask(b.textContent);
    });
    const terms = S.glossary.terms;
    $("#terms").innerHTML = terms.map((t, i) => `<button class="term" type="button" data-i="${i}">${esc(t.term)}</button>`).join("") + '<div id="termPop"></div>';
    $("#terms").addEventListener("click", (e) => {
      const b = e.target.closest(".term");
      if (!b) return;
      const t = terms[+b.dataset.i];
      $("#termPop").innerHTML = `<div class="term-pop"><b>${esc(t.term)}</b>${esc(t.definition)}${t.maps_to ? `<code>${esc(t.maps_to)}</code>` : ""}</div>`;
    });
  }

  function emptyState() {
    $("#thread").innerHTML = `<li class="empty-state"><b>Ask about trades, fails, positions or breaks.</b>Try “Which trades over $1M failed to settle this week, by counterparty?” or pick one on the right.</li>`;
  }

  // ------------------------------------------------------------ hero
  async function heroType() {
    const el = $("#heroType");
    let k = 0;
    for (;;) {
      const q = HERO_QS[k % HERO_QS.length];
      $("#heroBox").dataset.q = q;
      if (reduceMotion) { el.textContent = q; await sleep(4000); k++; continue; }
      for (let i = 1; i <= q.length; i++) { el.textContent = q.slice(0, i); await sleep(28); }
      await sleep(2200);
      for (let i = q.length; i >= 0; i -= 3) { el.textContent = q.slice(0, i); await sleep(12); }
      await sleep(300);
      k++;
    }
  }
  function renderStats() {
    const b = S.bench;
    const vals = { acc: [b.execution_accuracy * 100, 0, "%"], guard: [b.guard_pass_rate * 100, 0, "%"], checks: [5, 0, ""], ms: [b.p50_ms, 1, " ms"] };
    $$("#stats b").forEach((el) => {
      const [to, dec, suf] = vals[el.dataset.k];
      if (reduceMotion) { el.textContent = to.toFixed(dec) + suf; return; }
      const t0 = performance.now();
      const step = (t) => {
        const p = Math.min(1, (t - t0) / 1200), e = 1 - Math.pow(1 - p, 3);
        el.textContent = (to * e).toFixed(dec) + suf;
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  // ------------------------------------------------------------ guard playground
  function renderPlayground() {
    $("#pgExamples").innerHTML = ATTACKS.map(([label, , bad], i) => `<button class="pg-ex ${bad ? "bad" : ""}" type="button" data-i="${i}">${esc(label)}</button>`).join("");
    $("#pgExamples").addEventListener("click", (e) => {
      const b = e.target.closest(".pg-ex");
      if (!b) return;
      $("#pgSql").value = ATTACKS[+b.dataset.i][1];
      runPlayground();
    });
    $("#pgRun").disabled = true;
    $("#pgNote").textContent = "Waiting for the in-browser engine…";
    $("#pgRun").addEventListener("click", runPlayground);
  }
  async function runPlayground() {
    const out = $("#pgOut");
    if (!S.ready) {
      out.innerHTML = '<p class="muted">The guard runs in the in-browser Python engine, which is still loading.</p>';
      return;
    }
    S.py.globals.set("SQLTEXT", $("#pgSql").value);
    const g = JSON.parse(S.py.runPython("web.check(SQLTEXT)"));
    const byName = Object.fromEntries(g.checks.map((c) => [c.name, c]));
    const parseFail = g.checks.find((c) => c.name === "parses");
    const list = (parseFail ? [parseFail] : []).concat(CHECKS.map((n) => byName[n] || { name: n, skip: true }));
    out.innerHTML = `<p class="verdict ${g.ok ? "ok" : "no"}">${g.ok ? "Allowed. Running it." : "Blocked before it reached the warehouse."}</p>
      <ul class="gchecks">${list.map((c, i) => `<li class="${c.skip ? "skip" : c.ok ? "" : "no"}" style="animation-delay:${i * 70}ms">
        <span class="ico">${c.skip ? "·" : c.ok ? tick : cross}</span><span>${esc(c.name)}<small>${esc(c.skip ? "not reached" : c.detail || "ok")}</small></span></li>`).join("")}</ul>
      <div id="pgRes"></div>`;
    if (g.ok) {
      try {
        const r = execSql(g.sqlite);
        $("#pgRes").innerHTML = `<div class="sqlbox"><div class="sqlbar"><span>What actually ran (SQLite, after the guard)</span></div><pre class="sql">${hl(g.sqlite)}</pre></div>
          <div class="tbl-wrap"><table class="rows"><tr>${r.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
          ${r.rows.slice(0, 50).map((row) => `<tr>${row.map((v, i) => `<td>${fmt(v, r.columns[i])}</td>`).join("")}</tr>`).join("")}</table></div>
          <p class="muted small">${r.rows.length} row${r.rows.length === 1 ? "" : "s"}${r.rows.length > 50 ? ", first 50 shown" : ""}</p>`;
      } catch (e) {
        $("#pgRes").innerHTML = `<p class="err-line">${esc(e.message)}</p>`;
      }
    }
  }

  // ------------------------------------------------------------ semantic model
  const POS = {
    settlements: [40, 24], accounts: [280, 24],
    trades: [40, 190], positions: [280, 190], breaks: [520, 190],
    counterparties: [40, 356], securities: [280, 356],
  };
  function renderModel() {
    const W = 140, H = 54;
    const svgEl = $("#erd");
    const center = (n) => [POS[n][0] + W / 2, POS[n][1] + H / 2];
    const edges = S.model.relationships.map((r) => {
      const [x1, y1] = center(r.left_table), [x2, y2] = center(r.right_table);
      const my = (y1 + y2) / 2;
      return `<path class="eedge" data-a="${r.left_table}" data-b="${r.right_table}" d="M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}"/>`;
    }).join("");
    const facts = new Set(["trades", "positions", "breaks", "settlements"]);
    const nodes = S.model.tables.map((t) => {
      const [x, y] = POS[t.name];
      return `<g class="enode ${facts.has(t.name) ? "fact" : ""}" data-t="${t.name}" tabindex="0" role="button" aria-label="${t.name}">
        <rect x="${x}" y="${y}" width="${W}" height="${H}" rx="14"/>
        <text x="${x + 14}" y="${y + 23}">${t.name}</text>
        <text class="sub" x="${x + 14}" y="${y + 40}">${t.base_table.table}</text></g>`;
    }).join("");
    svgEl.innerHTML = edges + nodes;
    const pick = (name) => {
      $$(".enode", svgEl).forEach((g) => g.classList.toggle("sel", g.dataset.t === name));
      $$(".eedge", svgEl).forEach((p) => p.classList.toggle("hi", p.dataset.a === name || p.dataset.b === name));
      tableDetail(S.model.tables.find((t) => t.name === name));
    };
    svgEl.addEventListener("click", (e) => { const g = e.target.closest(".enode"); if (g) pick(g.dataset.t); });
    svgEl.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { const g = e.target.closest(".enode"); if (g) { e.preventDefault(); pick(g.dataset.t); } } });
    pick("trades");
    $("#glossary").innerHTML = S.glossary.terms.map((t) => `<div class="gcard"><h4>${esc(t.term)}</h4><p>${esc(t.definition)}</p>${t.maps_to ? `<code>${esc(t.maps_to)}</code>` : ""}</div>`).join("");
  }
  function tableDetail(t) {
    const sec = (title, items, fn) => (items && items.length ? `<div class="td-sec"><h4>${title}</h4>${items.map(fn).join("")}</div>` : "");
    const chips = (xs, cls = "") => (xs && xs.length ? `<span class="chips">${xs.map((s) => `<span class="chip ${cls}">${esc(s)}</span>`).join("")}</span>` : "");
    const field = (f) => `<div class="field"><code>${esc(f.name)}</code><span>${f.description ? `<span class="muted">${esc(f.description)}</span> ` : ""}${chips(f.synonyms)}${chips(f.sample_values, "v")}</span></div>`;
    const rows = (S.schema.find((x) => x.name === t.name) || {}).rows;
    $("#tableDetail").innerHTML = `<div class="td-head"><h3>${esc(t.name)}</h3>
      <p>${esc(t.description || "")} <span class="mono">${t.base_table.database}.${t.base_table.schema}.${t.base_table.table}</span>${rows != null ? ` · ${nf0.format(rows)} rows` : ""}</p>
      ${chips(t.synonyms)}</div>
      ${sec("Dimensions", (t.dimensions || []).filter((d) => !/_id$/.test(d.name) || d.unique), field)}
      ${sec("Time dimensions", t.time_dimensions, field)}
      ${sec("Facts", t.facts, field)}
      ${sec("Metrics", t.metrics, (m) => `<div class="field"><code>${esc(m.name)}</code><span class="mono">${esc(m.expr)}</span></div>`)}
      ${sec("Filters", t.filters, (f) => `<div class="field"><code>${esc(f.name)}</code><span><span class="mono">${esc(f.expr)}</span> ${chips(f.synonyms)}</span></div>`)}`;
  }

  // ------------------------------------------------------------ benchmark
  function renderBench() {
    const b = S.bench;
    const cats = Object.entries(b.by_category);
    const misses = b.results.filter((r) => !r.passed);
    $("#benchOut").innerHTML = `
      <div class="card">
        <p class="f-label">Execution accuracy · local engine</p>
        <div class="big">${(b.execution_accuracy * 100).toFixed(0)}%</div>
        <p class="muted" style="margin:10px 0 0">${b.passed} of ${b.n} gold questions return exactly the gold rows. ${(b.guard_pass_rate * 100).toFixed(0)}% of generated SQL passed the guardrails.</p>
        <div class="cats">${cats.map(([k, v]) => `<div class="cat"><span>${k}</span><span class="cb"><i data-w="${v.passed / v.n * 100}"></i></span><b>${v.passed}/${v.n}</b></div>`).join("")}</div>
        <p class="note">Misses: ${misses.map((m) => `<b>${m.id}</b> “${esc(m.question)}”`).join(", ")}. The first is a parser gap (it summed instead of listing), the second is a definition fight: currently-failing vs ever-failed.</p>
        <p class="note">This is the rule-based baseline that runs without a Snowflake account. The Cortex Analyst number comes from the same harness: <code>python -m copilot.benchmark --engine cortex</code>.</p>
      </div>
      <div class="card">
        <div class="qfilter seg" role="tablist" aria-label="Filter questions">
          <button aria-selected="true" data-f="all">All ${b.n}</button><button data-f="pass">Passed ${b.passed}</button><button data-f="fail">Missed ${b.n - b.passed}</button>
        </div>
        <div class="qlist" id="qlist"></div>
      </div>`;
    const draw = (f) => {
      $("#qlist").innerHTML = b.results.filter((r) => f === "all" || (f === "pass") === r.passed).map((r) => `
        <div class="qrow ${r.passed ? "" : "fail"}" data-id="${r.id}"><span class="qid">${r.id}</span><span>${esc(r.question)}</span>
        <span class="res">${r.passed ? tick : cross}</span></div>`).join("");
    };
    draw("all");
    $(".qfilter").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      $$(".qfilter button").forEach((x) => x.setAttribute("aria-selected", x === btn));
      draw(btn.dataset.f);
    });
    $("#qlist").addEventListener("click", (e) => {
      const row = e.target.closest(".qrow");
      if (!row) return;
      const open = $("pre", row);
      if (open) return open.remove();
      const r = b.results.find((x) => x.id === row.dataset.id);
      row.insertAdjacentHTML("beforeend", `<pre>${hl(r.sql)}</pre>`);
    });
    const io = new IntersectionObserver((en) => {
      if (!en.some((x) => x.isIntersecting)) return;
      io.disconnect();
      $$(".cat i[data-w]").forEach((i, k) => setTimeout(() => { i.style.width = `${i.dataset.w}%`; }, k * 100));
    }, { threshold: 0.2 });
    io.observe($("#benchOut"));
  }

  // ------------------------------------------------------------ boot
  function reveal() {
    const els = $$(".reveal");
    if (reduceMotion || !("IntersectionObserver" in window)) { els.forEach((e) => e.classList.add("in")); return; }
    const io = new IntersectionObserver((en) => en.forEach((x) => { if (x.isIntersecting) { x.target.classList.add("in"); io.unobserve(x.target); } }), { threshold: 0.06 });
    els.forEach((e) => io.observe(e));
  }

  async function boot() {
    initTheme();
    reveal();
    emptyState();
    try {
      const [answers, bench, model, glossary, values, schema] = await Promise.all(
        ["answers", "benchmark", "model", "glossary", "values", "schema"].map((n) => fetchJson(`data/${n}.json`)));
      Object.assign(S, { answers, bench, model, glossary, values, schema });
    } catch (e) {
      console.error(e);
      setEngine("Couldn't load the demo data.", 0, "error");
      return;
    }
    renderStats();
    renderSuggestions();
    renderPlayground();
    renderModel();
    renderBench();
    heroType();
    $("#askForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("#askInput").value;
      $("#askInput").value = "";
      ask(v);
    });
    $("#heroBox").addEventListener("click", () => {
      $("#ask").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" });
      setTimeout(() => ask($("#heroBox").dataset.q), reduceMotion ? 0 : 500);
    });
    const start = () => bootEngine();
    if ("requestIdleCallback" in window) requestIdleCallback(start, { timeout: 1500 });
    else setTimeout(start, 300);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
