// frontend/app.js
// Extracted from index.html so the CSP can drop 'unsafe-inline' from
// script-src. Keeping the app inline forced that directive, and
// 'unsafe-inline' is what would have made the trace-renderer innerHTML sink
// (fixed separately) actually executable. Same code, served same-origin.
//
// NOTE: entrypoint.sh substitutes the ORCHESTRATOR_URL placeholder in THIS
// file at container start -- it used to patch index.html.
// ── Config ─────────────────────────────────────────────────────────────────
  // In Docker: orchestrator is accessed from the browser via the host machine.
  // ORCHESTRATOR_URL is nginx-substituted at container start.
  const ORCHESTRATOR_URL = window.__ORCHESTRATOR_URL__ || 'http://localhost:8090';

  // ── Elements ───────────────────────────────────────────────────────────────
  const form        = document.getElementById('query-form');
  const input       = document.getElementById('query-input');
  const submitBtn   = document.getElementById('submit-btn');
  const loading     = document.getElementById('loading');
  const answerCard  = document.getElementById('answer-card');
  const answerText  = document.getElementById('answer-text');
  const sqlCard     = document.getElementById('sql-card');
  const sqlText     = document.getElementById('sql-text');
  const citCard     = document.getElementById('citations-card');
  const citList     = document.getElementById('citation-list');
  const errorCard   = document.getElementById('error-card');
  const errorText   = document.getElementById('error-text');
  const traceCard   = document.getElementById('trace-card');
  const chartCont   = document.getElementById('chart-container');
  const latencyBadge= document.getElementById('latency-badge');
  const footerStatus= document.getElementById('footer-status');

  // ── Suggestion buttons ─────────────────────────────────────────────────────
  document.querySelectorAll('.suggestion-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      input.value = btn.textContent.trim();
      input.focus();
    });
  });

  // ── Reset UI ───────────────────────────────────────────────────────────────
  function resetUI() {
    answerCard.classList.remove('visible');
    sqlCard.classList.remove('visible');
    citCard.classList.remove('visible');
    errorCard.classList.remove('visible');
    traceCard.classList.remove('visible');
    chartCont.classList.remove('visible');
    chartCont.innerHTML = '';
    answerText.textContent = '';
    sqlText.textContent = '';
    citList.textContent = '';
    errorText.textContent = '';
    traceCard.innerHTML = '';
  }

  // ── Citations renderer ─────────────────────────────────────────────────────
  // SECURITY: DOM nodes + textContent, same discipline as renderTrace -- a
  // chunk_id ultimately traces back to ingested document content.
  //
  // The API has returned this array since E1-V4 (build_citations, A7-adjacent:
  // main.py recomputes it at the process boundary so it can't be dropped
  // in transit) and nothing in the page ever read it -- a document-route
  // answer reached the user with zero visible source. Audit 003 F-003-5.
  function renderCitations(citations) {
    if (!citations || citations.length === 0) return;
    citList.textContent = '';
    citations.forEach((c) => {
      const item = document.createElement('div');
      item.className = 'citation-item';
      const kindEl = document.createElement('span');
      kindEl.className = 'citation-kind';
      kindEl.textContent = c.kind === 'chunk' ? 'doc' : c.kind;
      const refEl = document.createElement('span');
      refEl.className = 'citation-ref';
      refEl.textContent = c.kind === 'chunk' && c.page != null
        ? `${c.ref} (p.${c.page})`
        : c.ref;
      item.appendChild(kindEl);
      item.appendChild(refEl);
      citList.appendChild(item);
    });
    citCard.classList.add('visible');
  }

  function showLoading(show) {
    loading.classList.toggle('visible', show);
    submitBtn.disabled = show;
  }

  // ── Trace renderer ─────────────────────────────────────────────────────────
  // SECURITY: built with DOM nodes and textContent, never innerHTML.
  //
  // This previously interpolated trace values straight into innerHTML, which
  // is an XSS sink fed by untrusted data: state_trace carries `sql_rows` (raw
  // database content) and echoes `normalized_query` (the user's own text).
  //
  // Characterised honestly, because the distinction matters: this was a
  // LATENT vulnerability, not an exploited one. The Part F adversarial corpus
  // (scripts/seed_adversarial.py) is prompt-injection PROSE -- it contains no
  // <script>, no onerror, no executable construct -- so nothing currently
  // seeded achieves script execution. What the database does contain is
  // angle-bracketed text: two `delivery_events.note` rows carry
  // `<tool_call>...</tool_call>` payloads. That proves untrusted markup
  // reaches this sink. Had any payload been executable it would have run,
  // because the page's CSP allows 'unsafe-inline' for script-src (it must:
  // the app is a single inline <script>).
  //
  // There is a second, already-active bug here that is not about XSS at all:
  // innerHTML PARSES those `<tool_call>` tags as unknown elements and drops
  // them from the rendered output, so an analyst reading the trace to inspect
  // an injection attempt saw the payload silently vanish -- the security
  // trace concealing the exact thing it exists to show. textContent displays
  // it verbatim.
  //
  // Same threat model as F-V1's "tool poisoning through data", one layer
  // further out, where nothing was checking.
  function renderTrace(trace) {
    if (!trace) return;
    traceCard.textContent = '';
    Object.entries(trace).forEach(([k, v], i) => {
      if (i > 0) traceCard.appendChild(document.createTextNode('\n'));
      const keyEl = document.createElement('span');
      keyEl.className = 'trace-key';
      keyEl.textContent = k;
      const valEl = document.createElement('span');
      valEl.className = 'trace-value';
      valEl.textContent = typeof v === 'object' ? JSON.stringify(v) : String(v);
      traceCard.appendChild(keyEl);
      traceCard.appendChild(document.createTextNode(': '));
      traceCard.appendChild(valEl);
    });
    traceCard.classList.add('visible');
  }

  // ── A7 validator, validator 2 of 2 (E2, A7 enforcement) ───────────────────
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
    const interp = [
      window.vega && window.vega.expressionInterpreter,
      window.vegaInterpreter && window.vegaInterpreter.expressionInterpreter,
      window.expressionInterpreter,
    ].find(usable);
    if (!interp) {
      console.error('[A7] vega-interpreter unavailable — refusing to render via the eval-based compiler.');
    }
    return interp;
  }

  // ── Chart renderer ────────────────────────────────────────────────────────
  async function renderChart(spec) {
    if (!spec) return;

    // Validator 2 of 2 — runs BEFORE render, independently of whatever the
    // orchestrator concluded. No repair path: an invalid spec is dropped, the
    // prose answer stays, and the failure is logged.
    const violations = validateA7Chart(spec);
    if (violations.length > 0) {
      console.error('[A7 validation failure] chart rejected before render, not repaired:', violations);
      chartCont.classList.remove('visible');
      chartCont.innerHTML = '';
      return;
    }

    const interp = expressionInterpreter();
    if (!interp) { chartCont.classList.remove('visible'); return; }

    try {
      chartCont.classList.add('visible');
      // CSP-safe rendering (A7): parse to an AST and evaluate expressions with
      // vega-interpreter, never the default compiler, which builds functions
      // via `Function(...)` and would need `unsafe-eval`. The CSP in
      // frontend/nginx.conf omits unsafe-eval, so the compiler path would
      // throw rather than silently work — that is what makes E2-V4 a real
      // test instead of a cosmetic one.
      await vegaEmbed(chartCont, spec, {
        renderer: 'canvas',
        actions: false,
        config: { background: '#111827', view: { stroke: 'transparent' } },
        ast: true,
        expr: interp,
      });
    } catch (err) {
      console.error('Chart render error (A7 violation or vega error):', err);
      chartCont.classList.remove('visible');
    }
  }

  // ── Submit handler ─────────────────────────────────────────────────────────
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;

    resetUI();
    showLoading(true);
    footerStatus.textContent = 'Running…';

    try {
      const resp = await fetch(`${ORCHESTRATOR_URL}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      });

      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(errBody.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();

      // Answer
      answerText.textContent = data.answer_text || '';
      answerCard.classList.add('visible');
      latencyBadge.textContent = data.latency_ms ? `${data.latency_ms}ms` : '';

      // SQL (from state trace)
      const sql = data.state_trace?.executed_sql;
      if (sql) {
        sqlText.textContent = sql;
        sqlCard.classList.add('visible');
      }

      // Citations (kind:"sql" and/or kind:"chunk", per E1-V4)
      renderCitations(data.citations);

      // State trace
      renderTrace(data.state_trace);

      // Chart (Phase E2 — chart will be null in Phase B)
      if (data.chart) {
        await renderChart(data.chart);
      }

      footerStatus.textContent = `Done. query_id=${data.query_id}`;

    } catch (err) {
      errorText.textContent = err.message || 'Unknown error.';
      errorCard.classList.add('visible');
      footerStatus.textContent = `Error: ${err.message}`;
    } finally {
      showLoading(false);
    }
  });
