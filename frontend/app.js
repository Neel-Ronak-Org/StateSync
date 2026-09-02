// frontend/app.js
// Extracted from index.html so the CSP can drop 'unsafe-inline' from
// script-src. Keeping the app inline forced that directive, and
// 'unsafe-inline' is what would have made the trace-renderer innerHTML sink
// (fixed separately) actually executable. Same code, served same-origin.
//
// NOTE: entrypoint.sh substitutes the ORCHESTRATOR_URL placeholder in THIS
// file at container start -- it used to patch index.html.
//
// ── Structure of this file ───────────────────────────────────────────────────
//   1. Config + session
//   2. Theme (light / system / dark) and the chart token bridge
//   3. DOM helpers -- textContent only, never innerHTML with data
//   4. Thread model: append-only turns
//   5. Renderers: answer, SQL, results table, chart, citations
//   6. Export: CSV / JSON / Markdown / PNG
//   7. A7 validator (validator 2 of 2) -- transcribed from the contract
//   8. Chart rendering through the AST interpreter
//   9. Developer rail
//  10. Submit / history / keyboard
(() => {
  'use strict';

  // ── 1. Config ──────────────────────────────────────────────────────────────
  // In Docker: orchestrator is accessed from the browser via the host machine.
  // ORCHESTRATOR_URL is nginx-substituted at container start.
  //
  // DO NOT REFORMAT THE NEXT LINE. frontend/entrypoint.sh runs a line-oriented
  // `sed` against the exact byte sequence
  //   window.__ORCHESTRATOR_URL__ || 'http://localhost:8090'
  // and scripts/verify_deployed.py pins the same literal. Changing the quotes,
  // the spacing, `||` to `??`, or wrapping it across two lines makes the
  // substitution silently no-op: sed exits 0, entrypoint.sh still prints
  // "Frontend configured", and every deployment then talks to localhost.
  const ORCHESTRATOR_URL = window.__ORCHESTRATOR_URL__ || 'http://localhost:8090';

  const SESSION_KEY = 'statesync_session_id';
  const THEME_KEY   = 'statesync_theme';
  const THREAD_KEY  = 'statesync_thread';

  // ── Authentication & Token Storage ─────────────────────────────────────────
  // Security tradeoff: localStorage is readable by any script that achieves
  // XSS, accepted here because the page's CSP forbids inline script and
  // unsafe-eval, and because the alternative (a cross-origin cookie) would
  // require SameSite=None; Secure and therefore HTTPS, which local development
  // does not have.
  const AUTH_TOKEN_KEY   = 'statesync.apiToken';
  const AUTH_EXPIRED_MSG = 'Session expired or token invalid. Enter your API token.';

  // In-memory fallback variable.
  // EVERY localStorage access MUST be wrapped in try/catch and degrade to an
  // in-memory fallback variable. A browser with site data blocked throws on
  // access, and an uncaught throw here would white-screen the whole app.
  let inMemoryToken = null;

  function getToken() {
    try {
      const val = window.localStorage.getItem(AUTH_TOKEN_KEY);
      return val !== null ? val : inMemoryToken;
    } catch {
      // A browser with site data blocked throws on access, and an uncaught throw
      // here would white-screen the whole app.
      return inMemoryToken;
    }
  }

  function setToken(t) {
    inMemoryToken = t || null;
    try {
      if (t) {
        window.localStorage.setItem(AUTH_TOKEN_KEY, t);
      } else {
        window.localStorage.removeItem(AUTH_TOKEN_KEY);
      }
    } catch {
      // A browser with site data blocked throws on access, and an uncaught throw
      // here would white-screen the whole app.
    }
  }

  function clearToken() {
    inMemoryToken = null;
    try {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
    } catch {
      // A browser with site data blocked throws on access, and an uncaught throw
      // here would white-screen the whole app.
    }
  }

  // ── Auth Header Helper ─────────────────────────────────────────────────────
  function authHeaders(extra) {
    const token = getToken();
    if (!token) return extra;
    return Object.assign({}, extra, { Authorization: `Bearer ${token}` });
  }

  function rateLimitMessage(resp) {
    const retry = resp && resp.headers && resp.headers.get ? resp.headers.get('Retry-After') : null;
    return retry
      ? `Rate limit reached. Try again in ${retry} seconds.`
      : 'Rate limit reached. Try again later.';
  }

  function getOrCreateSessionId() {
    let id = sessionStorage.getItem(SESSION_KEY);
    if (!id) {
      id = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() :
        'sess-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      sessionStorage.setItem(SESSION_KEY, id);
    }
    return id;
  }
  let sessionId = getOrCreateSessionId();

  // ── Elements ───────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const appEl        = $('app');
  const thread       = $('thread');
  const cover        = $('cover');
  const form         = $('query-form');
  const input        = $('query-input');
  const submitBtn    = $('submit-btn');
  const footerStatus = $('footer-status');
  const sessionMeta  = $('session-meta');
  const healthDot    = $('health-dot');
  const railEl       = $('rail');
  const railBody     = $('rail-body');
  const banner       = $('composer-banner');
  const bannerText   = $('composer-banner-text');
  const loginGate    = $('login-gate');
  const loginForm    = $('login-form');
  const tokenInput   = $('token-input');
  const loginSubmitBtn = $('login-submit-btn');
  const loginError   = $('login-error');
  const signOutBtn   = $('sign-out-btn');

  // ── Login Gate UI Helpers ──────────────────────────────────────────────────
  function showGate(errMsg) {
    if (loginGate) loginGate.hidden = false;
    if (appEl) appEl.hidden = true;
    if (loginError) {
      if (errMsg) {
        loginError.textContent = errMsg;
        loginError.hidden = false;
      } else {
        loginError.textContent = '';
        loginError.hidden = true;
      }
    }
    if (tokenInput) {
      tokenInput.value = '';
      tokenInput.focus();
    }
  }

  function hideGate() {
    if (loginGate) loginGate.hidden = true;
    if (appEl) appEl.hidden = false;
    if (loginError) {
      loginError.textContent = '';
      loginError.hidden = true;
    }
  }

  // ── 2. Theme ───────────────────────────────────────────────────────────────
  // Three states. 'system' removes data-theme entirely so the media query in
  // index.html governs; 'light'/'dark' stamp the attribute so an explicit
  // choice wins over the OS in BOTH directions.
  const THEMES = ['light', 'system', 'dark'];

  function currentTheme() {
    try {
      const stored = localStorage.getItem(THEME_KEY);
      return THEMES.includes(stored) ? stored : 'system';
    } catch {
      return 'system';
    }
  }

  function applyTheme(mode) {
    if (mode === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', mode);
    try {
      localStorage.setItem(THEME_KEY, mode);
    } catch {
      // In-memory fallback: if localStorage throws, mode remains applied to documentElement
    }
    THEMES.forEach((m) => {
      const btn = $(`theme-${m}`);
      if (btn) btn.setAttribute('aria-pressed', String(m === mode));
    });
    // A compiled Vega view holds no live binding to the config it was built
    // with, so every mounted chart keeps its old colors forever unless it is
    // rebuilt. This is the other half of the dark-on-dark fix.
    rerenderAllCharts();
  }

  THEMES.forEach((m) => {
    const btn = $(`theme-${m}`);
    if (btn) btn.addEventListener('click', () => applyTheme(m));
  });
  // Following the OS while in 'system' mode still needs a chart rebuild.
  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => { if (currentTheme() === 'system') rerenderAllCharts(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  // ── The chart token bridge ─────────────────────────────────────────────────
  // Chart color comes from the SAME CSS custom properties the page uses, read
  // at render time. No color literal appears anywhere in this file, which is
  // what makes palette drift between page and chart structurally impossible.
  //
  // Reads are guarded: a renamed or misspelled token yields '' from
  // getPropertyValue, and an empty string handed to Vega silently produces an
  // invisible mark -- the exact class of bug this rewrite exists to fix. A
  // missing token therefore warns loudly and falls back to a named CSS color
  // rather than failing open.
  const CHART_FALLBACK = {
    '--chart-surface': 'white', '--chart-view-stroke': 'silver',
    '--chart-axis-domain': 'gray', '--chart-axis-tick': 'gray',
    '--chart-grid': 'gainsboro', '--chart-label': 'dimgray',
    '--chart-title': 'black', '--chart-subtitle': 'gray',
    '--chart-c1': 'steelblue', '--chart-c2': 'chocolate', '--chart-c3': 'seagreen',
    '--chart-c4': 'slateblue', '--chart-c5': 'mediumvioletred', '--chart-c6': 'darkgoldenrod',
    '--chart-c7': 'slategray', '--chart-c8': 'crimson', '--chart-c9': 'teal',
    '--chart-c10': 'sienna',
    '--chart-ramp-from': 'aliceblue', '--chart-ramp-to': 'steelblue',
    '--chart-diverge-from': 'chocolate', '--chart-diverge-mid': 'gainsboro',
    '--chart-diverge-to': 'steelblue',
  };

  function tok(cs, name) {
    const v = cs.getPropertyValue(name).trim();
    if (v) return v;
    console.warn(`[theme] chart token ${name} resolved empty — using fallback.`);
    return CHART_FALLBACK[name] || 'gray';
  }

  function chartTokens() {
    const cs = getComputedStyle(document.documentElement);
    return {
      surface:    tok(cs, '--chart-surface'),
      viewStroke: tok(cs, '--chart-view-stroke'),
      domain:     tok(cs, '--chart-axis-domain'),
      tick:       tok(cs, '--chart-axis-tick'),
      grid:       tok(cs, '--chart-grid'),
      label:      tok(cs, '--chart-label'),
      title:      tok(cs, '--chart-title'),
      subtitle:   tok(cs, '--chart-subtitle'),
      fontSans:   "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      fontSerif:  "'Source Serif 4', Georgia, serif",
      categorical: Array.from({ length: 10 }, (_, i) => tok(cs, `--chart-c${i + 1}`)),
      ramp:      [tok(cs, '--chart-ramp-from'), tok(cs, '--chart-ramp-to')],
      diverging: [tok(cs, '--chart-diverge-from'), tok(cs, '--chart-diverge-mid'), tok(cs, '--chart-diverge-to')],
    };
  }

  // Every guide is colored explicitly. The old config set only `background`
  // and left axis/legend/title text to Vega-Lite's near-black defaults, which
  // is precisely why charts were illegible on a dark ground.
  function vegaConfig() {
    const c = chartTokens();
    return {
      background: c.surface,
      padding: { left: 8, right: 14, top: 8, bottom: 8 },
      autosize: { type: 'fit', contains: 'padding', resize: true },
      font: c.fontSans,
      view: { stroke: c.viewStroke, continuousWidth: 520, continuousHeight: 300 },
      axis: {
        domain: true, domainColor: c.domain, domainWidth: 1,
        ticks: true, tickColor: c.tick, tickSize: 5, tickWidth: 1,
        grid: true, gridColor: c.grid, gridWidth: 1, gridOpacity: 1,
        labelColor: c.label, labelFont: c.fontSans, labelFontSize: 11,
        labelPadding: 6, labelOverlap: 'greedy', labelLimit: 160,
        titleColor: c.title, titleFont: c.fontSans, titleFontSize: 12,
        titleFontWeight: 600, titlePadding: 10, tickCount: 6,
      },
      // labelAngle is deliberately NOT forced. Pinning -35 rotates short
      // categorical labels ('SP', 'RJ') that would read fine horizontally, and
      // clips them at narrow widths. Vega-Lite already rotates only when the
      // labels would collide, which is the behaviour we want at both widths.
      axisX: { grid: false },
      axisY: { grid: true, domain: false, ticks: false, format: '~s' },
      legend: {
        orient: 'top', direction: 'horizontal', offset: 6, padding: 0,
        symbolType: 'square', symbolSize: 90, symbolStrokeWidth: 0,
        labelColor: c.label, labelFont: c.fontSans, labelFontSize: 11,
        titleColor: c.title, titleFont: c.fontSans, titleFontSize: 11,
        titleFontWeight: 600, rowPadding: 4, columnPadding: 14,
      },
      title: {
        color: c.title, font: c.fontSerif, fontSize: 15, fontWeight: 600,
        anchor: 'start', offset: 12,
        subtitleColor: c.subtitle, subtitleFont: c.fontSans, subtitleFontSize: 12,
      },
      header: { labelColor: c.label, titleColor: c.title, labelFont: c.fontSans, titleFont: c.fontSans },
      range: {
        category: c.categorical, ordinal: c.ramp, ramp: c.ramp,
        heatmap: c.ramp, diverging: c.diverging,
      },
      // tooltip is deliberately NOT enabled: vega-tooltip builds its table with
      // innerHTML from raw row values, reintroducing the untrusted-data sink
      // the trace renderer was fixed for. The results table shows the values.
      mark:  { tooltip: false },
      bar:   { fill: c.categorical[0], cornerRadiusEnd: 2 },
      line:  { stroke: c.categorical[0], strokeWidth: 2 },
      point: { fill: c.categorical[0], filled: true, size: 54 },
      area:  { fill: c.categorical[0], fillOpacity: 0.22, line: { stroke: c.categorical[0], strokeWidth: 2 } },
      arc:   { stroke: c.surface, strokeWidth: 1 },
      tick:  { color: c.categorical[0], thickness: 2 },
      text:  { color: c.label, font: c.fontSans, fontSize: 11 },
      // Belt and braces: guide-label/guide-title are the underlying styles every
      // axis and legend text mark resolves through. A spec setting an unusual
      // guide can escape config.axis but not config.style.
      style: {
        'guide-label': { fill: c.label, font: c.fontSans, fontSize: 11 },
        'guide-title': { fill: c.title, font: c.fontSans, fontSize: 12, fontWeight: 600 },
        'group-title': { fill: c.title, font: c.fontSerif },
        'group-subtitle': { fill: c.subtitle },
        cell: { stroke: c.viewStroke },
      },
    };
  }

  // ── 3. DOM helpers ─────────────────────────────────────────────────────────
  // SECURITY: every helper below sets text via textContent. innerHTML is used
  // in exactly one way in this file -- assigning '' to clear a container --
  // and never to interpolate a value. state_trace carries sql_rows (raw
  // database content) and echoes the user's own text; document chunk ids come
  // from ingested files. All of it is untrusted.
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function button(label, cls, onClick, title) {
    const b = el('button', cls, label);
    b.type = 'button';
    if (title) b.title = title;
    b.addEventListener('click', onClick);
    return b;
  }

  const fmtInt = new Intl.NumberFormat();
  function fmtNumber(v) {
    if (typeof v !== 'number' || !Number.isFinite(v)) return String(v);
    return Number.isInteger(v) ? fmtInt.format(v) : String(Number(v.toFixed(4)));
  }

  // ── 4. Thread model ────────────────────────────────────────────────────────
  // Append-only. The previous implementation called resetUI() at the top of
  // every submit, which wiped the answer, SQL, chart and citations -- so the
  // page was structurally single-turn no matter what the backend remembered.
  const turns = [];
  const charts = new Map();   // turnId -> { host, spec }
  const pendingMounts = [];   // [turnId, host, spec] flushed at the end of renderTurn
  let turnSeq = 0;
  let activeController = null;

  function persistThread() {
    try {
      // sql_rows dominates the payload and sessionStorage is ~5MB. Keep the
      // rows for recent turns only; older turns keep everything else.
      const slim = turns.map((t, i) => {
        const keepRows = i >= turns.length - 8;
        const st = t.trace ? { ...t.trace } : null;
        if (st && !keepRows) { st.sql_rows = []; st.rows_trimmed = true; }
        else if (st && Array.isArray(st.sql_rows)) st.sql_rows = st.sql_rows.slice(0, 200);
        return { ...t, trace: st };
      });
      sessionStorage.setItem(THREAD_KEY, JSON.stringify({ sessionId, turns: slim }));
    } catch (err) {
      console.warn('[thread] could not persist to sessionStorage:', err && err.message);
    }
  }

  function restoreThread() {
    try {
      const raw = sessionStorage.getItem(THREAD_KEY);
      if (!raw) return false;
      const parsed = JSON.parse(raw);
      if (!parsed || parsed.sessionId !== sessionId || !Array.isArray(parsed.turns)) return false;
      parsed.turns.forEach((t) => { turns.push(t); renderTurn(t, true); });
      return turns.length > 0;
    } catch (err) {
      console.warn('[thread] could not restore thread:', err && err.message);
      return false;
    }
  }

  function updateSessionMeta() {
    const n = turns.filter((t) => t.state === 'done').length;
    sessionMeta.textContent = n === 0
      ? 'New conversation'
      : `${n} turn${n === 1 ? '' : 's'} · session ${sessionId.slice(0, 8)}…`;
  }

  function atBottom() {
    return thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
  }
  function scrollToBottom() { thread.scrollTop = thread.scrollHeight; }

  // ── 5. Renderers ───────────────────────────────────────────────────────────
  function renderTurn(turn, restoring) {
    const stick = restoring ? false : atBottom();
    if (cover && cover.parentNode) cover.remove();

    let art = document.getElementById(turn.id);
    if (art) { clear(art); } else {
      art = el('article', 'turn');
      art.id = turn.id;
      thread.appendChild(art);
    }
    art.setAttribute('aria-label', `Turn ${turn.ordinal}`);

    art.appendChild(el('div', 'turn__ordinal', String(turn.ordinal).padStart(2, '0')));

    const q = el('p', 'turn__question', turn.question);
    art.appendChild(q);

    // Per-turn actions
    const actions = el('div', 'turn__actions');
    actions.appendChild(button('Ask again', 'btn btn--ghost btn--sm',
      () => runQuery(turn.question), 'Re-issue this question as a new turn'));
    actions.appendChild(button('Edit', 'btn btn--ghost btn--sm',
      () => { input.value = turn.question; input.focus(); autosize(); }, 'Load into the composer'));
    art.appendChild(actions);

    if (turn.state === 'pending') {
      const p = el('div', 'pending');
      p.appendChild(el('span', 'spinner'));
      const lab = el('span', null, 'Thinking… ');
      const ela = el('span', 'elapsed', '0.0s');
      lab.appendChild(ela);
      p.appendChild(lab);
      p.appendChild(button('Stop', 'btn btn--ghost btn--sm', () => {
        if (activeController) activeController.abort();
      }));
      art.appendChild(p);
      turn._elapsedEl = ela;
      if (stick) scrollToBottom();
      return;
    }

    if (turn.state === 'error') {
      const n = el('div', 'notice notice--error');
      n.appendChild(el('strong', null, 'Request failed. '));
      n.appendChild(document.createTextNode(turn.error || 'Unknown error.'));
      art.appendChild(n);
      updateSessionMeta();
      if (stick) scrollToBottom();
      return;
    }

    const tr = turn.trace || {};

    // Rewrite note — makes the contextual reinterpretation visible.
    if (tr.rewrite_applied && tr.normalized_query) {
      const note = el('div', 'rewrite-note' + (tr.context_guessed ? ' rewrite-note--guessed' : ''));
      note.appendChild(el('span', 'rewrite-note__label', tr.context_guessed ? 'Guessed' : 'Interpreted as'));
      const body = el('span');
      body.appendChild(el('em', null, tr.normalized_query));
      if (tr.context_guessed) {
        body.appendChild(document.createTextNode(' — ambiguous reference resolved without asking.'));
      }
      note.appendChild(body);
      art.appendChild(note);
    }

    if (turn.answer) art.appendChild(el('div', 'answer', turn.answer));

    // KPI: a 1x1 result is a headline number, not a table.
    const cols = Array.isArray(tr.sql_columns) ? tr.sql_columns : [];
    const rows = Array.isArray(tr.sql_rows) ? tr.sql_rows : [];
    if (cols.length === 1 && rows.length === 1) {
      const v = rows[0][cols[0]];
      const k = el('div', 'kpi');
      k.appendChild(el('span', 'kpi__label', cols[0]));
      k.appendChild(document.createTextNode(fmtNumber(v)));
      art.appendChild(k);
    }

    art.appendChild(metaRow(turn, tr));

    if (turn.chart) art.appendChild(figureBlock(turn));
    if (rows.length) art.appendChild(tableBlock(turn, cols, rows));
    if (tr.executed_sql) art.appendChild(sqlBlock(tr.executed_sql));
    if (Array.isArray(turn.citations) && turn.citations.length) art.appendChild(citationsBlock(turn.citations));

    // A restored turn is stamped, and says so BEFORE its truncation notice.
    // Prose reads as a record of what was said; a rendered table reads as live
    // data. V2_STRATEGY 3b's staleness question is unanswered -- this does not
    // answer it, it makes the ambiguity visible, which is 3b's own minimum bar
    // ("answers stamped with when they were computed, at minimum").
    if (tr.restored) {
      const when = tr.computed_at ? new Date(tr.computed_at) : null;
      const stamp = el('div', 'restored-stamp');
      let text = 'Restored from history';
      if (when && !isNaN(when)) text += ` — computed ${when.toLocaleString()}`;
      if (tr.preview_truncated) {
        text += ` · showing ${fmtInt.format(rows.length)} stored of ` +
                `${fmtInt.format(tr.sql_rows_count)} rows`;
      }
      text += '. Not re-run; the underlying data may have changed since.';
      stamp.appendChild(document.createTextNode(text));
      art.appendChild(stamp);
    }

    // Truncation is reported with the config key that caused it, so the number
    // is actionable rather than mysterious.
    if (!tr.restored && typeof tr.sql_rows_count === 'number' && tr.sql_rows_count >= 1000) {
      art.appendChild(el('div', 'notice',
        `Returned ${fmtInt.format(tr.sql_rows_count)} rows — at or above the 1,000-row default cap ` +
        '(mcp.max_rows_default in config/statesync.toml). Results may be incomplete.'));
    }

    updateSessionMeta();
    flushMounts();
    if (stick) scrollToBottom();
  }

  // Mount charts once their article is attached to the document.
  function flushMounts() {
    while (pendingMounts.length) {
      const [id, host, spec] = pendingMounts.shift();
      renderChart(id, host, spec);
    }
  }

  function metaRow(turn, tr) {
    const row = el('div', 'meta-row');
    const route = (tr.route || 'unknown').toLowerCase();
    row.appendChild(el('span', `chip chip--${['sql', 'docs', 'hybrid'].includes(route) ? route : ''}`.trim(), route));
    if (typeof turn.latency === 'number') row.appendChild(el('span', 'chip', `${fmtInt.format(turn.latency)} ms`));
    if (typeof tr.sql_rows_count === 'number') {
      row.appendChild(el('span', 'chip', `${fmtInt.format(tr.sql_rows_count)} rows`));
    }
    if (tr.memory_hit) row.appendChild(el('span', 'chip', 'cached plan'));
    if (typeof tr.attempt === 'number' && tr.attempt > 0) {
      row.appendChild(el('span', 'chip chip--warn', `self-corrected ×${tr.attempt}`));
    }
    if (tr.detected_language && tr.detected_language !== 'en') {
      row.appendChild(el('span', 'chip', tr.detected_language));
    }
    row.appendChild(button('Trace', 'btn btn--ghost btn--sm', () => {
      openRail(); showRailTab('trace', turn);
    }, 'Open this turn in the developer panel'));
    return row;
  }

  function sqlBlock(sql) {
    const d = el('details', 'artifact');
    const s = el('summary');
    s.appendChild(el('span', 'disclosure-caret', '▶'));
    s.appendChild(el('span', 'artifact__title', 'Executed SQL'));
    s.appendChild(el('span', 'artifact__spacer'));
    s.appendChild(button('Copy', 'btn btn--sm', (ev) => {
      ev.preventDefault(); ev.stopPropagation(); copyText(sql, 'SQL copied.');
    }));
    d.appendChild(s);
    d.appendChild(el('pre', 'sql', sql));
    return d;
  }

  function inferNumericColumns(cols, rows) {
    const numeric = new Set();
    cols.forEach((c) => {
      const sample = rows.slice(0, 40).map((r) => r[c]).filter((v) => v !== null && v !== undefined);
      if (sample.length && sample.every((v) => typeof v === 'number')) numeric.add(c);
    });
    return numeric;
  }

  function tableBlock(turn, cols, rows) {
    const d = el('details', 'artifact block--wide');
    // Small results open by default; large ones stay collapsed so the reading
    // flow is not swamped.
    if (rows.length <= 12) d.open = true;
    const s = el('summary');
    s.appendChild(el('span', 'disclosure-caret', '▶'));
    s.appendChild(el('span', 'artifact__title', `Results · ${fmtInt.format(rows.length)} × ${cols.length}`));
    s.appendChild(el('span', 'artifact__spacer'));
    s.appendChild(button('CSV', 'btn btn--sm', (ev) => {
      ev.preventDefault(); ev.stopPropagation(); downloadCSV(turn, cols, rows);
    }, 'Download as CSV'));
    s.appendChild(button('JSON', 'btn btn--sm', (ev) => {
      ev.preventDefault(); ev.stopPropagation(); downloadJSON(turn, rows);
    }, 'Download as JSON'));
    s.appendChild(button('Copy TSV', 'btn btn--sm', (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      copyText(toDelimited(cols, rows, '\t', false), 'Copied — paste into Sheets or Excel.');
    }));
    d.appendChild(s);

    const numeric = inferNumericColumns(cols, rows);
    const wrap = el('div', 'table-scroll');
    const table = el('table', 'results');
    const cap = el('caption', null,
      rows.length > 500
        ? `Showing the first 500 of ${fmtInt.format(rows.length)} rows. Export for the full set.`
        : `${fmtInt.format(rows.length)} row${rows.length === 1 ? '' : 's'}.`);
    table.appendChild(cap);

    const thead = el('thead');
    const htr = el('tr');
    cols.forEach((c) => {
      const th = el('th', null, c);
      th.scope = 'col';
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);

    const tbody = el('tbody');
    rows.slice(0, 500).forEach((r) => {
      const tr2 = el('tr');
      cols.forEach((c) => {
        const v = r[c];
        let td;
        if (v === null || v === undefined) td = el('td', 'null', 'null');
        else if (numeric.has(c)) td = el('td', 'num', fmtNumber(v));
        else td = el('td', null, typeof v === 'object' ? JSON.stringify(v) : String(v));
        td.title = v === null || v === undefined ? 'null' : String(v);
        tr2.appendChild(td);
      });
      tbody.appendChild(tr2);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    d.appendChild(wrap);
    return d;
  }

  function figureBlock(turn) {
    const wrap = el('div', 'artifact block--wide');
    const head = el('div', 'artifact__head');
    head.appendChild(el('span', 'artifact__title', `Figure ${turn.ordinal}`));
    head.appendChild(el('span', 'artifact__spacer'));
    head.appendChild(button('PNG', 'btn btn--sm', () => exportPNG(turn), 'Download the chart as a PNG'));
    head.appendChild(button('Spec', 'btn btn--sm', () => { openRail(); showRailTab('spec', turn); }));
    wrap.appendChild(head);

    const fig = el('figure', 'figure');
    const host = el('div', 'chart-host');
    fig.appendChild(host);
    const cap = el('figcaption');
    const tr = turn.trace || {};
    cap.appendChild(document.createTextNode(
      `Source: StateSync · ${(tr.route || 'unknown')} route` +
      (typeof tr.sql_rows_count === 'number' ? ` · ${fmtInt.format(tr.sql_rows_count)} rows` : '')));
    fig.appendChild(cap);
    wrap.appendChild(fig);

    charts.set(turn.id, { host, spec: turn.chart });
    // Deliberately NOT scheduled with requestAnimationFrame. rAF does not fire
    // while the tab is hidden, and a local model can take 5-30s per turn -- so
    // a user who switches away while waiting would come back to a figure frame
    // that never mounted. renderTurn() flushes this after the article is in the
    // document, which is also when the host is measurable for autosize 'fit'.
    wrap.dataset.chartTurn = turn.id;
    if (ro) ro.observe(wrap);
    pendingMounts.push([turn.id, host, turn.chart]);
    return wrap;
  }

  function citationsBlock(citations) {
    const wrap = el('div', 'artifact');
    const head = el('div', 'artifact__head');
    head.appendChild(el('span', 'artifact__title', 'Retrieved context'));
    wrap.appendChild(head);
    const list = el('div', 'citation-list');
    citations.forEach((c) => {
      const item = el('div', 'citation');
      item.appendChild(el('span', 'citation__kind', c.kind === 'chunk' ? 'doc' : c.kind));
      item.appendChild(el('span', 'citation__ref',
        c.kind === 'chunk' && c.page != null ? `${c.ref} (p.${c.page})` : c.ref));
      list.appendChild(item);
    });
    wrap.appendChild(list);
    return wrap;
  }

  // ── 6. Export ──────────────────────────────────────────────────────────────
  // CSV formula-injection guard: a field starting with = + - @ TAB or CR is
  // executed as a formula when the file is opened in Excel or Sheets. Values
  // here come straight from the database, so this is a real path from stored
  // data to code execution on an analyst's machine. Prefixing an apostrophe
  // makes the cell literal text.
  function csvCell(v) {
    if (v === null || v === undefined) return '';
    let s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    if (/[",\r\n]/.test(s)) s = `"${s.replace(/"/g, '""')}"`;
    return s;
  }

  function toDelimited(cols, rows, sep, guard) {
    const head = cols.map((c) => (guard ? csvCell(c) : c)).join(sep);
    const body = rows.map((r) => cols.map((c) => {
      const v = r[c];
      if (guard) return csvCell(v);
      if (v === null || v === undefined) return '';
      return typeof v === 'object' ? JSON.stringify(v) : String(v);
    }).join(sep));
    return [head].concat(body).join('\r\n');
  }

  function downloadBlob(name, mime, text) {
    try {
      const blob = new Blob([text], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = el('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      status(`Downloaded ${name}`);
    } catch (err) {
      status(`Download failed: ${err && err.message}`);
    }
  }

  function downloadCSV(turn, cols, rows) {
    // BOM so Excel reads UTF-8 rather than guessing a legacy codepage.
    downloadBlob(`statesync-turn-${turn.ordinal}.csv`, 'text/csv;charset=utf-8',
      '﻿' + toDelimited(cols, rows, ',', true));
  }
  function downloadJSON(turn, rows) {
    downloadBlob(`statesync-turn-${turn.ordinal}.json`, 'application/json', JSON.stringify(rows, null, 2));
  }

  async function exportPNG(turn) {
    const entry = charts.get(turn.id);
    if (!entry || !entry.view) { status('Chart is not mounted.'); return; }
    try {
      const url = await entry.view.toImageURL('png', 2);
      const a = el('a');
      a.href = url; a.download = `statesync-figure-${turn.ordinal}.png`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      status('Chart exported.');
    } catch (err) {
      status(`PNG export failed: ${err && err.message}`);
    }
  }

  function copyText(text, okMsg) {
    const done = () => status(okMsg || 'Copied.');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else fallbackCopy(text, done);
  }
  function fallbackCopy(text, done) {
    // navigator.clipboard is undefined on insecure origins, which includes
    // http://localhost:3000 in some browsers -- the exact way this app is served.
    const ta = el('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); }
    catch (err) { status('Copy failed.'); }
    document.body.removeChild(ta);
  }

  function threadAsMarkdown() {
    const parts = [`# StateSync conversation\n\nSession \`${sessionId}\``];
    turns.forEach((t) => {
      if (t.state !== 'done') return;
      const tr = t.trace || {};
      parts.push(`\n## Turn ${t.ordinal}\n\n**Q:** ${t.question}`);
      if (tr.rewrite_applied && tr.normalized_query) parts.push(`\n_Interpreted as: ${tr.normalized_query}_`);
      parts.push(`\n${t.answer || ''}`);
      if (tr.executed_sql) parts.push('\n```sql\n' + tr.executed_sql + '\n```');
      const bits = [];
      if (tr.route) bits.push(`route: ${tr.route}`);
      if (typeof tr.sql_rows_count === 'number') bits.push(`rows: ${tr.sql_rows_count}`);
      if (typeof t.latency === 'number') bits.push(`${t.latency} ms`);
      if (bits.length) parts.push(`\n_${bits.join(' · ')}_`);
    });
    return parts.join('\n');
  }

  // ── 7. A7 validator, validator 2 of 2 (E2, A7 enforcement) ────────────────
  // Deliberately hand-written rather than a JSON-Schema library port. A7 asks
  // for "two independent validations, because the guarantee must not depend on
  // one component being correct" -- two copies of the same implementation
  // share the same bugs and are therefore one validation wearing two hats.
  // This is a direct structural transcription of the A7 contract; the
  // orchestrator side runs jsonschema over the declared schema. E2-V6 tests
  // that either alone suffices.
  //
  // Never repairs. Returns [] when valid, or a list of reasons when not; the
  // caller drops the chart and logs. Mirrors the orchestrator's BD7
  // expression-string audit over `transform`, which A7's own schema leaves
  // open (additionalProperties: true on transform items).
  const A7 = {
    MARKS: ['bar', 'line', 'point', 'area', 'tick', 'rect', 'arc'],
    ENCODING_PROPS: ['field', 'type', 'aggregate', 'title', 'sort'],
    ENCODING_TYPES: ['quantitative', 'nominal', 'ordinal', 'temporal'],
    AGGREGATES: ['sum', 'mean', 'count', 'min', 'max', 'median'],
    TOP_LEVEL: ['$schema', 'mark', 'data', 'encoding', 'transform', 'title', 'width', 'height'],
    REQUIRED: ['mark', 'encoding', 'data'],
    SCHEMA_URL: 'https://vega.github.io/schema/vega-lite/v5.json',
    // Always expression strings, wherever they appear.
    EXPRESSION_KEYS: ['expr', 'signal', 'calculate', 'test', 'init', 'update', 'on'],
    // Dual-form: `filter` takes either an expression string or a
    // non-executable predicate object. Only the string form is an expression.
    DUAL_FORM_EXPRESSION_KEYS: ['filter'],
    MAX_VALUES: 5000,
    MAX_TRANSFORMS: 8,
    MIN_DIM: 100,
    MAX_WIDTH: 1600,
    MAX_HEIGHT: 1200,
  };

  function isPlainObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  // Recursive scan for expression-bearing keys (BD7).
  function auditExpressions(node, path, out) {
    if (Array.isArray(node)) {
      node.forEach((item, i) => auditExpressions(item, `${path}/${i}`, out));
    } else if (isPlainObject(node)) {
      for (const [k, v] of Object.entries(node)) {
        if (A7.EXPRESSION_KEYS.includes(k)) {
          out.push(`${path}/${k}: expression-bearing property is not permitted (BD7)`);
        } else if (A7.DUAL_FORM_EXPRESSION_KEYS.includes(k) && typeof v === 'string') {
          out.push(`${path}/${k}: string form is an expression; use the predicate-object form instead (BD7)`);
        }
        auditExpressions(v, `${path}/${k}`, out);
      }
    }
  }

  function validateA7Chart(chart) {
    const errs = [];
    if (chart === null || chart === undefined) return errs;   // chart:null is valid
    if (!isPlainObject(chart)) { errs.push('chart: must be an object or null'); return errs; }

    for (const key of A7.REQUIRED) {
      if (!(key in chart)) errs.push(`chart: missing required property '${key}'`);
    }
    for (const key of Object.keys(chart)) {
      if (!A7.TOP_LEVEL.includes(key)) errs.push(`chart/${key}: unknown top-level property`);
    }

    if ('$schema' in chart && chart.$schema !== A7.SCHEMA_URL) {
      errs.push(`chart/$schema: must be exactly ${A7.SCHEMA_URL}`);
    }

    // mark: primitive string from the enum. Object-form marks are the path
    // where mark properties smuggle expressions, so they are rejected here.
    if ('mark' in chart) {
      if (typeof chart.mark !== 'string') errs.push('chart/mark: must be a string enum value, not an object');
      else if (!A7.MARKS.includes(chart.mark)) errs.push(`chart/mark: '${chart.mark}' is not in the permitted enum`);
    }

    // data: { values: [...] } only. No url, no name, no format.
    if ('data' in chart) {
      if (!isPlainObject(chart.data)) errs.push('chart/data: must be an object');
      else {
        if (!('values' in chart.data)) errs.push("chart/data: missing required property 'values'");
        for (const key of Object.keys(chart.data)) {
          if (key !== 'values') errs.push(`chart/data/${key}: unknown property (only 'values' is permitted)`);
        }
        if ('values' in chart.data) {
          if (!Array.isArray(chart.data.values)) errs.push('chart/data/values: must be an array');
          else if (chart.data.values.length > A7.MAX_VALUES) {
            errs.push(`chart/data/values: ${chart.data.values.length} items exceeds maximum of ${A7.MAX_VALUES}`);
          }
        }
      }
    }

    // encoding: channel objects restricted to the property allowlist.
    if ('encoding' in chart) {
      if (!isPlainObject(chart.encoding)) errs.push('chart/encoding: must be an object');
      else {
        for (const [channel, def] of Object.entries(chart.encoding)) {
          if (!isPlainObject(def)) { errs.push(`chart/encoding/${channel}: must be an object`); continue; }
          for (const key of Object.keys(def)) {
            if (!A7.ENCODING_PROPS.includes(key)) {
              errs.push(`chart/encoding/${channel}/${key}: unknown encoding property`);
            }
          }
          if ('type' in def && !A7.ENCODING_TYPES.includes(def.type)) {
            errs.push(`chart/encoding/${channel}/type: '${def.type}' is not a permitted type`);
          }
          if ('aggregate' in def && !A7.AGGREGATES.includes(def.aggregate)) {
            errs.push(`chart/encoding/${channel}/aggregate: '${def.aggregate}' is not a permitted aggregate`);
          }
          if ('field' in def && typeof def.field !== 'string') {
            errs.push(`chart/encoding/${channel}/field: must be a string`);
          }
        }
      }
    }

    if ('transform' in chart) {
      if (!Array.isArray(chart.transform)) errs.push('chart/transform: must be an array');
      else if (chart.transform.length > A7.MAX_TRANSFORMS) {
        errs.push(`chart/transform: ${chart.transform.length} items exceeds maximum of ${A7.MAX_TRANSFORMS}`);
      }
      auditExpressions(chart.transform, 'chart/transform', errs);
    }

    if ('title' in chart && typeof chart.title !== 'string') errs.push('chart/title: must be a string');
    for (const [dim, max] of [['width', A7.MAX_WIDTH], ['height', A7.MAX_HEIGHT]]) {
      if (dim in chart) {
        const v = chart[dim];
        if (!Number.isInteger(v)) errs.push(`chart/${dim}: must be an integer`);
        else if (v < A7.MIN_DIM || v > max) errs.push(`chart/${dim}: ${v} outside [${A7.MIN_DIM}, ${max}]`);
      }
    }

    return errs;
  }
  // Exposed so E2-V3/E2-V6 can drive this validator directly, independently of
  // the orchestrator, which is the whole point of the "either alone suffices"
  // guarantee. Not used by the render path itself.
  window.validateA7Chart = validateA7Chart;

  // ── 8. Chart rendering ─────────────────────────────────────────────────────
  // Resolve the CSP-safe expression interpreter. Different builds expose it
  // under different globals, so probe rather than assume; if none is present
  // we must NOT silently fall through to the default compiler (that needs
  // unsafe-eval and would make E2-V4 pass only because nothing rendered).
  function expressionInterpreter() {
    // Candidates are checked by SHAPE, not by truthiness. vega-interpreter's
    // UMD build also defines a bare `window.expressionInterpreter`, but that
    // one is a plain function with no `.operator` — passing it to vega's
    // `expr` option throws "this.expr.operator is not a function" and every
    // render fails. The usable object is `vega.expressionInterpreter`, which
    // implements {operator, parameter, event, handler, encode}. Verified
    // in-browser; a truthiness check silently picks the wrong one.
    const usable = c => c && typeof c.operator === 'function';
    const found = [
      window.vega && window.vega.expressionInterpreter,
      window.vegaInterpreter && window.vegaInterpreter.expressionInterpreter,
      window.expressionInterpreter,
    ].find(usable);
    if (!found) {
      console.error('[A7] vega-interpreter unavailable — refusing to render via the eval-based compiler.');
    }
    return found;
  }

  function renderWithheld(host, reasons) {
    clear(host);
    host.appendChild(el('div', 'chart-withheld',
      `Chart withheld — ${reasons.length} A7 validation ${reasons.length === 1 ? 'violation' : 'violations'}. ` +
      'The prose answer above is unaffected. See the developer panel for detail.'));
  }

  async function renderChart(turnId, host, spec) {
    if (!spec || !host) return;

    // Validator 2 of 2 — runs BEFORE render, independently of whatever the
    // orchestrator concluded. No repair path: an invalid spec is dropped, the
    // prose answer stays, and the failure is surfaced rather than only logged.
    const violations = validateA7Chart(spec);
    if (violations.length > 0) {
      console.error('[A7 validation failure] chart rejected before render, not repaired:', violations);
      renderWithheld(host, violations);
      const rec = charts.get(turnId);
      if (rec) rec.violations = violations;
      return;
    }

    const interp = expressionInterpreter();
    if (!interp) { renderWithheld(host, ['expression interpreter unavailable']); return; }

    try {
      clear(host);
      // CSP-safe rendering (A7): parse to an AST and evaluate expressions with
      // vega-interpreter, never the default compiler, which builds functions
      // via `Function(...)` and would need `unsafe-eval`. The CSP in
      // frontend/nginx.conf omits unsafe-eval, so the compiler path would
      // throw rather than silently work — that is what makes E2-V4 a real
      // test instead of a cosmetic one.
      // Size the chart to its frame.
      //
      // vega-embed's `width` OPTION does not work for a Vega-Lite spec --
      // verified in-browser: the same spec renders 98px via the option and
      // 738px when `width` is set on the spec itself. Only the spec field is
      // honoured, so the width must go there.
      //
      // That is safe under A7, which permits a top-level `width` as an integer
      // in [MIN_DIM, MAX_WIDTH]. The spec RECEIVED from the orchestrator is
      // validated first and unmodified; the width is applied to a copy, and
      // the copy is validated again before it reaches vega. If the copy fails
      // for any reason we render the original rather than nothing.
      const frame = host.closest('.artifact') || host.parentElement;
      const box = frame ? frame.getBoundingClientRect().width : 0;
      let draw = spec;
      if (!('width' in spec) && box > 40) {
        const avail = Math.min(A7.MAX_WIDTH, Math.max(A7.MIN_DIM, Math.floor(box) - 28));
        const candidate = { ...spec, width: avail };
        draw = validateA7Chart(candidate).length === 0 ? candidate : spec;
      }
      const result = await vegaEmbed(host, draw, {
        renderer: 'canvas',
        actions: false,
        config: vegaConfig(),
        ast: true,
        expr: interp,
      });
      const rec0 = charts.get(turnId);
      if (rec0 && box > 40) rec0.lastWidth = Math.floor(box);
      const rec = charts.get(turnId);
      if (rec) rec.view = result.view;
    } catch (err) {
      console.error('Chart render error (A7 violation or vega error):', err);
      renderWithheld(host, [String((err && err.message) || err)]);
    }
  }

  // Rebuilding is the only way to recolor: a compiled Vega view captured the
  // old config by value and will never pick up new CSS custom properties.
  function rerenderAllCharts() {
    charts.forEach((rec, turnId) => {
      if (rec && rec.host && rec.spec) renderChart(turnId, rec.host, rec.spec);
    });
  }

  // Charts are sized from a measurement, and a measurement can be wrong or
  // simply unavailable at mount time -- a container that is display:none, in a
  // collapsed grid track, or in a tab the browser has not laid out yet all
  // report 0. Rather than trust one reading, observe the frame and re-render
  // when its width actually changes. This also gives real responsive
  // behaviour when the developer rail opens and the reading column narrows.
  const ro = ('ResizeObserver' in window) ? new ResizeObserver((entries) => {
    entries.forEach((e) => {
      const id = e.target.dataset.chartTurn;
      if (!id) return;
      const rec = charts.get(id);
      if (!rec || !rec.spec) return;
      const w = Math.floor(e.contentRect.width);
      // Only act on a real change, or the re-render's own layout retriggers us.
      if (w < 40 || Math.abs(w - (rec.lastWidth || 0)) <= 8) return;
      rec.lastWidth = w;
      if (rec.resizeTimer) clearTimeout(rec.resizeTimer);
      rec.resizeTimer = setTimeout(() => renderChart(id, rec.host, rec.spec), 120);
    });
  }) : null;

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(rerenderAllCharts, 200);
  });

  // ── 9. Developer rail ──────────────────────────────────────────────────────
  let railTab = 'trace';
  let railTurn = null;

  function openRail()  { document.documentElement.setAttribute('data-rail', 'open'); $('rail-toggle').setAttribute('aria-expanded', 'true'); }
  function closeRail() { document.documentElement.removeAttribute('data-rail'); $('rail-toggle').setAttribute('aria-expanded', 'false'); }
  function toggleRail() {
    if (document.documentElement.getAttribute('data-rail') === 'open') closeRail();
    else { openRail(); paintRail(); }
  }
  $('rail-toggle').addEventListener('click', toggleRail);
  $('rail-close').addEventListener('click', closeRail);
  ['trace', 'metrics', 'spec', 'theme'].forEach((t) => {
    $(`tab-${t}`).addEventListener('click', () => showRailTab(t, railTurn));
  });

  function showRailTab(tab, turn) {
    railTab = tab;
    if (turn) railTurn = turn;
    ['trace', 'metrics', 'spec', 'theme'].forEach((t) => {
      $(`tab-${t}`).setAttribute('aria-selected', String(t === tab));
    });
    paintRail();
  }

  function traceRows(obj, into) {
    Object.entries(obj).forEach(([k, v]) => {
      const row = el('div', 'trace__row');
      row.appendChild(el('span', 'trace__key', k));
      // SECURITY: textContent. sql_rows is raw database content and
      // normalized_query echoes the user's own text; both reach this node.
      // The previous implementation interpolated these into innerHTML, which
      // also silently swallowed angle-bracketed payloads (the adversarial
      // corpus seeds `<tool_call>` strings) so an analyst inspecting an
      // injection attempt saw the payload vanish from the very trace meant to
      // reveal it.
      const val = typeof v === 'object' && v !== null ? JSON.stringify(v, null, 1) : String(v);
      row.appendChild(el('span', 'trace__val', val));
      into.appendChild(row);
    });
  }

  function paintRail() {
    clear(railBody);
    const turn = railTurn || turns.filter((t) => t.state === 'done').slice(-1)[0];

    if (railTab === 'theme') {
      const sec = el('div', 'rail__section');
      sec.appendChild(el('h3', null, 'Resolved chart tokens'));
      const box = el('div', 'trace');
      const c = chartTokens();
      traceRows({
        theme: currentTheme(),
        resolved: document.documentElement.getAttribute('data-theme') || 'system',
        surface: c.surface, label: c.label, title: c.title,
        grid: c.grid, axisDomain: c.domain,
        categorical: c.categorical.join(' '),
      }, box);
      sec.appendChild(box);
      const note = el('div', 'rail__empty',
        'These are read from CSS custom properties at render time. Switching theme rebuilds every mounted chart.');
      sec.appendChild(note);
      railBody.appendChild(sec);
      return;
    }

    if (!turn) { railBody.appendChild(el('div', 'rail__empty', 'Run a query to populate the trace.')); return; }

    if (railTab === 'spec') {
      const rec = charts.get(turn.id);
      if (rec && rec.violations) {
        const sec = el('div', 'rail__section');
        sec.appendChild(el('h3', null, 'A7 violations'));
        rec.violations.forEach((v) => sec.appendChild(el('div', 'trace__val', v)));
        railBody.appendChild(sec);
      }
      railBody.appendChild(el('pre', 'sql',
        turn.chart ? JSON.stringify(turn.chart, null, 2) : 'chart: null (no chart produced for this turn)'));
      return;
    }

    const tr = turn.trace || {};
    if (railTab === 'metrics') {
      const box = el('div', 'trace');
      traceRows(tr.metrics || { metrics: 'none' }, box);
      railBody.appendChild(box);
      const sec = el('div', 'rail__section');
      sec.appendChild(el('h3', null, 'Session totals'));
      const tot = turns.reduce((a, t) => {
        const m = (t.trace && t.trace.metrics) || {};
        a.turns += t.state === 'done' ? 1 : 0;
        a.tokens_in += m.tokens_in || 0;
        a.tokens_out += m.tokens_out || 0;
        a.llm_calls += m.llm_calls || 0;
        a.rows += (t.trace && t.trace.sql_rows_count) || 0;
        return a;
      }, { turns: 0, tokens_in: 0, tokens_out: 0, llm_calls: 0, rows: 0 });
      const box2 = el('div', 'trace');
      traceRows(tot, box2);
      sec.appendChild(box2);
      railBody.appendChild(sec);
      return;
    }

    // Trace tab. sql_rows is shown as a count, not inlined -- the full rows
    // are in the results table, and dumping 1000 of them here makes the panel
    // unusable for its actual purpose.
    const shown = { ...tr };
    if (Array.isArray(shown.sql_rows)) shown.sql_rows = `[${shown.sql_rows.length} rows — see the results table]`;
    const box = el('div', 'trace');
    traceRows(shown, box);
    railBody.appendChild(box);
  }

  // ── 10. Submit / history / keyboard ────────────────────────────────────────
  function status(msg) { footerStatus.textContent = msg; }

  // Grow the composer with its content, but never trust a measurement taken
  // before layout has settled. At boot, scrollHeight is read before the web
  // fonts land and reports a bogus value that clamps to the max-height and
  // then sticks, because nothing re-measures until the next input event --
  // which is how an empty one-line box rendered 190px tall.
  function autosize() {
    if (input.value === '') { input.style.height = ''; return; }   // natural rows=1 height
    input.style.height = 'auto';
    const h = input.scrollHeight;
    input.style.height = (h > 0 ? Math.min(h, 190) : 22) + 'px';
  }
  input.addEventListener('input', autosize);

  const sent = [];
  let histIdx = -1;
  let draft = '';

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); return; }
    if (e.key === 'Escape' && activeController) { activeController.abort(); return; }
    // Shell-style recall, only from an empty (or already-recalled) composer so
    // it never eats a cursor movement inside real text.
    if (e.key === 'ArrowUp' && (input.value === '' || histIdx >= 0) && sent.length) {
      if (histIdx === -1) draft = input.value;
      histIdx = Math.min(histIdx + 1, sent.length - 1);
      input.value = sent[sent.length - 1 - histIdx];
      e.preventDefault(); autosize();
    } else if (e.key === 'ArrowDown' && histIdx >= 0) {
      histIdx -= 1;
      input.value = histIdx === -1 ? draft : sent[sent.length - 1 - histIdx];
      e.preventDefault(); autosize();
    }
  });

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'd') { e.preventDefault(); toggleRail(); }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    input.value = ''; autosize();
    histIdx = -1; draft = '';
    runQuery(q);
  });

  async function runQuery(question) {
    if (activeController) { status('A query is already running.'); return; }
    sent.push(question);

    turnSeq += 1;
    const turn = {
      id: `turn-${turnSeq}`, ordinal: turnSeq, question,
      state: 'pending', answer: '', chart: null, citations: [], trace: null,
      latency: null, error: null,
    };
    turns.push(turn);
    renderTurn(turn);
    scrollToBottom();

    const t0 = performance.now();
    const tick = setInterval(() => {
      if (turn._elapsedEl) turn._elapsedEl.textContent = `${((performance.now() - t0) / 1000).toFixed(1)}s`;
    }, 100);

    activeController = new AbortController();
    submitBtn.disabled = true;
    status('Running…');

    try {
      const resp = await fetch(`${ORCHESTRATOR_URL}/query`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ query: question, session_id: sessionId }),
        signal: activeController.signal,
      });
      if (resp.status === 401) {
        clearToken();
        showGate(AUTH_EXPIRED_MSG);
        throw new Error(AUTH_EXPIRED_MSG);
      }
      if (resp.status === 429) {
        throw new Error(rateLimitMessage(resp));
      }
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(errBody.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();

      turn.state = 'done';
      turn.answer = data.answer_text || '';
      turn.chart = data.chart || null;
      turn.citations = data.citations || [];
      turn.trace = data.state_trace || {};
      turn.latency = data.latency_ms;
      turn.queryId = data.query_id;
      // The backend's own turn index is authoritative; the local counter is
      // only a display ordinal and can drift from it after a restore.
      if (turn.trace && typeof turn.trace.turn_index === 'number') {
        turn.backendTurn = turn.trace.turn_index;
      }
      status(`Done · query_id=${data.query_id}`);
    } catch (err) {
      turn.state = 'error';
      turn.error = (err && err.name === 'AbortError') ? 'Cancelled.' : (err && err.message) || 'Unknown error.';
      status(turn.error);
    } finally {
      clearInterval(tick);
      activeController = null;
      submitBtn.disabled = false;
      delete turn._elapsedEl;
      renderTurn(turn);
      persistThread();
      if (railTurn === null || railTab) { railTurn = turn; if (document.documentElement.getAttribute('data-rail') === 'open') paintRail(); }
    }
  }

  // ── Masthead actions ───────────────────────────────────────────────────────
  $('new-conversation-btn').addEventListener('click', () => {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(THREAD_KEY);
    sessionId = getOrCreateSessionId();
    syncActiveSessionRow();
    turns.length = 0; charts.clear(); turnSeq = 0; railTurn = null;
    clear(thread);
    thread.appendChild(cover);
    buildSuggestions();
    updateSessionMeta();
    status('Started a new conversation.');
  });

  $('copy-thread-btn').addEventListener('click', () => {
    if (!turns.some((t) => t.state === 'done')) { status('Nothing to copy yet.'); return; }
    copyText(threadAsMarkdown(), 'Conversation copied as Markdown.');
  });

  if (signOutBtn) {
    signOutBtn.addEventListener('click', () => {
      clearToken();
      if (sidebarOpen && sidebarOpen()) closeSidebar();
      showGate();
      status('Signed out.');
    });
  }

  if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const val = tokenInput ? tokenInput.value.trim() : '';
      if (!val) {
        if (loginError) {
          loginError.textContent = 'API token is required.';
          loginError.hidden = false;
        }
        return;
      }

      if (loginSubmitBtn) loginSubmitBtn.disabled = true;
      if (loginError) loginError.hidden = true;

      // Store the token immediately so authHeaders() can attach it to the validation request
      setToken(val);

      try {
        const resp = await fetch(`${ORCHESTRATOR_URL}/sessions?limit=1`, {
          headers: authHeaders(),
        });

        if (resp.status === 401) {
          clearToken();
          if (loginError) {
            loginError.textContent = 'Invalid API token.';
            loginError.hidden = false;
          }
          if (loginSubmitBtn) loginSubmitBtn.disabled = false;
          return;
        }

        if (resp.status === 429) {
          const msg = rateLimitMessage(resp);
          if (loginError) {
            loginError.textContent = msg;
            loginError.hidden = false;
          }
          if (loginSubmitBtn) loginSubmitBtn.disabled = false;
          return;
        }

        // ANY other response, including 404, is considered success.
        // 404 means the session browser is disabled, which still proves the token authenticated.
        hideGate();
        if (loginSubmitBtn) loginSubmitBtn.disabled = false;
        if (resp.ok) {
          historyBtn.hidden = false;
          openHistory();
        }
        input.focus();
        status('Ready.');
      } catch (err) {
        clearToken();
        if (loginError) {
          loginError.textContent = `Connection error: ${(err && err.message) || 'failed to reach orchestrator'}`;
          loginError.hidden = false;
        }
        if (loginSubmitBtn) loginSubmitBtn.disabled = false;
      }
    });
  }

  // ── Past conversations ────────────────────────────────────────────────────
  // The button stays hidden unless GET /sessions actually answers. The route
  // is now always available to an authenticated caller and returns only that
  // principal's own conversations. Probing discovers whether the endpoint is
  // reachable so the UI degrades gracefully if the orchestrator is unavailable.
  const historyBtn  = $('history-btn');
  const historyList = $('history-list');

  function sidebarOpen() { return document.documentElement.getAttribute('data-sidebar') === 'open'; }
  function openSidebar()  { document.documentElement.setAttribute('data-sidebar', 'open'); historyBtn.setAttribute('aria-expanded', 'true'); }
  function closeSidebar() { document.documentElement.removeAttribute('data-sidebar'); historyBtn.setAttribute('aria-expanded', 'false'); }

  async function probeSessionBrowser() {
    try {
      const r = await fetch(`${ORCHESTRATOR_URL}/sessions?limit=1`, {
        headers: authHeaders(),
      });
      if (r.status === 401) {
        clearToken();
        showGate(AUTH_EXPIRED_MSG);
        status(AUTH_EXPIRED_MSG);
        return;
      }
      if (r.status === 429) {
        status(rateLimitMessage(r));
        return;
      }
      if (r.ok) { historyBtn.hidden = false; openHistory(); }
    } catch (err) {
      /* orchestrator down or route absent -- leave the button hidden. */
    }
  }

  function fmtWhen(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1)   return 'just now';
    if (mins < 60)  return `${mins}m ago`;
    if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  async function openHistory() {
    openSidebar();
    clear(historyList);
    historyList.appendChild(el('div', 'rail__empty', 'Loading…'));
    try {
      const r = await fetch(`${ORCHESTRATOR_URL}/sessions?limit=50`, {
        headers: authHeaders(),
      });
      if (r.status === 401) {
        clearToken();
        showGate(AUTH_EXPIRED_MSG);
        status(AUTH_EXPIRED_MSG);
        clear(historyList);
        historyList.appendChild(el('div', 'rail__empty', AUTH_EXPIRED_MSG));
        return;
      }
      if (r.status === 429) {
        const msg = rateLimitMessage(r);
        status(msg);
        clear(historyList);
        historyList.appendChild(el('div', 'rail__empty', msg));
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      clear(historyList);
      const list = data.sessions || [];
      if (!list.length) {
        historyList.appendChild(el('div', 'rail__empty', 'No conversations stored yet.'));
        return;
      }
      list.forEach((sess) => {
        const row = el('button', 'session-row');
        row.type = 'button';
        row.title = sess.first_query || sess.session_id;
        row.dataset.sessionId = sess.session_id;
        row.appendChild(el('span', 'session-row__q', sess.first_query || '(no question recorded)'));
        row.appendChild(el('span', 'session-row__meta',
          `${sess.turn_count} turn${sess.turn_count === 1 ? '' : 's'} · ${fmtWhen(sess.last_activity)}`));
        row.addEventListener('click', () => loadSession(sess.session_id));
        historyList.appendChild(row);
      });
      syncActiveSessionRow();
    } catch (err) {
      clear(historyList);
      historyList.appendChild(el('div', 'rail__empty', `Could not load history: ${err.message}`));
    }
  }

  // BUG FIXED: aria-current was set once at row-creation time, comparing
  // against sessionId as it stood THEN. loadSession() and "New conversation"
  // both reassign the outer `sessionId` variable afterward but never touched
  // the DOM again, so whichever row happened to match at the last openHistory()
  // call stayed marked "current" forever -- including while a completely
  // different conversation was open on screen. This re-derives the highlight
  // from the live sessionId on every call, driven off data-session-id rather
  // than recomputed at creation, so it can be called again after ANY change to
  // which conversation is active.
  function syncActiveSessionRow() {
    historyList.querySelectorAll('.session-row').forEach((row) => {
      if (row.dataset.sessionId === sessionId) row.setAttribute('aria-current', 'true');
      else row.removeAttribute('aria-current');
    });
  }

  function toggleHistory() { if (sidebarOpen()) closeSidebar(); else openHistory(); }

  // Loading a past conversation ADOPTS its session_id, so a follow-up typed
  // afterwards continues that thread on the backend too -- the rewrite node
  // reads history by session_id, so resuming the UI without resuming the id
  // would silently start a new conversation wearing an old one's transcript.
  async function loadSession(id) {
    try {
      const r = await fetch(`${ORCHESTRATOR_URL}/sessions/${encodeURIComponent(id)}?limit=100`, {
        headers: authHeaders(),
      });
      if (r.status === 401) {
        clearToken();
        showGate(AUTH_EXPIRED_MSG);
        status(AUTH_EXPIRED_MSG);
        return;
      }
      if (r.status === 429) {
        status(rateLimitMessage(r));
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      sessionId = id;
      sessionStorage.setItem(SESSION_KEY, id);
      syncActiveSessionRow();
      turns.length = 0; charts.clear(); turnSeq = 0; railTurn = null;
      clear(thread);
      (data.turns || []).forEach((t) => {
        turnSeq += 1;
        const turn = {
          id: `turn-${turnSeq}`, ordinal: t.turn_index || turnSeq,
          question: t.raw_query, state: 'done',
          answer: t.answer_text || '', chart: t.chart || null, citations: [],
          // Artifacts come back from migration 012. sql_rows_preview is a
          // CAPPED slice, so sql_rows_count (the true size) travels with it
          // and the turn is stamped: a rendered table must not imply it is
          // the whole result, nor that it is live. A turn written before 012
          // has all four fields NULL and degrades to prose + SQL, which is
          // exactly the old behaviour rather than an empty table frame.
          trace: {
            route: t.route, executed_sql: t.executed_sql,
            normalized_query: t.rewritten_query,
            rewrite_applied: t.rewrite_applied,
            context_guessed: t.context_guessed,
            turn_index: t.turn_index,
            sql_columns: t.sql_columns || [],
            sql_rows: t.sql_rows_preview || [],
            sql_rows_count: t.sql_rows_count,
            restored: true,
            computed_at: t.computed_at,
            preview_truncated: (t.sql_rows_count || 0) > (t.sql_rows_preview || []).length,
          },
          latency: null, error: null,
        };
        turns.push(turn);
        renderTurn(turn, true);
      });
      updateSessionMeta();
      persistThread();
      scrollToBottom();
      status(`Resumed conversation ${id.slice(0, 8)}… — follow-ups continue this thread.`);
    } catch (err) {
      status(`Could not load that conversation: ${err.message}`);
    }
  }

  historyBtn.addEventListener('click', toggleHistory);
  $('sidebar-new').addEventListener('click', () => $('new-conversation-btn').click());

  // ── Suggestions ────────────────────────────────────────────────────────────
  // These target the SHIPPED default corpus -- the synthetic ONDC schema in
  // `public` (orders, sellers, fulfillments, disputes, logistics_providers,
  // documents), which is what schema_retriever.target_schema resolves to by
  // default. They are deliberately NOT Olist questions: Olist lives in its own
  // `olist` schema and is not reachable on the default configuration, so
  // suggesting product-category or review-score questions here would hand the
  // user a prompt the running system cannot answer.
  const SUGGESTIONS = [
    ['SQL', 'How many orders are there in total?'],
    ['SQL', 'Which sellers have the highest average order value?'],
    ['SQL', 'List logistics providers with more than 50 fulfillments.'],
    ['SQL', 'How many disputes were raised in the last 90 days?'],
    ['Docs', "What committed transit time does FastShip 1's SLA annexure specify for the Metro Mumbai zone?"],
    ['Follow-up', 'Ask any of the above, then follow up with “and how many were delivered?”'],
  ];

  function buildSuggestions() {
    const grid = $('suggestions');
    if (!grid) return;
    clear(grid);
    SUGGESTIONS.forEach(([kind, text]) => {
      const b = el('button', 'suggestion');
      b.type = 'button';
      b.appendChild(el('span', 'suggestion__kind', kind));
      b.appendChild(document.createTextNode(text));
      b.addEventListener('click', () => {
        if (kind === 'Follow-up') { input.focus(); return; }
        input.value = text; input.focus(); autosize();
      });
      grid.appendChild(b);
    });
  }

  // ── Health ─────────────────────────────────────────────────────────────────
  async function pollHealth(force) {
    // Skip only the RECURRING poll while hidden. The first call must always
    // run: a tab that loads in the background would otherwise leave the status
    // dot on 'unknown' indefinitely, which reads as "broken" rather than
    // "not checked yet".
    if (document.hidden && !force) return;
    try {
      // Deliberately unauthenticated: /health is an open route used as a liveness probe before login.
      const r = await fetch(`${ORCHESTRATOR_URL}/health`, { method: 'GET' });
      const ok = r.ok;
      healthDot.dataset.state = ok ? 'ok' : 'down';
      healthDot.title = ok ? 'Orchestrator reachable' : `Orchestrator returned HTTP ${r.status}`;
    } catch (err) {
      healthDot.dataset.state = 'down';
      healthDot.title = `Orchestrator unreachable: ${(err && err.message) || 'network error'}`;
    }
  }

  // ── Boot ───────────────────────────────────────────────────────────────────
  applyTheme(currentTheme());
  buildSuggestions();
  const restored = restoreThread();
  if (restored) {
    turnSeq = turns.length;
    if (cover && cover.parentNode) cover.remove();
    status('Restored this conversation from the current tab session.');
  }
  updateSessionMeta();
  autosize();

  const token = getToken();
  if (!token) {
    showGate();
  } else {
    hideGate();
    input.focus();
    probeSessionBrowser();
  }

  pollHealth(true);
  setInterval(pollHealth, 30000);
  window.addEventListener('focus', () => pollHealth(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pollHealth(true); });
})();
