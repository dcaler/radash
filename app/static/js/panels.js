/**
 * Shared dashboard panels.
 *
 * These were all on one page, which was how the page grew to seven panels
 * answering four different questions. They belong beside the thing they
 * describe — accrual next to the works it counts, areas next to the map that
 * produced them — so they live here and each view takes the ones it needs.
 *
 * Exposed through one namespace rather than as globals: view files share a
 * scope, and a helper named here would be a helper named for every one of
 * them.
 */
(function () {
  'use strict';

  function age(block) {
    if (!block || block.age_days === null || block.age_days === undefined) {
      return '<span class="counts">age unknown</span>';
    }
    const d = block.age_days;
    const text = d < 1 ? `${Math.round(d * 24)}h` : `${d.toFixed(0)} days`;
    return `<span class="counts">${escHtml(text)} old · ${escHtml(block.source || '')}</span>`;
  }

  function panel(title, block, body) {
    return `<div class="card viz-root">
      <h2>${escHtml(title)} ${age(block)}</h2>
      ${body}
    </div>`;
  }

  // --- the lead panel ------------------------------------------------------

  function changes(c) {
    if (!c || !c.available) {
      return panel('Changes', c, `<p class="muted">${escHtml(c && c.note
        || 'Nothing to compare yet.')}</p>`);
    }
    const moved = (c.works_moved || []).slice(0, 8).map(m => `
      <tr><td>${escHtml(m.title)}</td>
          <td class="num">${escHtml(m.was)}</td>
          <td class="num">${escHtml(m.now)}</td>
          <td class="num delta ${m.delta > 0 ? 'up' : 'down'}"
            >${m.delta > 0 ? '+' : ''}${escHtml(m.delta)}</td></tr>`).join('');
    const list = (rows, label) => rows.length
      ? `<p><strong>${escHtml(label)}:</strong> ${escHtml(rows.join('; '))}</p>` : '';
    return panel(`Changes since ${new Date(c.since).toISOString().slice(0, 10)}`, c, `
      <p class="hero">${c.citations_delta >= 0 ? '+' : ''}${escHtml(c.citations_delta)}
        <span class="hero-unit">citations in ${escHtml(Math.round(c.since_days))} days</span></p>
      ${list(c.works_added || [], 'Works added')}
      ${list(c.works_removed || [], 'Works removed')}
      ${moved ? `<table>
        <thead><tr><th>Work</th><th class="num">Was</th><th class="num">Now</th>
          <th class="num">Change</th></tr></thead>
        <tbody>${moved}</tbody></table>`
        : '<p class="muted">No citation counts moved.</p>'}`);
  }

  // --- headline ------------------------------------------------------------

  function headline(h) {
    const lanes = h.citations_manual
      ? `<tr><th>manual (Scholar)</th><td>${escHtml(h.citations_manual)}
           <span class="muted">${escHtml(h.lane_gap)} above the automated lane —
           the gap is the bound on both</span></td></tr>`
      : `<tr><th>manual (Scholar)</th><td><span class="muted">nothing imported;
           paste your profile on the Ledger page</span></td></tr>`;
    return panel('Portfolio', h, `
      <p class="hero">${escHtml(h.citations_automated)}
        <span class="hero-unit">citations, automated lane</span></p>
      <table class="kv">
        <tr><th>works</th><td>${escHtml(h.works)}, of which
          ${escHtml(h.publications)} are publications</td></tr>
        ${lanes}
        <tr><th>span</th><td>${h.span ? escHtml(h.span.join('–')) : '—'}</td></tr>
        <tr><th>unconfirmed</th><td>${escHtml(h.unconfirmed)}
          <span class="muted">no ruling recorded</span></td></tr>
      </table>`);
  }

  // --- accrual -------------------------------------------------------------

  function accrual(a) {
    const works = (a.works || []).filter(w => Object.keys(w.counts).length > 1);
    if (!works.length) {
      return panel('Citation accrual', a,
        '<p class="muted">No per-year counts yet.</p>');
    }
    const thisYear = new Date().getUTCFullYear();
    const cells = works.slice(0, 12).map(w => {
      const years = Object.keys(w.counts).map(Number).sort((x, y) => x - y);
      const peak = Math.max(...Object.values(w.counts));
      // Scaled to its own peak, so the *shape* compares between a paper with
      // 348 citations and one with 11. The total is printed beside it, because
      // shape without magnitude is its own kind of lie.
      const bars = years.map((y, i) => {
        const v = w.counts[y];
        const h = Math.max(1.5, (v / peak) * 34);
        const recent = y > thisYear - 2;
        return `<rect class="accr ${recent ? 'accr-recent' : ''}"
          x="${i * 9}" y="${36 - h}" width="6.5" height="${h.toFixed(1)}" rx="2"
          data-label="${escHtml(y)}: ${escHtml(v)} citations"></rect>`;
      }).join('');
      return `<div class="multiple">
        <svg viewBox="0 0 ${Math.max(years.length * 9, 20)} 38"
             preserveAspectRatio="xMaxYMax meet" role="img"
             aria-label="${escHtml(w.title)} from ${escHtml(years[0])} to ${escHtml(years[years.length - 1])}: ${escHtml(w.total)} citations, peak ${escHtml(peak)}"
          >${bars}</svg>
        <div class="multiple-label">${escHtml(w.title.slice(0, 52))}</div>
        <div class="counts">${escHtml(years[0])}–${escHtml(years[years.length - 1])}
          · ${escHtml(w.total)} total · peak ${escHtml(peak)}</div>
      </div>`;
    }).join('');
    return panel('Citation accrual', a, `
      <p class="muted">Each series is scaled to its own peak, so shape is
      comparable across works of very different size. There are no year labels
      — there is no room for them — so every series is <strong>anchored to the
      right, where the present is</strong>: the rightmost bar is the most
      recent year, and the two most recent are emphasised. The first and last
      year are printed under each. A total cannot say whether a paper is
      alive; these can.</p>
      <div class="multiples">${cells}</div>`);
  }

  // --- momentum ------------------------------------------------------------

  function momentum(m) {
    const rows = (m.works || []).slice(0, 12);
    if (!rows.length) return panel('Momentum', m, '<p class="muted">No counts.</p>');
    const bars = rows.map(r => `
      <tr>
        <td>${escHtml(r.title.slice(0, 48))}</td>
        <td class="num">${escHtml(r.year || '—')}</td>
        <td class="num">${escHtml(r.age === null ? '—' : r.age + 'y')}</td>
        <td class="bar-cell">
          <span class="bar" style="width:${(r.share * 100).toFixed(0)}%"
                data-label="${escHtml(r.recent)} of ${escHtml(r.total)} citations"></span>
          <span class="counts">${(r.share * 100).toFixed(0)}%</span>
        </td>
      </tr>`).join('');
    return panel('Momentum', m, `
      <p class="muted">Share of each work's citations earned in the last
      ${escHtml(m.window_years)} years, on one common scale. This ranks
      <em>attention, not quality</em>, and it is unreadable without age beside
      it — a paper published last year reads 100% by construction, which is why
      the year and the age sit in the same row and cannot be hidden.</p>
      <table>
        <thead><tr><th>Work</th><th class="num">Year</th><th class="num">Age</th>
          <th>Recent share</th></tr></thead>
        <tbody>${bars}</tbody>
      </table>`);
  }

  // --- areas ---------------------------------------------------------------

  function areas(a) {
    const regions = a.regions || [];
    if (!regions.length) {
      return panel('Active areas', a, `<p class="muted">${escHtml(a.note
        || 'No regions yet — fit the map first.')}</p>`);
    }
    const maxCollected = Math.max(...regions.map(r => r.collected), 1);
    const rows = regions.map(r => `
      <tr>
        <td>${escHtml(r.name || r.terms.slice(0, 3).join(' · ') || `region ${r.cluster}`)}</td>
        <td class="bar-cell">
          <span class="bar" style="width:${(r.collected / maxCollected * 100).toFixed(0)}%"
                data-label="${escHtml(r.collected)} collected"></span>
          <span class="counts">${escHtml(r.collected)}</span>
        </td>
        <td class="num">${r.read ? escHtml(r.read)
          : '<span class="muted">none</span>'}</td>
        <td class="num">${r.written ? escHtml(r.written)
          : '<span class="muted">none</span>'}</td>
        <td>${escHtml(r.exemplar || '—')}</td>
      </tr>`).join('');
    const unwritten = regions.filter(r => r.collected >= 20 && !r.written).length;
    return panel('Active areas', a, `
      <p class="muted">Collected, read and written, side by side. The first two
      are not the same thing and the gap between them is the more interesting
      number: a region holding ninety papers of which four have been read is a
      backlog, not a body of knowledge. <strong>Read</strong> counts only
      positive evidence — a Zotero tag or collection you set, an annotation, a
      note, or a file Zotero saw you open — so it undercounts anything read
      elsewhere. ${escHtml(unwritten)} substantial regions have collecting
      behind them and nothing published.</p>
      <table>
        <thead><tr><th>Region</th><th>Collected</th><th class="num">Read</th>
          <th class="num">Written</th><th>Most-cited member</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">${escHtml(a.unclustered)} read items sit between regions
      and are left there.</p>`);
  }

  // --- drift ---------------------------------------------------------------

  function drift(d) {
    const p = d.position;
    const pub = d.public || {};
    const unclaimed = (pub.unclaimed || []).length;
    return panel('Drift', d, `
      ${p ? `<p>Your ${escHtml(p.published.n)} published works and
        ${escHtml(p.in_progress.n)} current projects sit in
        <strong>${p.displacement < 0.02 ? 'much the same place'
          : p.displacement < 0.08 ? 'nearby but distinguishable places'
          : 'noticeably different places'}</strong> on the map.</p>
      <p class="muted">There is a number behind that — ${escHtml(p.displacement)}
      — and on its own it means nothing: it is a distance in the units of
      whichever two components the map happens to be drawn on, so it is not
      comparable between projections, between refits, or with anyone else.
      What it is good for is <em>watching</em>: the same measure on the same
      space, refit after refit, says whether you are moving. The honest
      version of this panel is the arrow on the map.</p>
      <p><a href="#/map">See it on the map</a> — the arrow runs from where your
      older work sits to where your recent work sits.</p>`
        : '<p class="muted">Needs both published works and project briefs in a fitted space.</p>'}
      ${pub.checked ? `<p><strong>Against your public CV:</strong>
        ${escHtml(pub.matched)} matched, ${escHtml(unclaimed)} in the record and
        not on the page${pub.claim_age_days !== null
          ? `, from a CV ${escHtml(pub.claim_age_days)} days old` : ''}.</p>`
        : '<p class="muted">No public CV configured, so nothing to compare.</p>'}`);
  }

  // --- coverage ------------------------------------------------------------

  function coverage(c) {
    const sources = (c.sources || []).map(s => `
      <tr><td>${escHtml(s.key)}</td>
        <td><span class="badge badge-${escHtml(s.status)}">${escHtml(s.status)}</span></td>
        <td class="num">${s.items === null ? '—' : escHtml(s.items)}</td>
        <td class="num">${s.age_days === null ? '—' : escHtml(s.age_days) + 'd'}</td></tr>`).join('');
    return panel('What raDash cannot see', c, `
      <p class="muted">Every figure here is counted from current state. A
      reassuring constant would be worse than no panel, because it would be
      believed.</p>
      <table class="kv">
        <tr><th>fitted documents</th><td>${escHtml(c.fitted_documents)}</td></tr>
        <tr><th>between regions</th><td>${escHtml(c.unclustered)}
          ${c.unclustered_share !== null
            ? `<span class="muted">${(c.unclustered_share * 100).toFixed(0)}% of the corpus</span>` : ''}</td></tr>
        <tr><th>regions</th><td>${escHtml(c.regions)}</td></tr>
        <tr><th>unconfirmed works</th><td>${escHtml(c.unconfirmed_works)}
          <span class="muted">${escHtml(c.rulings_made)} rulings recorded,
          ${escHtml(c.excluded)} exclusions</span></td></tr>
        <tr><th>published, not on your CV</th><td>${escHtml(c.unclaimed_publicly)}</td></tr>
        <tr><th>public CV age</th><td>${c.public_cv_age_days === null
          || c.public_cv_age_days === undefined ? '—'
          : escHtml(c.public_cv_age_days) + ' days'}</td></tr>
        <tr><th>manual citation lane</th><td>${c.manual_lane_empty
          ? '<span class="muted">empty — nothing pasted from Scholar</span>'
          : 'populated'}</td></tr>
      </table>
      <h3>Sources</h3>
      <table>
        <thead><tr><th>Source</th><th>State</th><th class="num">Items</th>
          <th class="num">Read</th></tr></thead>
        <tbody>${sources}</tbody>
      </table>`);
  }

  function readingList(r) {
    const rows = (r.candidates || []).map(c => `
      <tr>
        <td>${c.url
            ? `<a href="${escHtml(c.url)}" target="_blank" rel="noopener noreferrer">${escHtml(c.label)}</a>`
            : escHtml(c.label)}
          ${c.frontier ? ' <span class="badge badge-stale">not yours yet</span>' : ''}
          ${c.region ? `<br><span class="counts">${escHtml(c.region)}</span>` : ''}</td>
        <td class="num">${escHtml(c.year || '—')}</td>
        <td class="num">${c.cited_by === null ? '—' : escHtml(c.cited_by)}</td>
        <td><ul class="reasons">${c.reasons.map(w =>
          `<li><span class="counts">${w.effect === 'damps'
             ? `×${w.weight.toFixed(2)}` : `+${w.weight.toFixed(2)}`}</span>
             ${escHtml(w.detail)}</li>`).join('')}</ul></td>
      </tr>`).join('');
    if (!rows) {
      return panel('What to read next', r, `<p class="muted">${escHtml(r.note
        || 'Nothing to suggest yet — fit the map first.')}</p>`);
    }
    // Where the order comes from, stated before the order. The drift term is
    // the heaviest in the score, and a list reordered by something the reader
    // cannot see is a list they can only take on trust.
    const drift = r.drift
      ? `<p class="muted"><strong>Ordered with your drift.</strong> The
         heaviest term is how far a paper lies along the direction your work
         has travelled — from the centre of the ${escHtml(r.drift.older)}
         works you published up to ${escHtml(r.drift.cut)}, toward the centre
         of the ${escHtml(r.drift.recent)} since. That is the arrow on the
         <a href="#/map">Landscape</a>. A paper level with your recent work
         earns half of it; one as far beyond again earns all of it, because
         the question is what to read <em>next</em> and matching where you
         already are is not next.</p>`
      : `<p class="muted">No drift term in this ranking: it needs at least
         four placed works spanning more than six years. Without it the list
         leans on where you already are rather than on where you are going.</p>`;
    return panel('What to read next', r, `
      ${drift}
      <p class="muted">${escHtml(r.collected_unread)} items collected with no
      evidence of reading, against ${escHtml(r.with_reading_evidence)} with
      some, ranked together with frontier papers you do not hold at all. Each
      row shows every signal that put it there and what it contributed;
      nothing here is a verdict, and a ranking you cannot take apart is one
      you cannot disagree with.</p>
      <table>
        <thead><tr><th>Item</th><th class="num">Year</th>
          <th class="num">Cited</th><th>Why it is here</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">${escHtml(r.note || '')}</p>`);
  }

  // --- imports -------------------------------------------------------------

  // Mirrors the server's limit, so an oversized file is refused here with a
  // sentence rather than there with a 422.
  const MAX_IMPORT_BYTES = 512 * 1024;

  /**
   * Google Scholar, step by step.
   *
   * This lives beside the control rather than in a README because the source
   * panel used to answer "nothing here yet" with *drop a Scholar export in
   * /app/data/import* — a path inside raDash's own Docker volume, with no host
   * side, and no way to open it. The instruction was impossible to follow and
   * unnecessary to follow, since the importer below writes there itself.
   */
  const SCHOLAR_HELP = `
    <p class="muted">Scholar has no API worth the name, so this lane is manual:
    about a minute, roughly monthly. It is always the largest of the three
    citation lanes and always the oldest, and raDash dates it rather than
    quietly showing you March's number as today's.</p>
    <ol class="steps">
      <li>Open your Google Scholar profile — <code>scholar.google.com</code>,
        then your own name.</li>
      <li>Scroll to the bottom and press <strong>Show more</strong> until it
        stops appearing. Scholar lists twenty works at a time, and anything
        still hidden is simply not in the import.</li>
      <li>Select the table — from the <strong>TITLE</strong> header down to the
        last row — and copy it.</li>
      <li>Paste it below, or save it as a <code>.txt</code> and press
        <strong>Upload a file…</strong>. Dropping a file onto the box works
        too.</li>
      <li>Press <strong>Preview</strong>, and check the <em>with citation
        counts</em> figure. That column is the whole reason this one is done
        by hand.</li>
    </ol>
    <p class="muted">What a good paste looks like: three header cells, then
    each work as an indented title, its authors, and a row carrying the venue,
    the count and the year.</p>
    <pre class="sample">TITLE
CITED BY
YEAR
    The Dispossessed: an ambiguous utopia of residential solar PV
N Jemisin, OE Butler, J Russ
Renewable Energy 12, 101-120    314    2016</pre>
    <p class="muted">A work with no citations leaves that cell empty, and
    raDash records it as uncaptured rather than as zero — those are different
    facts.</p>
    <p class="muted"><strong>Scholar's CSV export will be refused.</strong> It
    carries no citation column — none of Scholar's exports do — so it cannot
    move the manual lane, and its entries would reach the ledger as DOI-less
    candidates you then have to rule on one by one. The counts exist only on
    the page.</p>
    <p class="muted">Nothing is stored until you press <strong>Import</strong>.
    The import is written inside raDash's own volume, which is created on first
    use — there is no folder on your machine to put anything in, and never
    was.</p>`;

  function importPanel(id, title, endpoint, help) {
    return `
      <div class="card">
        <h2>${escHtml(title)}</h2>
        ${help}
        <textarea id="${id}-text" class="import-text" rows="8"
          placeholder="Paste the list here, or drop a saved file on this box…"></textarea>
        <div class="actions import-actions">
          <button class="action" id="${id}-upload" type="button">Upload a file…</button>
          <button class="action" id="${id}-preview"
            data-endpoint="${escHtml(endpoint)}">Preview</button>
          <button class="action" id="${id}-commit"
            data-endpoint="${escHtml(endpoint)}" disabled>Import</button>
          <input type="file" id="${id}-file" class="import-file" hidden
            accept=".txt,.csv,text/plain,text/csv">
          <span class="counts" id="${id}-file-name"></span>
        </div>
        <div id="${id}-out"></div>
      </div>`;
  }

  function wireImport(el, id) {
    const text = el.querySelector(`#${id}-text`);
    const out = el.querySelector(`#${id}-out`);
    const commit = el.querySelector(`#${id}-commit`);
    const preview = el.querySelector(`#${id}-preview`);
    if (!text || !out || !commit || !preview) return;
    const endpoint = preview.dataset.endpoint;
    const picker = el.querySelector(`#${id}-file`);
    const upload = el.querySelector(`#${id}-upload`);
    const chosen = el.querySelector(`#${id}-file-name`);

    async function runPreview() {
      out.innerHTML = '<p class="muted">Parsing…</p>';
      try {
        const r = await api.post(`${endpoint}/preview`, { text: text.value });
        const rows = (r.entries || []).slice(0, 12).map(e => `
          <tr>
            <td>${escHtml(e.title)}${e.confident === false
              ? ' <span class="badge badge-stale">check</span>' : ''}</td>
            <td class="num">${escHtml(e.year ?? '—')}</td>
            <td class="num">${escHtml(e.cited_by ?? e.doi ?? '—')}</td>
          </tr>`).join('');
        out.innerHTML = `
          <p>${escHtml(r.count)} entries${r.uncertain
            ? `, ${escHtml(r.uncertain)} to check` : ''}${
            r.with_citations !== undefined
              ? `, ${escHtml(r.with_citations)} with citation counts` : ''}.</p>
          <table><tbody>${rows}</tbody></table>
          ${r.refused ? `<p class="error">${escHtml(r.refused)}</p>` : ''}
          ${r.note ? `<p class="muted">${escHtml(r.note)}</p>` : ''}`;
        // A preview that says the file will be refused must not leave Import
        // live: the 422 behind it is a backstop, not the explanation.
        commit.disabled = !r.count || Boolean(r.refused);
      } catch (err) {
        out.innerHTML = `<p class="error">${escHtml(err.message)}</p>`;
        commit.disabled = true;
      }
    }

    /** Take a file and show what it parsed to.
     *
     * Previewing straight away rather than waiting for a second click: a file
     * you chose that produces no visible change reads as a control that did
     * not work, and the next thing you do is choose it again.
     */
    async function take(file) {
      if (!file) return;
      if (chosen) chosen.textContent = `${file.name} · ${Math.max(1, Math.round(file.size / 1024))} KB`;
      if (file.size > MAX_IMPORT_BYTES) {
        out.innerHTML = `<p class="error">${escHtml(file.name)} is
          ${escHtml(Math.round(file.size / 1024))} KB and the limit is
          ${escHtml(MAX_IMPORT_BYTES / 1024)} KB. This expects a publication
          list, not a document.</p>`;
        return;
      }
      try {
        text.value = await file.text();
      } catch (err) {
        out.innerHTML = `<p class="error">Could not read ${escHtml(file.name)}:
          ${escHtml(err.message)}</p>`;
        return;
      }
      await runPreview();
    }

    preview.addEventListener('click', runPreview);
    if (upload && picker) {
      upload.addEventListener('click', () => picker.click());
      picker.addEventListener('change', () => take(picker.files && picker.files[0]));
    }
    // The box says a file can be dropped on it, so it has to accept one.
    ['dragenter', 'dragover'].forEach(ev => text.addEventListener(ev, (e) => {
      e.preventDefault();
      text.classList.add('dropping');
    }));
    ['dragleave', 'dragend'].forEach(ev => text.addEventListener(ev, () => {
      text.classList.remove('dropping');
    }));
    text.addEventListener('drop', (e) => {
      e.preventDefault();
      text.classList.remove('dropping');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      // A drag of selected text rather than a file: let the textarea have it.
      if (f) take(f);
    });

    commit.addEventListener('click', async () => {
      commit.disabled = true;
      try {
        const r = await api.post(`${endpoint}/import`, { text: text.value });
        out.innerHTML = `<p class="ok">Stored as <code>${escHtml(r.stored)}</code>.
          ${escHtml(r.note || '')}</p>`;
      } catch (err) {
        out.innerHTML = `<p class="error">${escHtml(err.message)}</p>`;
        commit.disabled = false;
      }
    });
  }

  function wireHover(el) {
    const tip = el.querySelector('.viz-tip');
    if (!tip) return;
    el.querySelectorAll('[data-label]').forEach(mark => {
      const show = (e) => {
        tip.textContent = mark.dataset.label;
        tip.hidden = false;
        const box = el.getBoundingClientRect();
        tip.style.left = `${e.clientX - box.left + 12}px`;
        tip.style.top = `${e.clientY - box.top + 12}px`;
      };
      mark.addEventListener('mouseenter', show);
      mark.addEventListener('mousemove', show);
      mark.addEventListener('mouseleave', () => { tip.hidden = true; });
    });
  }

  window.raPanels = {
    age, panel, changes, headline, accrual, momentum, areas, readingList,
    drift, coverage, wireHover, importPanel, wireImport, SCHOLAR_HELP,
  };
})();
