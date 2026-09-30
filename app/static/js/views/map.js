/**
 * Map view — M3-T9.
 *
 * Colour carries *kind*, not region. On a scatter of an embedding space the
 * regions are already separated by position, so hueing them would spend the
 * palette on information the geometry already gives — and the categorical
 * palette validates only three slots under the all-pairs rule a scatter
 * needs, against seven to twenty-two regions. So the three hues go to the
 * distinction the map exists to show: what you have read, what you have
 * written, and what you are working on. Regions are named in place.
 *
 * The corpus is drawn as a recessive density layer because it is context —
 * a thousand-odd small marks establishing the shape of the field. Your works
 * and projects are the foreground, at full marker size with hit targets
 * larger than the mark, because they are what the page is asking about.
 *
 * Light-mode aqua sits below 3:1 on the surface, so the relief rule applies:
 * the legend is labelled and a table view carries the same content.
 */
(function () {
  'use strict';

  const KINDS = [
    { key: 'corpus', label: 'Read', slot: 1, help: 'items in your Zotero library' },
    { key: 'work', label: 'Written', slot: 2, help: 'works from the ledger' },
    { key: 'project', label: 'In progress', slot: 3, help: 'project briefs' },
  ];

  // Your own work is sized by citations. Area carries the value, not radius:
  // at 348 against 46 a linear radius would make the one paper look seven
  // times the other rather than under three times, which is the standard way
  // a bubble overstates its outlier. Floored so an uncited work is still a
  // visible, clickable mark — zero citations is a fact about a paper, not a
  // reason to hide it — and capped so one paper cannot swamp the map.
  const R_MIN = 4;
  const R_MAX = 15;

  function radius(citations, max) {
    if (!citations || citations <= 0 || !max) return R_MIN;
    return R_MIN + (R_MAX - R_MIN) * Math.sqrt(citations) / Math.sqrt(max);
  }

  const W = 720;
  const H = 460;
  const PAD = 28;

  function extent(values) {
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (!isFinite(lo)) return [-1, 1];
    if (lo === hi) return [lo - 1, hi + 1];
    const pad = (hi - lo) * 0.04;
    return [lo - pad, hi + pad];
  }

  function scaler([lo, hi], size) {
    return (v) => PAD + ((v - lo) / (hi - lo)) * (size - PAD * 2);
  }

  // What sits at each end of an axis, in the axis's own words. Without this
  // the reader is told the horizontal axis is "component 1" and left to infer
  // direction from the scatter, which is the one thing a scatter cannot say.
  function pole(axes, component, end, n) {
    const a = (axes || []).find(x => x.component === component);
    if (!a) return '';
    return ((end === 'high' ? a.positive : a.negative) || []).slice(0, n).join(' · ');
  }

  function axisName(axes, component) {
    const a = (axes || []).find(x => x.component === component);
    if (!a) return `component ${component}`;
    if (a.name) return escHtml(a.name);
    const pos = (a.positive || []).slice(0, 3).join(', ');
    return `component ${component}${pos ? ` — ${escHtml(pos)}` : ''}`;
  }

  // Two centres of mass for your own work, older and recent, and the vector
  // between them. This is the one view that makes "am I becoming a different
  // researcher than my CV says" a measurable question — and it has to be
  // recomputed per projection, because a direction in one pair of components
  // is not a direction in another.
  const RECENT_YEARS = 6;

  function driftVector(pts) {
    const works = pts.filter(p => p.kind === 'work' && p.year);
    if (works.length < 4) return null;
    const newest = Math.max(...works.map(p => p.year));
    const cut = newest - RECENT_YEARS;
    const older = works.filter(p => p.year <= cut);
    const recent = works.filter(p => p.year > cut);
    if (!older.length || !recent.length) return null;
    const mean = (rows, k) => rows.reduce((a, p) => a + p[k], 0) / rows.length;
    return {
      from: { x: mean(older, 'x'), y: mean(older, 'y'), n: older.length },
      to: { x: mean(recent, 'x'), y: mean(recent, 'y'), n: recent.length },
      cut,
    };
  }

  function scatter(data) {
    const dx = (data.display || {}).x ?? 1;
    const dy = (data.display || {}).y ?? 2;
    const pts = data.points || [];
    if (!pts.length) return '<p class="muted">No points in this space.</p>';
    const sx = scaler(extent(pts.map(p => p.x)), W);
    const sy = scaler(extent(pts.map(p => p.y)), H);

    const drift = driftVector(pts);
    const maxCites = Math.max(0, ...pts.filter(p => p.kind === 'work')
      .map(p => p.citations || 0));

    const layer = (kind) => pts.filter(p => p.kind === kind).map(p => {
      const r = kind === 'corpus' ? 2.4
        : kind === 'work' ? radius(p.citations, maxCites) : 5.5;
      const cites = p.citations === null || p.citations === undefined
        ? '' : ` · ${p.citations} citations`;
      return `<circle class="pt pt-${kind}" cx="${sx(p.x).toFixed(1)}"
        cy="${(H - sy(p.y)).toFixed(1)}" r="${r.toFixed(1)}"
        data-label="${escHtml(p.label)}${escHtml(cites)}"
        data-kind="${escHtml(kind)}"
        data-cluster="${p.cluster === null ? '' : p.cluster}"></circle>`;
    }).join('');

    // Region names sit on the map rather than in a colour key: with up to
    // twenty-two of them a legend would be a second thing to read.
    const labels = (data.clusters || []).slice(0, 12).map(c => `
      <text class="region" x="${sx(c.x).toFixed(1)}" y="${(H - sy(c.y)).toFixed(1)}"
            text-anchor="middle">${escHtml(c.name || (c.terms || [])[0] || `region ${c.cluster}`)}</text>`
    ).join('');

    return `
      <svg viewBox="0 0 ${W} ${H + 34}" class="map-svg" role="img"
           aria-label="Document space; read items in blue, your works in orange,
                       projects in green. Horizontal axis runs from
                       ${escHtml(pole(data.axes, dx, 'low', 3))} to
                       ${escHtml(pole(data.axes, dx, 'high', 3))}; vertical from
                       ${escHtml(pole(data.axes, dy, 'low', 3))} to
                       ${escHtml(pole(data.axes, dy, 'high', 3))}.">
        <line class="grid" x1="${PAD}" y1="${H / 2}" x2="${W - PAD}" y2="${H / 2}"></line>
        <line class="grid" x1="${W / 2}" y1="${PAD}" x2="${W / 2}" y2="${H - PAD}"></line>
        ${layer('corpus')}${labels}${layer('work')}${layer('project')}
        <text class="pole" x="${PAD}" y="${H / 2 - 7}" text-anchor="start"
          >← ${escHtml(pole(data.axes, dx, 'low', 3))}</text>
        <text class="pole" x="${W - PAD}" y="${H / 2 - 7}" text-anchor="end"
          >${escHtml(pole(data.axes, dx, 'high', 3))} →</text>
        <text class="pole" x="${W / 2 + 8}" y="${PAD - 4}" text-anchor="start"
          >↑ ${escHtml(pole(data.axes, dy, 'high', 3))}</text>
        <text class="pole" x="${W / 2 + 8}" y="${H - PAD + 14}" text-anchor="start"
          >↓ ${escHtml(pole(data.axes, dy, 'low', 3))}</text>
        ${drift ? `
        <defs>
          <marker id="drift-head" viewBox="0 0 10 10" refX="9" refY="5"
                  markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" class="drift-head"></path>
          </marker>
        </defs>
        <line class="drift" x1="${sx(drift.from.x).toFixed(1)}"
          y1="${(H - sy(drift.from.y)).toFixed(1)}"
          x2="${sx(drift.to.x).toFixed(1)}" y2="${(H - sy(drift.to.y)).toFixed(1)}"
          marker-end="url(#drift-head)"></line>
        <circle class="drift-from" cx="${sx(drift.from.x).toFixed(1)}"
          cy="${(H - sy(drift.from.y)).toFixed(1)}" r="4"></circle>` : ''}
        <text class="axis-title" x="${W / 2}" y="${H + 26}" text-anchor="middle"
          >horizontal: ${axisName(data.axes, dx)}</text>
      </svg>
      <div class="map-axis-note">
        <span><strong>horizontal (component ${escHtml(dx)}):</strong>
          ${escHtml(pole(data.axes, dx, 'low', 5) || '—')}
          &nbsp;↔&nbsp; ${escHtml(pole(data.axes, dx, 'high', 5) || '—')}</span>
        <span><strong>vertical (component ${escHtml(dy)}):</strong>
          ${escHtml(pole(data.axes, dy, 'low', 5) || '—')}
          &nbsp;↔&nbsp; ${escHtml(pole(data.axes, dy, 'high', 5) || '—')}</span>
      </div>
      ${drift ? `<p class="muted"><strong>The arrow is your own drift.</strong>
      It runs from the centre of your ${drift.from.n} works published up to
      ${escHtml(drift.cut)} to the centre of the ${drift.to.n} since. It is
      recomputed for whichever components are on screen: a direction in one
      projection is not a direction in another, and a short arrow here may be a
      long one elsewhere.</p>` : `<p class="muted">No drift arrow: it needs
      works on both sides of ${escHtml(RECENT_YEARS)} years ago, and there are
      too few dated works in this space.</p>`}
      <p class="muted">The terms at each end are the ones loading most strongly
      there. They describe the direction, not a category: a document near the
      left is not "about" those words, it is further that way than one on the
      right.</p>`;
  }

  function scree(data, axes, space) {
    const dx = (data.display || {}).x ?? 1;
    const dy = (data.display || {}).y ?? 2;
    const vals = (axes || []).map(a => a.explained_variance || 0);
    if (!vals.length) return '';
    const max = Math.max(...vals, 0.0001);
    const W = vals.length * 22 + 34;
    const bars = vals.map((v, i) => {
      const h = Math.max(2, (v / max) * 44);
      const x = 34 + i * 22;
      return `<rect class="scree-bar" x="${x}" y="${52 - h}" width="14"
          height="${h.toFixed(1)}" rx="2"
          data-label="component ${i}: ${(v * 100).toFixed(2)}% of variance"></rect>
        <text class="scree-value" x="${x + 7}" y="${48 - h}" text-anchor="middle"
          >${(v * 100).toFixed(1)}</text>
        <text class="scree-tick" x="${x + 7}" y="64" text-anchor="middle"
          >c${i}</text>`;
    }).join('');
    const shown = vals.reduce((a, b) => a + b, 0);
    const all = space && space.explained_variance;
    return `
      <div class="scree">
        <svg viewBox="0 0 ${W} 70" class="scree-svg" role="img"
             aria-label="Percentage of variance explained by each component">
          <line class="grid" x1="30" y1="52" x2="${W}" y2="52"></line>
          <text class="scree-axis" x="0" y="14">% of</text>
          <text class="scree-axis" x="0" y="24">variance</text>
          ${bars}
        </svg>
        <p class="muted">Variance per component, in the order the fit produced
        them. The ${vals.length} shown account for ${(shown * 100).toFixed(1)}%
        ${all ? `of the ${(all * 100).toFixed(1)}% all
        ${escHtml(space.dimensions)} components explain between them` : ''}.</p>
        <p class="muted"><strong>These percentages are lower than they look,
        and that is expected.</strong> The decomposition does not centre the
        data, so variance here means spread around the average document, and a
        corpus of short abstracts spreads thinly across many directions rather
        than concentrating in a few. It is not a score out of a hundred and a
        low figure does not mean the space is empty — the regions below are
        the test of whether there is structure, and there are
        ${escHtml((data.clusters || []).length)} of them.</p>
        <p class="muted"><strong>Component 0 is not used at all.</strong> Every
        document scores positive on it: it points along the average document
        and measures how typical a text is rather than what it is about, which
        is why it holds the largest singular value and almost no variance. It
        is excluded from the map, from the axis choices and from the
        clustering. The map is drawn on components ${escHtml(dx)} and
        ${escHtml(dy)}.</p>
        <p class="muted">The textbook fix is to centre the data before
        decomposing, which would make component 0 meaningful instead of
        discardable. raDash does not, because centring densifies a
        1,735&nbsp;&times;&nbsp;11,337 sparse matrix and the deploy target has
        two cores. Dropping the mean direction is the cheap equivalent, and
        nothing else about the space changes.</p>
      </div>`;
  }

  // Which projection you want depends on what you are looking for, so the
  // pair is chosen rather than fixed. Each option carries its own terms,
  // because "component 7" tells you nothing about whether it is worth drawing.
  function axisPicker(data) {
    const dx = (data.display || {}).x ?? 1;
    const dy = (data.display || {}).y ?? 2;
    const choosable = (data.axes || []).filter(a => a.component > 0);
    const opts = (sel) => choosable.map(a => {
      const terms = (a.positive || []).slice(0, 3).join(' · ');
      const pct = ((a.explained_variance || 0) * 100).toFixed(2);
      return `<option value="${a.component}"${a.component === sel ? ' selected' : ''}
        >c${a.component} — ${escHtml(a.name || terms || 'no terms')} (${pct}%)</option>`;
    }).join('');
    return `
      <div class="axis-picker">
        <label>horizontal <select id="pick-x">${opts(dx)}</select></label>
        <label>vertical <select id="pick-y">${opts(dy)}</select></label>
        <span class="counts">${data.display && data.display.reprojected
          ? 'reprojected from the stored vectors'
          : 'the pair this fit was drawn on'}</span>
      </div>`;
  }

  function legend(data) {
    const cites = (data.points || []).filter(p => p.kind === 'work')
      .map(p => p.citations || 0);
    const max = Math.max(0, ...cites);
    // A size legend, because area is not readable without a reference: two
    // marks at known values say more than a sentence about scaling.
    const sizeKey = max > 0 ? `
      <span class="viz-key size-key">
        <svg viewBox="0 0 ${R_MAX * 4} ${R_MAX * 2}" width="${R_MAX * 4}"
             height="${R_MAX * 2}" role="img" aria-label="Marker size by citations">
          <circle class="pt-work" cx="${R_MAX}" cy="${R_MAX}" r="${R_MIN}"></circle>
          <circle class="pt-work" cx="${R_MAX * 2.6}" cy="${R_MAX}"
                  r="${radius(max, max).toFixed(1)}"></circle>
        </svg>
        <span class="muted">0 and ${escHtml(max)} citations — area, not width</span>
      </span>` : '';
    return `<div class="viz-legend">${KINDS.map(k =>
      `<span class="viz-key"><i class="swatch s${k.slot}"></i>${escHtml(k.label)}
        <span class="muted">${escHtml(k.help)}</span></span>`).join('')}${sizeKey}</div>`;
  }

  function stamp(space) {
    return `
      <table class="kv">
        <tr><th>fitted</th><td>${escHtml(space.created_at)}</td></tr>
        <tr><th>backend</th><td>${escHtml(space.backend)} · <code>${escHtml(space.model)}</code></td></tr>
        <tr><th>documents</th><td>${escHtml(space.rows)} in ${escHtml(space.dimensions)} dimensions</td></tr>
        <tr><th>variance explained</th><td>${((space.explained_variance || 0) * 100).toFixed(1)}%</td></tr>
        <tr><th>corpus fingerprint</th><td><code>${escHtml(space.corpus_hash)}</code></td></tr>
        <tr><th>took</th><td>${escHtml(space.fit_seconds)}s</td></tr>
      </table>
      <p class="muted">The fingerprint is of the exact text this was fitted on.
      A space whose fingerprint no longer matches your library is stale as a
      matter of fact, not of judgement.</p>`;
  }

  function previousFits(previous) {
    if (!previous || !previous.length) {
      return '<p class="muted">This is the first fit. Earlier ones are kept '
        + 'rather than replaced, so a refit that goes wrong stays comparable '
        + 'against the one before it.</p>';
    }
    return `
      <h3>Earlier fits</h3>
      <table>
        <thead><tr><th>Fitted</th><th class="num">Documents</th>
          <th class="num">Variance</th><th>Corpus fingerprint</th></tr></thead>
        <tbody>${previous.map(p => `<tr>
          <td>${escHtml(p.created_at)}</td>
          <td class="num">${escHtml(p.rows)}</td>
          <td class="num">${((p.explained_variance || 0) * 100).toFixed(1)}%</td>
          <td><code>${escHtml(p.corpus_hash)}</code></td></tr>`).join('')}
        </tbody>
      </table>
      <p class="muted">A different fingerprint means the corpus changed between
      fits, so the two are about different libraries and their numbers are not
      directly comparable.</p>`;
  }

  function regionTable(data) {
    const rows = (data.clusters || []).map(c => `
      <tr>
        <td><input class="region-name" data-cluster="${c.cluster}"
                   value="${escHtml(c.name || '')}"
                   placeholder="${escHtml((c.terms || [])[0] || 'unnamed')}"></td>
        <td class="num">${escHtml(c.size)}</td>
        <td><span class="counts">${escHtml((c.terms || []).join(' · '))}</span></td>
        <td>${escHtml(c.exemplar || '—')}</td>
      </tr>`).join('');
    return `
      <table>
        <thead><tr><th>Region</th><th class="num">Items</th>
          <th>Distinguishing terms</th><th>Most-cited member</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">Terms are those with the most lift over the rest of the
      corpus, not the most frequent inside the region — "energy" is everywhere
      in this library and separates nothing. The exemplar is the most-cited
      member rather than the most central: the centre is a synthetic point
      nothing sits on.</p>`;
  }

  function axesPanel(data) {
    return (data.axes || []).slice(0, 8).map(a => `
      <div class="setting" data-component="${a.component}">
        <strong>Component ${a.component}</strong>
        <span class="counts">${((a.explained_variance || 0) * 100).toFixed(1)}% of variance</span>
        <table class="kv">
          <tr><th>one end</th><td><span class="counts">${escHtml((a.positive || []).join(', ') || '—')}</span></td></tr>
          <tr><th>the other</th><td><span class="counts">${escHtml((a.negative || []).join(', ') || '—')}</span></td></tr>
        </table>
        <input class="axis-name" data-component="${a.component}"
               value="${escHtml(a.name || '')}"
               placeholder="name it, or leave it numbered">
        <div class="actions"><button class="action axis-save">Save</button>
          <span class="counts out"></span></div>
      </div>`).join('');
  }

  // rabbitHole grouped the same papers independently, by argument, and named
  // each group. That makes it the closest thing to an external check this map
  // has — and the two numbers have to be read together, because they fail in
  // different directions.
  function agreementRow(r) {
    // Tidy themes that all land in one region: raDash has an area where
    // rabbitHole sees several arguments.
    const coarse = r.agreement < 0.1 && r.mean_concentration > 0.8;
    return `
      <tr>
        <td>${escHtml(r.project.replace(/^\d+_/, ''))}
          <br><span class="counts">${escHtml(r.compared)} of
          ${escHtml(r.nodes)} papers compared</span></td>
        <td class="num">${escHtml(r.agreement)}</td>
        <td class="num">${(r.mean_concentration * 100).toFixed(0)}%</td>
        <td>${escHtml(r.themes)} themes fell into
          ${escHtml(r.regions_touched)} region${r.regions_touched === 1 ? '' : 's'}
          ${coarse ? '<br><span class="badge badge-stale">coarser than the reading</span>'
            : ''}</td>
      </tr>`;
  }

  function agreementPanel(data) {
    const rows = (data.projects || []).filter(p => p.compared >= 8);
    if (!rows.length) {
      return `<div class="card"><h2>Against rabbitHole's reading</h2>
        <p class="muted">No project has enough of its litmap placed on the map
        to compare yet. A litmap paper can only be compared if it is in Zotero,
        carries enough text to be placed, and fell inside a region.</p></div>`;
    }
    const body = rows.map(agreementRow).join('');
    return `<div class="card">
      <h2>Against rabbitHole's reading</h2>
      <p class="muted">Read the two numbers together, because they fail
      differently. <strong>Agreement</strong> is adjusted for chance: 0 means
      the two groupings are unrelated, 1 means identical.
      <strong>Concentration</strong> is the share of a theme's papers landing
      in its single most common region. High concentration with near-zero
      agreement is the interesting case — every theme lands somewhere tidy,
      but they all land in the <em>same</em> region, which means raDash has one
      area where rabbitHole sees several distinct arguments.</p>
      <table>
        <thead><tr><th>Project</th><th class="num">Agreement</th>
          <th class="num">Concentration</th><th>What that looks like</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      <p class="muted">Disagreement is not automatically raDash being wrong.
      rabbitHole groups one project's reading by argument; raDash clusters the
      whole library by vocabulary. They are different questions, and a theme
      that holds together in one and dissolves in the other is worth knowing
      about either way.</p>
    </div>`;
  }

  async function render(el, pair) {
    el.innerHTML = '<h1>Landscape</h1><p class="lede">Reading…</p>';
    const query = pair ? `?x=${encodeURIComponent(pair.x)}&y=${
      encodeURIComponent(pair.y)}` : '';
    const data = await api.get(`/map${query}`);

    if (!data.space) {
      el.innerHTML = `
        <h1>Landscape</h1>
        <p class="lede">The shape of what you read, and where your own work
        sits in it.</p>
        <div class="info"><strong>No space has been fitted yet.</strong>
          The map is built on request. On the deploy target this takes about
          five seconds, plus a one-off fifteen-second import on a cold
          container.</div>
        <div class="card"><div class="actions">
          <button class="action" id="fit-btn">Fit the space</button>
          <span class="counts out"></span>
        </div></div>`;
      wireFit(el);
      return;
    }

    el.innerHTML = `
      <h1>Landscape</h1>
      <p class="lede">The shape of what you read, and where your own work sits
      in it.</p>
      <div class="card">
        <div class="actions">
          <button class="action primary" id="fit-btn">Refit the space</button>
          <span class="counts out"></span>
        </div>
        <p class="muted">Fitted ${escHtml(String(data.space.created_at).slice(0, 10))}
        on ${escHtml(data.space.rows)} documents. Refit after your library
        changes, or after changing a setting that affects the map — it takes a
        few seconds. Earlier fits are kept, and listed under Reproducibility
        below.</p>
      </div>
      <div class="card viz-root">
        <h2>The space</h2>
        ${axisPicker(data)}
        ${legend(data)}
        ${scatter(data)}
        <div class="viz-tip" hidden></div>
        <p class="muted">The corpus defines the space; your works and projects
        are placed into it rather than helping to build it — otherwise asking
        where your work sits relative to the field would partly be asking where
        it sits relative to itself. ${escHtml(data.unclustered)} read items fall
        between regions and are left there.</p>
      </div>
      <div class="card viz-root"><h2>How much structure is there?</h2>
        ${scree(data, data.axes, data.space)}</div>
      <div class="card"><h2>Regions</h2>${regionTable(data)}</div>
      <div class="card"><h2>Axes</h2>
        <p class="muted">The terms below are not names — they are the terms
        that load most heavily at each end of a component, read straight off
        the arithmetic. raDash stops there deliberately: turning
        <em>"agent, simulation, models"</em> into a phrase like "methodological
        orientation" would read well and assert a structure the fit may not
        contain, and every later reading of the map would be anchored to it. A
        numbered component beats a confabulated label. Naming is yours, and
        what you write is kept across refits.</p>
        ${axesPanel(data)}</div>
      <div class="card"><h2>Reproducibility</h2>${stamp(data.space)}
        ${previousFits(data.previous)}</div>`;

    const P = window.raPanels;
    if (P) {
      const [status, agree] = await Promise.all([
        api.get('/status'), api.get('/map/agreement'),
      ]);
      // What to read next moved to Frontier. It is a question about the
      // literature — half its rows are papers you do not hold — and this page
      // is about the shape of what you already have.
      el.insertAdjacentHTML('beforeend',
        `${P.areas(status.areas)}${agreementPanel(agree)}`);
    }

    wireFit(el);
    wireHover(el);
    wireNames(el);
    wirePicker(el);
  }

  function wirePicker(el) {
    const x = el.querySelector('#pick-x');
    const y = el.querySelector('#pick-y');
    if (!x || !y) return;
    const redraw = async () => {
      x.disabled = y.disabled = true;
      await render(el, { x: x.value, y: y.value });
    };
    x.addEventListener('change', redraw);
    y.addEventListener('change', redraw);
  }

  function wireFit(el) {
    const btn = el.querySelector('#fit-btn');
    if (!btn) return;
    const out = btn.parentElement.querySelector('.out');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      out.textContent = 'fitting — a few seconds, longer on a cold container…';
      try {
        await api.post('/map/fit?refresh=false');
        await render(el);
      } catch (err) {
        btn.disabled = false;
        out.textContent = `failed: ${err.message}`;
      }
    });
  }

  function wireHover(el) {
    const tip = el.querySelector('.viz-tip');
    if (!tip) return;
    el.querySelectorAll('.pt, .scree-bar').forEach(mark => {
      const show = (e) => {
        const label = mark.dataset.label || '';
        const cluster = mark.dataset.cluster;
        tip.innerHTML = escHtml(label) +
          (cluster ? `<br><span class="muted">region ${escHtml(cluster)}</span>` : '');
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

  function wireNames(el) {
    el.querySelectorAll('.axis-save').forEach(btn => {
      btn.addEventListener('click', async () => {
        const box = btn.closest('.setting');
        const input = box.querySelector('.axis-name');
        const out = box.querySelector('.out');
        btn.disabled = true;
        try {
          await api.post(`/map/axis/${box.dataset.component}?name=${
            encodeURIComponent(input.value)}`);
          out.textContent = input.value ? 'saved' : 'left numbered';
        } catch (err) {
          out.textContent = `failed: ${err.message}`;
        }
        btn.disabled = false;
      });
    });

    el.querySelectorAll('.region-name').forEach(input => {
      input.addEventListener('change', async () => {
        input.disabled = true;
        try {
          await api.post(`/map/cluster/${input.dataset.cluster}?name=${
            encodeURIComponent(input.value)}`);
        } catch (_) { /* the placeholder still shows the terms */ }
        input.disabled = false;
      });
    });
  }

  registerView('/map', async (el) => { await render(el); });
})();
