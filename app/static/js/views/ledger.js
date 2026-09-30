/**
 * Ledger view — the works list, and the gate that settles it.
 *
 * Two ideas drive the layout:
 *
 * Proposals lead, because nothing below them is trustworthy while they are
 * open. Each one shows the signals that fired, the confidence, and the honest
 * counter-case — the reason it might be wrong. A proposal you cannot argue
 * with is a number you cannot defend.
 *
 * Citation lanes are never blended. The automated lane (OpenAlex, Semantic
 * Scholar) and the manual lane (Scholar) sit in separate columns with their
 * provenance, because they disagree and that disagreement is the honest bound
 * on how precisely any of this can be stated.
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

  const RULING_HELP = {
    confirmed: 'These are one work. Their numbers merge from the next rebuild.',
    split: 'These are separate works. You will not be asked again.',
    excluded: 'Not your work, or not a work. Dropped from the ledger.',
  };

  function lane(value, provenance, which) {
    if (value === null || value === undefined) return '<span class="muted">—</span>';
    const src = (provenance && provenance[which] && provenance[which].source) || '';
    return `${escHtml(value)}${src ? `<br><span class="counts">${escHtml(src)}</span>` : ''}`;
  }

  // A link only appears when a source gave us an identifier for it. Nothing
  // here is a search URL: landing you on the wrong paper would be worse than
  // no link, because you would rule on it confidently.
  function linksHtml(links) {
    if (!links || !links.length) {
      return '<span class="muted">no link — no DOI or source id was given</span>';
    }
    return links.map(l =>
      `<a href="${escHtml(l.url)}" target="_blank" rel="noopener noreferrer">${escHtml(l.label)}</a>`
    ).join(' · ');
  }

  function typeBadge(type) {
    if (!type) return '';
    // Anything that is not a journal article is worth seeing at a glance:
    // a conference abstract and a paper get ruled on differently.
    const cls = type === 'article' ? 'badge-ok' : 'badge-stale';
    return ` <span class="badge ${cls}">${escHtml(type)}</span>`;
  }

  function signalBadges(signals) {
    return (signals || []).map(s =>
      `<span class="badge badge-ok" title="${escHtml(s.detail)}">${escHtml(s.name)}</span>`
    ).join(' ');
  }

  function proposalCard(p) {
    // An adoption asks one question about one work -- "is this yours?" -- so
    // it gets two buttons. Offering "One work / Separate" where there is no
    // second side was asking you to compare a thing with nothing.
    const adopt = p.kind === 'adopt';
    const buttons = adopt
      ? `<button class="action rule-btn" data-ruling="confirmed"
                 title="Yes, this is my work. Its citations join your totals.">Yes, mine</button>
         <button class="action rule-btn" data-ruling="excluded"
                 title="${escHtml(RULING_HELP.excluded)}">Not mine</button>`
      : `<button class="action rule-btn" data-ruling="confirmed"
                 title="${escHtml(RULING_HELP.confirmed)}">One work</button>
         <button class="action rule-btn" data-ruling="split"
                 title="${escHtml(RULING_HELP.split)}">Separate</button>
         <button class="action rule-btn" data-ruling="excluded"
                 title="${escHtml(RULING_HELP.excluded)}">Not mine</button>`;
    const side = (label, type, links, text) => !text ? '' : `
      <tr><th>${escHtml(label)}</th><td>${escHtml(text)}${typeBadge(type)}
        <br><span class="counts">${linksHtml(links)}</span></td></tr>`;
    return `
      <div class="card proposal" data-left="${escHtml(p.left)}"
           data-right="${escHtml(p.right)}" data-kind="${escHtml(p.kind)}">
        <h3>${escHtml(p.kind)} · confidence ${escHtml(p.confidence)}</h3>
        <table class="kv">
          ${side('A', p.left_type, p.left_links, p.left_label)}
          ${adopt
            ? `<tr><th>B</th><td><span class="muted">${escHtml(p.right_label)}</span></td></tr>`
            : side('B', p.right_type, p.right_links, p.right_label)}
          <tr><th>signals</th><td>${signalBadges(p.signals) || '<span class="muted">none</span>'}</td></tr>
        </table>
        ${p.counter_case ? `<p class="muted"><strong>Counter-case:</strong> ${escHtml(p.counter_case)}</p>` : ''}
        <div class="actions">${buttons}</div>
      </div>`;
  }

  // Every row is actionable, because the thing you need to say most often --
  // "that is mine but it is not a publication", or "that is not mine at all"
  // -- is about a work, not about a proposal. Before this, a work that was in
  // no proposal had no exit from the ledger except a lie.
  function rowActions(w, categories) {
    const opts = categories.map(c =>
      `<option value="${escHtml(c)}"${c === w.category ? ' selected' : ''}>${escHtml(c)}</option>`
    ).join('');
    return `
      <select class="cat-select" data-fp="${escHtml(w.fingerprint)}"
              title="Set what kind of output this is">${opts}</select>
      <button class="action drop-btn" data-fp="${escHtml(w.fingerprint)}"
              title="Remove it from the ledger entirely. Use this for work that
                     is not yours, not for your own work in the wrong category.">Not mine</button>`;
  }

  function workRow(w, categories) {
    const years = Object.keys(w.counts_by_year || {}).length;
    return `
      <tr>
        <td>
          ${w.url
            ? `<a href="${escHtml(w.url)}" target="_blank" rel="noopener noreferrer">${
                 escHtml(w.title) || 'untitled'}</a>`
            : (escHtml(w.title) || '<span class="muted">untitled</span>')}
          ${typeBadge(w.category)}
          ${w.on_site ? ' <span class="badge badge-ok">on your CV</span>' : ''}
          <br><span class="counts">${linksHtml(w.links)}</span>
          ${(w.venues_seen || []).length > 1
            ? `<br><span class="counts">also: ${escHtml(w.venues_seen.slice(1).join(', '))}</span>` : ''}
        </td>
        <td class="num">${w.year === null ? '—' : escHtml(w.year)}</td>
        <td>${escHtml(w.venue || '—')}</td>
        <td class="num">${lane(w.citations.automated, w.citations.provenance, 'automated')}</td>
        <td class="num">${lane(w.citations.manual, w.citations.provenance, 'manual')}</td>
        <td class="num">${years ? escHtml(years) : '<span class="muted">—</span>'}</td>
        <td>${escHtml((w.sources || []).join(', '))}
          ${w.category_source === 'you'
            ? '<br><span class="counts">category set by you</span>' : ''}</td>
        <td class="row-actions">${rowActions(w, categories)}</td>
      </tr>`;
  }

  async function render(el) {
    el.innerHTML = '<h1>Your work</h1><p class="lede">Reading…</p>';
    // The shared panels, in hand before the template rather than after it:
    // the Scholar importer is one of them.
    const P = window.raPanels;
    const [led, props] = await Promise.all([
      api.get('/ledger'), api.get('/ledger/proposals'),
    ]);

    const t = led.totals || {};
    const open = props.proposals || [];
    const cats = led.categories || ['publication', 'conference', 'preprint',
                                    'software', 'report', 'other'];
    const byCat = led.by_category || {};

    el.innerHTML = `
      <h1>Your work</h1>
      <p class="lede">One list of everything you have produced — papers, talks,
      software, preprints — assembled from sources that disagree about it, with
      the disagreements settled by you and kept.</p>

      ${led.snapshot === null
        ? `<div class="info"><strong>No snapshot yet.</strong> This is the expected
             state on a fresh deploy — the ledger is built on request, not at startup.
             Use <em>Refresh sources and rebuild</em> below to assemble the first one;
             it fetches OpenAlex and trundlr, so give it a few seconds.</div>`
        : `<div class="${open.length ? 'error' : 'ok'}">
             ${open.length
               ? `<strong>${open.length} proposal(s) awaiting your ruling.</strong>
                  None of them has moved a number — the counts below are what raDash
                  believes <em>before</em> you decide.`
               : 'No open proposals. Every duplicate has been ruled on.'}
           </div>`}

      <div class="card">
        <h2>Totals</h2>
        <table class="kv">
          <tr><th>works</th><td>${escHtml(t.works ?? 0)}</td></tr>
        <tr><th>publications</th><td>${escHtml(t.publications ?? 0)}
          <span class="muted">talks, deposits and preprints are counted below, not here</span></td></tr>
          <tr><th>automated citations</th><td>${escHtml(t.automated ?? 0)} <span class="muted">OpenAlex / Semantic Scholar, max per work</span></td></tr>
          <tr><th>manual citations</th><td>${escHtml(t.manual ?? 0)} <span class="muted">Google Scholar, as of your last paste</span></td></tr>
          <tr><th>on your CV</th><td>${escHtml(t.on_cv ?? 0)}</td></tr>
          <tr><th>unconfirmed</th><td>${escHtml(t.unconfirmed ?? 0)}</td></tr>
        </table>
        <div class="actions">
          <button class="action" id="rebuild-btn">Rebuild from cache</button>
          <button class="action" id="rebuild-refresh-btn">Refresh sources and rebuild</button>
        </div>
        <p class="muted">Rebuilding replays your standing rulings; it cannot overturn one.
        ${led.snapshot ? `Snapshot ${escHtml(led.snapshot.id)}, ${escHtml(led.snapshot.created_at)}.` : ''}</p>
      </div>

      ${open.length ? `<h2>Proposals</h2>${open.map(proposalCard).join('')}` : ''}

      <div class="card">
        <h2>Works</h2>
        <table>
          <thead><tr>
            <th>Work</th><th class="num">Year</th><th>Venue</th>
            <th class="num">Automated</th><th class="num">Manual</th>
            <th class="num">Accrual yrs</th><th>Sources</th><th>What is it?</th>
          </tr></thead>
          <tbody>${(led.works || []).map(w => workRow(w, cats)).join('')}</tbody>
        </table>
        <p class="muted">The two citation lanes are never blended. Where they differ,
        that difference is the honest bound on how precisely the number can be stated.</p>
      </div>

      ${driftHtml(led.drift)}

    ${P ? P.importPanel('scholar', 'Google Scholar citations',
        '/sources/scholar', P.SCHOLAR_HELP) : ''}`;

    if (P) {
      const status = await api.get('/status');
      el.insertAdjacentHTML('beforeend', `<div class="viz-tip" hidden></div>
        ${P.accrual(status.accrual)}${P.momentum(status.momentum)}`);
      P.wireHover(el);
    }

    wire(el);
  }

  // The public CV is not a truth source; it is the claim other people read.
  // The gap between it and the record is the output, and it only exists
  // because something compares them -- you do not notice a line you never
  // added.
  function driftHtml(d) {
    if (!d || !d.checked) {
      // "Not checked" has several causes needing different actions: unset,
      // unreachable, the wrong kind of URL, or a page that parsed to nothing.
      // Reporting all of them as "unset" was wrong in the most annoying way --
      // it told you to set something you had already set.
      const why = d && d.reason
        ? `<div class="info">${escHtml(d.reason)}</div>`
        : `<p class="muted">No public CV page is configured. Set one on the
             <a href="#/settings">Settings</a> page, then
             <em>Refresh sources and rebuild</em>.</p>`;
      return `<div class="card"><h2>Public claim</h2>
        ${why}
        <p class="muted">raDash reads the HTML page that lists your publications,
        not the PDF it links to. Configured on the
        <a href="#/settings">Settings</a> page; a change takes effect on the
        next rebuild.</p></div>`;
    }
    const rows = (list, empty) => list.length
      ? `<table><tbody>${list.map(u => `<tr>
           <td>${u.url
             ? `<a href="${escHtml(u.url)}" target="_blank" rel="noopener noreferrer">${escHtml(u.title)}</a>`
             : escHtml(u.title)}
             ${(u.links && u.links.length)
               ? `<br><span class="counts">${linksHtml(u.links)}</span>` : ''}</td>
           <td class="num">${escHtml(u.year ?? '—')}</td>
           <td>${escHtml(u.category || u.section || '')}</td>
           <td>${u.fingerprint
             ? `<button class="action drop-btn" data-fp="${escHtml(u.fingerprint)}"
                        title="Remove it from the ledger entirely.">Not mine</button>`
             : ''}</td></tr>`).join('')}
         </tbody></table>`
      : `<p class="ok">${escHtml(empty)}</p>`;
    const age = d.claim_age_days;
    return `
      <div class="card">
        <h2>Public claim</h2>
        <p>${escHtml(d.matched)} of your works are on your CV page.
          ${d.cv_file ? `It links <code>${escHtml(d.cv_file)}</code>, ${escHtml(age)} days old.` : ''}</p>
        ${age !== null && age > 120
          ? `<div class="info">Your published CV is ${escHtml(age)} days old.
              Anything below may simply predate it.</div>` : ''}
        <h3>In the record, not on your CV</h3>
        ${rows(d.unclaimed || [], 'Everything the indexes know about is claimed.')}
        <h3>On your CV, not in the record</h3>
        ${rows(d.unindexed || [], 'Everything you claim is indexed somewhere.')}
        <p class="muted">Matched on title, since your CV carries no DOIs — so
        each line is a prompt to look, not a verdict.
        ${d.unindexed_other
          ? `${escHtml(d.unindexed_other)} talks and conference sessions are
             counted but not listed: no index carries them.` : ''}</p>
      </div>`;
  }

  function wire(el) {
    el.querySelectorAll('.cat-select').forEach(sel => {
      sel.addEventListener('change', async () => {
        const prev = sel.dataset.prev || '';
        sel.disabled = true;
        try {
          await api.post('/ledger/classify',
            { fingerprint: sel.dataset.fp, category: sel.value });
          sel.insertAdjacentHTML('afterend',
            '<br><span class="counts">saved — rebuild to apply</span>');
        } catch (err) {
          if (prev) sel.value = prev;
          sel.insertAdjacentHTML('afterend',
            `<br><span class="counts">failed: ${escHtml(err.message)}</span>`);
        }
        sel.disabled = false;
      });
    });

    el.querySelectorAll('.drop-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await api.post('/ledger/rule',
            { fingerprint: btn.dataset.fp, ruling: 'excluded' });
          btn.closest('tr').style.opacity = '0.45';
          btn.textContent = 'dropped';
        } catch (err) {
          btn.disabled = false;
          btn.textContent = 'failed — retry';
        }
      });
    });

    el.querySelectorAll('.rule-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const card = btn.closest('.proposal');
        const ruling = btn.dataset.ruling;
        card.querySelectorAll('button').forEach(b => { b.disabled = true; });
        try {
          const single = ruling === 'excluded' || card.dataset.kind === 'adopt';
          await api.post('/ledger/rule', {
            fingerprint: card.dataset.left,
            other_fingerprint: single ? null : card.dataset.right,
            ruling,
          });
          card.innerHTML = `<p class="ok">Recorded: <strong>${escHtml(ruling)}</strong>.
            ${escHtml(RULING_HELP[ruling])}</p>`;
        } catch (err) {
          card.insertAdjacentHTML('beforeend',
            `<p class="error">Could not record: ${escHtml(err.message)}</p>`);
        }
      });
    });

    ['rebuild-btn', 'rebuild-refresh-btn'].forEach(id => {
      const btn = el.querySelector(`#${id}`);
      if (!btn) return;
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = 'Rebuilding…';
        try {
          await api.post(`/ledger/rebuild${id.includes('refresh') ? '?refresh=true' : ''}`);
          await render(el);
        } catch (err) {
          btn.disabled = false;
          btn.textContent = 'Rebuild failed — retry';
          el.querySelector('.actions').insertAdjacentHTML('beforeend',
            `<p class="error">${escHtml(err.message)}</p>`);
        }
      });
    });

    if (window.raPanels) {
      ['scholar'].forEach(id => window.raPanels.wireImport(el, id));
    }
  }

  registerView('/ledger', async (el) => { await render(el); });
})();
