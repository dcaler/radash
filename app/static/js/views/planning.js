/**
 * Planning — M6-T7.
 *
 * Advisory. It ranks, explains, and stops.
 *
 * The page is four sections and no total. That is the whole design: signal 1
 * says write, signal 2 says read in, signal 3 says you may have found a hole,
 * signal 4 says go and look — and a combined score would rank them against
 * each other as though they were the same kind of thing, producing a top row
 * whose action nobody could name.
 *
 * Every candidate opens three ways. **Why this score** takes the number apart
 * into the inputs that built it. **What is behind it** is the evidence: the
 * items you have read here, the frontier papers that came back, and the
 * nearest thing you have published. **The counter-case** is the reason not
 * to, and it is the only one of the three that is open by default — the rest
 * of the row argues for the candidate, because that is what a score does, and
 * a page of arguments for things is a page you agree with by default.
 */
(function () {
  'use strict';

  function amount(i) {
    return i.effect === 'scales'
      ? `×${i.value.toFixed(2)}` : `+${i.contribution.toFixed(2)}`;
  }

  function inputRow(i) {
    return `<tr>
      <td>${escHtml(i.input.replace(/_/g, ' '))}</td>
      <td class="num">${escHtml(amount(i))}</td>
      <td class="num">${escHtml(i.weight.toFixed(2))} ×
        ${escHtml(i.value.toFixed(2))}</td>
      <td>${escHtml(i.detail)}</td>
    </tr>`;
  }

  function corpusRow(c) {
    const title = c.url
      ? `<a href="${escHtml(c.url)}" target="_blank" rel="noopener noreferrer"
          >${escHtml(c.label)}</a>`
      : escHtml(c.label);
    return `<tr>
      <td>${title}</td>
      <td class="num">${escHtml(c.year ?? '—')}</td>
      <td class="num">${c.cited_by === null || c.cited_by === undefined
        ? '—' : escHtml(c.cited_by)}</td>
      <td>${c.reading === 'collected'
        ? '<span class="muted">no evidence</span>'
        : `<span class="badge badge-ok">${escHtml(c.reading)}</span>`}</td>
    </tr>`;
  }

  function frontierRow(f) {
    return `<tr>
      <td><a href="${escHtml(f.url)}" target="_blank" rel="noopener noreferrer"
          >${escHtml(f.title)}</a>
        ${f.venue ? `<br><span class="counts">${escHtml(f.venue)}</span>` : ''}</td>
      <td class="num">${escHtml(f.year ?? '—')}</td>
      <td class="num">${f.cited_by === null || f.cited_by === undefined
        ? '—' : escHtml(f.cited_by)}</td>
      <td class="num">${f.distance === null || f.distance === undefined
        ? '—' : escHtml(f.distance.toFixed(3))}</td>
    </tr>`;
  }

  /** The score, taken apart. A ranking you cannot decompose is one you
   *  cannot disagree with. */
  function why(c) {
    return `<details class="why">
      <summary>Why this score — ${escHtml(c.score.toFixed(2))}</summary>
      <table>
        <thead><tr><th>Input</th><th class="num">Effect</th>
          <th class="num">Weight × value</th><th>What it is</th></tr></thead>
        <tbody>${c.inputs.map(inputRow).join('')}</tbody>
      </table>
      <p class="muted">Terms marked <code>+</code> add; a term marked
      <code>×</code> scales everything before it. Values are shares of this
      library's own maxima, so a score is comparable between regions of one
      corpus and between nothing else.</p>
    </details>`;
  }

  /** The items and papers the score was computed from. */
  function evidence(c) {
    const near = c.nearest_work
      ? `<p><strong>Nearest work of yours:</strong>
          ${escHtml(c.nearest_work.label)}${c.nearest_work.year
            ? ` (${escHtml(c.nearest_work.year)})` : ''}
          <span class="counts">${escHtml(c.nearest_work.distance)} from this
          region's centre</span></p>`
      : '<p class="muted">No publication of yours could be placed near this region.</p>';
    const topics = (c.topics || []).length
      ? `<p class="muted">Resolved to ${c.topics.map(t =>
          `${escHtml(t.name)} <span class="counts">${(t.share * 100).toFixed(0)}%
          of members</span>`).join(' · ')}.</p>`
      : '';
    return `<details class="evidence">
      <summary>What is behind it</summary>
      ${near}
      <h4>In your library</h4>
      ${c.corpus.length ? `<table>
        <thead><tr><th>Item</th><th class="num">Year</th>
          <th class="num">Cited</th><th>Reading</th></tr></thead>
        <tbody>${c.corpus.map(corpusRow).join('')}</tbody></table>`
        : '<p class="muted">Nothing placed here.</p>'}
      <h4>On the frontier</h4>
      ${c.frontier.length ? `<table>
        <thead><tr><th>Paper</th><th class="num">Year</th>
          <th class="num">Cited</th><th class="num">Distance</th></tr></thead>
        <tbody>${c.frontier.map(frontierRow).join('')}</tbody></table>`
        : `<p class="muted">${c.frontier_age_days === null
            || c.frontier_age_days === undefined
            ? 'No frontier set has been gathered for this region.'
            : 'The gathered set held nothing that landed inside this region.'}</p>`}
      ${topics}
      ${c.frontier_age_days === null || c.frontier_age_days === undefined ? ''
        : `<p class="muted">Frontier set gathered
           ${escHtml(c.frontier_age_days.toFixed(0))} days ago.</p>`}
    </details>`;
  }

  /** Why this might be a bad idea. Open by default, and never empty. */
  function against(c) {
    return `<details class="counter" open>
      <summary>The counter-case — ${escHtml(c.counter_case.length)}
        ${c.counter_case.length === 1 ? 'reason' : 'reasons'} not to</summary>
      <ul class="reasons">${c.counter_case.map(line =>
        `<li>${escHtml(line)}</li>`).join('')}</ul>
    </details>`;
  }

  function badges(c) {
    const out = [];
    if (c.confirmation) {
      out.push(c.confirmation.confirmed
        ? `<span class="badge badge-ok">confirmed</span>`
        : `<span class="badge badge-stale">unconfirmed</span>`);
    }
    if (c.bridge) out.push('<span class="badge badge-degraded">bridge</span>');
    if (!c.counts.live) out.push('<span class="badge badge-missing">dormant</span>');
    return out.join(' ');
  }

  function candidate(c, top) {
    const width = top > 0 ? Math.max(2, (c.score / top) * 100) : 2;
    const confirmation = c.confirmation
      ? `<p class="muted"><strong>${escHtml(c.confirmation.basis)}:</strong>
          ${escHtml(c.confirmation.detail)}</p>` : '';
    const bridge = c.bridge
      ? `<p class="muted"><strong>Bridge:</strong>
          ${escHtml(c.bridge.detail)}.</p>` : '';
    return `<div class="candidate">
      <h3>${escHtml(c.region)} ${badges(c)}</h3>
      <div class="bar-cell">
        <span class="bar" style="width:${width.toFixed(0)}%"
              data-label="score ${c.score.toFixed(2)} on signal ${c.signal}"></span>
        <span class="counts">${escHtml(c.score.toFixed(2))}</span>
      </div>
      <p class="counts">
        <span>${escHtml(c.counts.collected)} collected</span>
        <span>${escHtml(c.counts.read)} with reading evidence</span>
        <span>${escHtml(c.counts.written)} of your works</span>
        ${c.counts.newest_year
          ? `<span>newest ${escHtml(c.counts.newest_year)}</span>` : ''}
        ${c.exemplar ? `<span>most cited: ${escHtml(c.exemplar)}</span>` : ''}
      </p>
      ${confirmation}
      ${bridge}
      ${why(c)}
      ${evidence(c)}
      ${against(c)}
      ${route(c)}
    </div>`;
  }

  /**
   * Where the candidate routes, and the button that takes it there.
   *
   * A signal 1, 2 or confirmed 3 emits a read-in brief; an unconfirmed 3 or a
   * signal 4 emits a corpus fill list, because the honest response to a
   * reading gap is reading and offering to draft a paper there would be
   * flattery.
   *
   * Preview first, always. Writing a file into `output/` that you have not
   * seen is how a directory fills with artifacts nobody trusts, so the button
   * shows you the text and a second press commits it.
   */
  function route(c) {
    return `<div class="actions route-row">
      <div class="route-buttons">
        <button class="action route-btn" type="button"
          data-cluster="${escHtml(c.cluster)}" data-signal="${escHtml(c.signal)}"
          data-kind="${escHtml(c.route.output)}"
          >${escHtml(c.route.label)}</button>
        <span class="counts out"></span>
      </div>
      <span class="counts">${escHtml(c.route.why)}</span>
      <span class="counts">writes <code>${escHtml(c.route.file)}</code> —
        ${escHtml(c.route.detail)}.</span>
      <span class="counts"><a href="#/map">see the region on the map</a> ·
        <a href="#/frontier">see its frontier</a></span>
      <pre class="route-preview" hidden></pre>
    </div>`;
  }

  /** Preview, then commit. The second press is the one that writes. */
  function wireRoutes(el) {
    el.querySelectorAll('.route-btn').forEach(btn => {
      const row = btn.closest('.route-row');
      const out = row.querySelector('.out');
      const pre = row.querySelector('.route-preview');
      const { cluster, signal, kind } = btn.dataset;
      const query = `?cluster=${encodeURIComponent(cluster)}&signal=${
        encodeURIComponent(signal)}`;
      let previewed = false;

      btn.addEventListener('click', async () => {
        btn.disabled = true;
        out.textContent = previewed ? 'writing…' : 'building…';
        try {
          const r = await api.post(
            `/outputs/${kind}${query}${previewed ? '' : '&preview=true'}`);
          pre.textContent = r.text;
          pre.hidden = false;
          if (previewed) {
            out.textContent = `written to ${r.written}`;
            btn.textContent = 'Written';
          } else {
            previewed = true;
            out.textContent = `preview of ${r.filename} — press again to write`;
            btn.textContent = 'Write it';
            btn.disabled = false;
          }
        } catch (err) {
          out.textContent = `failed: ${err.message}`;
          btn.disabled = false;
        }
      });
    });
  }

  function section(s) {
    const top = s.candidates.length ? s.candidates[0].score : 0;
    const body = s.candidates.length
      ? s.candidates.map(c => candidate(c, top)).join('')
      : `<p class="muted">No region passes this signal's gate.</p>`;
    return `<div class="card viz-root">
      <h2>${escHtml(s.signal)} · ${escHtml(s.name)}
        <span class="counts">${escHtml(s.found)} candidate${s.found === 1
          ? '' : 's'} · the move is ${escHtml(s.move)}</span></h2>
      <p class="muted">${escHtml(s.note)}</p>
      ${body}
    </div>`;
  }

  function unassessed(rows) {
    if (!rows.length) return '';
    return `<div class="card">
      <h2>Not assessed <span class="counts">${escHtml(rows.length)}
        region${rows.length === 1 ? '' : 's'}</span></h2>
      <p class="muted">Silence from a source that was never asked is not
      agreement, so these are listed rather than scored zero.</p>
      <ul class="reasons">${rows.map(r =>
        `<li><strong>${escHtml(r.region)}</strong> — ${escHtml(r.reason)}</li>`
      ).join('')}</ul>
      <div class="actions">
        <button class="action gather-btn" type="button">Gather the frontier</button>
        <span class="counts out"></span>
      </div>
    </div>`;
  }

  function header(d) {
    const t = d.thresholds || {};
    return `<div class="card">
      <p>${escHtml(d.regions)} regions on a space of ${escHtml(d.space
        ? d.space.rows : 0)} documents, ${escHtml(d.frontier_queried)} of them
      with a gathered frontier set. ${escHtml(d.works_placed)} of your works
      and ${escHtml(d.briefs_placed)} project briefs are placed in it.</p>
      <p class="muted">${escHtml(d.note)}</p>
      <table class="kv">
        <tr><th>densely read</th><td>${escHtml(t.dense_region)} items or more
          <span class="muted">gates signal 3</span></td></tr>
        <tr><th>thinly read</th><td>${escHtml(t.thin_region)} items or fewer
          <span class="muted">gates signals 2 and 4</span></td></tr>
        <tr><th>quiet after</th><td>${escHtml(t.quiet_years)} years
          <span class="muted">since the newest thing you hold in a region</span></td></tr>
        <tr><th>distances in</th><td>${escHtml(d.geometry)}${d.exact_geometry
          ? '' : ' <span class="muted">the fitted vectors could not be loaded,'
            + ' so distance is read off the picture rather than the space</span>'}</td></tr>
      </table>
      <p class="muted">All three thresholds are judgements about your own
      library rather than facts about the world, and all three are editable on
      <a href="#/settings">Settings</a>.</p>
    </div>`;
  }

  async function render(el) {
    el.innerHTML = '<h1>Planning</h1><p class="lede">Reading…</p>';
    const d = await api.get('/planning');
    const P = window.raPanels;

    if (!d.signals.length) {
      el.innerHTML = `<h1>Planning</h1>
        <div class="info">${escHtml(d.note)}</div>`;
      return;
    }

    el.innerHTML = `
      <h1>Planning</h1>
      <p class="lede">Four signals, scored separately and never blended.
      Advisory: it ranks, explains, and stops. ${P.age(d)}</p>
      <div class="viz-tip" hidden></div>
      ${header(d)}
      ${d.signals.map(section).join('')}
      ${unassessed(d.unassessable || [])}`;
    P.wireHover(el);

    wireRoutes(el);

    el.querySelectorAll('.gather-btn').forEach(btn => {
      const out = btn.parentElement.querySelector('.out');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        out.textContent = 'gathering — a second or so per region…';
        try {
          const r = await api.post('/frontier/refresh');
          out.textContent = `${r.items} papers across ${r.regions} regions`;
          await render(el);
        } catch (err) {
          btn.disabled = false;
          out.textContent = `failed: ${err.message}`;
        }
      });
    });
  }

  registerView('/planning', async (el) => { await render(el); });
})();
