/**
 * Sources view — per-source counts, staleness and parse failures.
 *
 * The panel is built around one distinction: a source can be *present and
 * wrong*. `ok` and `error` are the easy cases; `stale` (cached, labelled with
 * its age) and `degraded` (read, but partially, with the reason) are the ones
 * that matter, because those are the states in which a number still renders
 * and is no longer true. They get their own colours and their failures are
 * printed rather than summarised behind a count.
 *
 * Nothing here fetches the network on load. The refresh button is the only
 * path that does, and it says so.
 */
/*
 * Scoped in an IIFE. View files are plain <script> tags sharing one global
 * scope, so a helper named here is a helper named for every other view — and
 * two views that both call their entry point `render` silently overwrite each
 * other, with the survivor decided by script order in index.html. That is what
 * broke the ledger page: sources.js loads later, its `render(el, data)` won
 * and the ledger called it with no data.
 *
 * Only `registerView` crosses the boundary, which is the whole interface a
 * view needs. tests/test_frontend.py asserts no view leaks a global.
 */
(function () {
  'use strict';

  function ageText(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const h = seconds / 3600;
    if (h < 1) return `${Math.max(0, Math.round(seconds / 60))} min`;
    if (h < 48) return `${h.toFixed(1)} h`;
    return `${(h / 24).toFixed(0)} days`;
  }

  function countsHtml(counts) {
    const keys = Object.keys(counts || {});
    if (!keys.length) return '';
    return `<div class="counts">${keys.map(k =>
      `<span><strong>${escHtml(counts[k])}</strong> ${escHtml(k.replace(/_/g, ' '))}</span>`
    ).join('')}</div>`;
  }

  // Six sources with terse keys and five states with technical names. Without
  // this the panel is a table of jargon reporting on jargon.
  const WHAT_IT_IS = {
    zotero: 'your reference library, read from its SQLite file',
    haarpi: 'project folders carrying a haarpi.yaml and a corpus ledger',
    trundlr: 'the project tracker: what work exists and its priority',
    openalex: 'an open index of published work — the automated citation lane',
    s2: 'Semantic Scholar, a second citation index. It exists to disagree with '
      + 'OpenAlex: where the two differ, that gap bounds both',
    scholar: 'Google Scholar, pasted by hand — the manual citation lane, '
      + 'always the largest number and always the oldest',
    website: 'your public CV page: a source of claims, not of truth',
  };

  const WHAT_STATE_MEANS = {
    ok: 'read cleanly',
    stale: 'served from cache, with its age shown — not an error',
    degraded: 'read, but only partly; the note says what was missing',
    missing: 'nothing to read yet, which on a fresh deploy is expected',
    error: 'the read failed; the note says why',
  };

  function sourceRow(s) {
    const failures = (s.failures || []).length
      ? `<ul class="failures">${s.failures.slice(0, 5).map(f => `<li>${escHtml(f)}</li>`).join('')}
         ${s.failures.length > 5 ? `<li>…and ${s.failures.length - 5} more</li>` : ''}</ul>`
      : '';
    return `
      <tr>
        <td>
          <strong>${escHtml(s.key)}</strong>
          <br><span class="counts">${escHtml(WHAT_IT_IS[s.key] || '')}</span>
          ${s.note ? `<br><span class="muted">${escHtml(s.note)}</span>` : ''}
          ${countsHtml(s.counts)}
          ${failures}
        </td>
        <td class="num">${s.count === null ? '—' : escHtml(s.count)}</td>
        <td class="num">${escHtml(ageText(s.age_seconds))}</td>
        <td><span class="badge badge-${escHtml(s.status)}">${escHtml(s.status)}</span>
        <br><span class="counts">${escHtml(WHAT_STATE_MEANS[s.status] || '')}</span></td>
      </tr>`;
  }

  function joinHtml(join) {
    if (!join) return '';
    const unmatched = (label, list, why) => !list.length ? '' : `
      <p class="muted"><strong>${escHtml(label)}</strong> (${list.length}): 
      ${escHtml(list.join(', '))}<br>${escHtml(why)}</p>`;
    return `
      <div class="card">
        <h2>Proposed join</h2>
      <p class="muted">Three systems name the same work three ways: trundlr
      calls it <code>lathe_dev</code>, Zotero calls it <code>lathe</code>, and
      the folder is <code>260601_lathe</code>. Nothing enforces agreement, so
      this is raDash's proposal for which names mean the same project, with
      the basis for each. <code>declared</code> means the project's own
      <code>haarpi.yaml</code> states its trundlr id, which settles it;
      <code>exact</code> and <code>stem</code> are name comparisons, which do
      not. It matters because every per-project number downstream is joined
      this way.</p>
        <p>${join.matched} matches — ${join.complete} present in all three systems.
        Basis: ${Object.entries(join.by_basis || {})
          .map(([b, n]) => `${n} ${escHtml(b)}`).join(', ')}.</p>
        ${unmatched('Zotero collections with no project', join.unmatched_zotero,
          'Reading with nothing tracking it.')}
        ${unmatched('trundlr projects with no collection', join.unmatched_trundlr,
          'Tracked work with no reading behind it — which is a finding, not a bug.')}
        ${unmatched('Project folders not joined', join.unmatched_folders,
          'A folder whose manifest names nothing raDash could match.')}
        <p class="muted">A match is a proposal with a stated basis, never an assumed
        identity. <code>declared</code> comes from <code>haarpi.yaml</code>;
        <code>exact</code> and <code>stem</code> are name comparisons.</p>
      </div>`;
  }

  function render(el, data) {
    // Scholar is the one source with no automated path at all, and the panel
    // that says so is the panel the importer belongs beside. It was on Your
    // work only, which is a page you go to after the ledger exists — not the
    // page that tells you the lane is empty.
    const P = window.raPanels;
    const failing = data.failing || [];
    const waiting = data.waiting || [];
    const stale = data.stale || [];

    // Three states, deliberately distinct. A failure is something to fix; a
    // source with nothing in it yet is something to do; a stale one is fine and
    // just needs its age shown.
    const parts = [];
    // Offline leads, because it changes how every other row should be read:
    // a stale source is not a source that failed, it is one nobody asked.
    if (data.offline) {
      parts.push(`<div class="info"><strong>Offline.</strong> raDash did not
        open a socket. Every network source below is serving its cache with
        the age attached, and nothing was fetched or attempted — turn it off
        on <a href="#/settings">Settings</a> to reach the network again.</div>`);
    }
    if (failing.length) {
      parts.push(`<div class="error"><strong>${failing.length} source(s) failed:</strong>
        ${escHtml(failing.join(', '))}. Panels that depend on them will say so rather
        than show a number.</div>`);
    }
    if (waiting.length) {
      parts.push(`<div class="info"><strong>Nothing yet from:</strong>
        ${escHtml(waiting.join(', '))}. This is the expected state on a fresh deploy —
        the network sources are only fetched when you ask, and Scholar waits for an
        import — the panel at the foot of this page is where that happens. Nothing is
        broken.</div>`);
    }
    if (stale.length) {
      parts.push(`<div class="info">Serving cached data for
        ${escHtml(stale.join(', '))}; the age is in the table.</div>`);
    }
    if (!parts.length) parts.push('<div class="ok">Every source read cleanly.</div>');
    const banner = parts.join('');

    el.innerHTML = `
      <h1>Sources</h1>
      <p class="lede">What each source contains, how old it is, and what failed reading it.</p>
      ${banner}
      <div class="card">
        <h2>Per source</h2>
        <table>
          <thead><tr><th>Source</th><th class="num">Items</th><th class="num">Data age</th><th>Status</th></tr></thead>
          <tbody>${data.sources.map(sourceRow).join('')}</tbody>
        </table>
        <div class="actions">
          <button class="action" id="refresh-btn"${data.offline ? ' disabled' : ''}
            >${data.offline ? 'Offline — nothing to fetch'
                            : 'Refresh network sources'}</button>
        </div>
        <p class="muted">Zotero and the project folders are read live on every load.
        ${escHtml((data.network_sources || []).join(', '))} are served from cache until
        you refresh — a diagnostic that spends three API round-trips per page view is one
        nobody leaves open. <em>Data age</em> is the age of the data, not of the read.</p>
      </div>
      ${joinHtml(data.join)}
      ${P ? P.importPanel('scholar', 'Google Scholar citations',
          '/sources/scholar', P.SCHOLAR_HELP) : ''}`;

    if (P) P.wireImport(el, 'scholar');

    const btn = el.querySelector('#refresh-btn');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Fetching…';
      try {
        render(el, await api.post('/sources/refresh'));
      } catch (err) {
        btn.disabled = false;
        btn.textContent = 'Refresh network sources';
        el.querySelector('.actions').insertAdjacentHTML('beforeend',
          `<p class="muted">Refresh failed: ${escHtml(err.message)}</p>`);
      }
    });
  }

  registerView('/sources', async (el) => {
    el.innerHTML = '<h1>Sources</h1><p class="lede">Reading…</p>';
    render(el, await api.get('/sources'));
  });
})();
