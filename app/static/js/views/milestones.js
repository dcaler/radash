/**
 * Milestones — each track's pathway to its deliverable, drawn the way the
 * HAARPi figures draw it.
 *
 * Everything shown comes from the project's `.haarpi/status.json`; raDash
 * derives none of it.
 *
 * A **track** is one pathway to one deliverable: SchellingChords for CSS 2026
 * and SchellingChords for Leonardo are two tracks, sharing the stages before
 * the paper forks and nothing after. A project with no venue chosen yet is one
 * track that stops at the fork.
 *
 * Every track is laid on the same columns, literature review through deck, so
 * how far each has come reads straight down the page, and the rows are sorted
 * by it. State is carried by form rather than colour, as in the figures: an
 * artifact that exists is drawn as one, a step not reached is a dot on the
 * path, and the path is solid as far as the work has come.
 */
(function () {
  'use strict';

  const COLUMNS = [
    { key: 'litreview', label: 'literature review' },
    { key: 'methodsreview', label: 'methods review' },
    { key: 'design', label: 'design' },
    { key: 'build', label: 'build' },
    { key: 'experiments', label: 'experiments' },
    { key: 'onepager', label: 'one-pager' },
    { key: 'venue', label: 'venue' },
    { key: 'outline', label: 'outline' },
    { key: 'draft', label: 'draft' },
    { key: 'submission', label: 'submission' },
    { key: 'deck', label: 'deck' },
  ];

  const WAITING = { you: 'with you', tool: 'needs next', queue: 'queued', running: 'running' };

  const LABEL_W = 200;
  const COL_W = 96;
  const ROW_H = 46;
  const HEAD_H = 56;
  const ART_W = 24;
  const ART_H = 18;

  /** Which column a milestone id lands in. */
  function columnOf(id) {
    const parts = id.split('.');
    if (parts[0] === 'paper') return parts[parts.length - 1];
    if (parts[0] === 'deck') return 'deck';
    return parts[0];
  }

  /** How far a step has got: done beats in flight beats opened. */
  function rank(m) {
    return m.state === 'done' ? 3 : m.state === 'in_flight' ? 2 : m.state === 'open' ? 1 : 0;
  }

  /** One project's tracks: the shared stages, plus each venue's own rungs. */
  function tracksOf(p) {
    const shared = p.milestones.filter(m => !m.track);
    const venues = [...new Set(p.milestones.filter(m => m.track).map(m => m.track))];
    const outcome = Object.fromEntries((p.tracks || []).map(t => [t.venue, t.outcome]));
    const build = (venue, own) => {
      const cells = {};
      [...shared, ...own].forEach(m => {
        const col = columnOf(m.id);
        // A track can carry several decks (a long talk and a poster); the
        // column shows the furthest along.
        if (!cells[col] || rank(m) > rank(cells[col])) cells[col] = m;
      });
      let reach = -1;
      COLUMNS.forEach((c, i) => {
        const m = cells[c.key];
        if (m && m.state === 'done') reach = Math.max(reach, i + 0.5);
        if (m && m.state === 'in_flight') reach = Math.max(reach, i);
      });
      return { project: p.name, venue, outcome: venue ? outcome[venue] : null,
               archived: p.archived, cells, reach };
    };
    return venues.length
      ? venues.map(v => build(v, p.milestones.filter(m => m.track === v)))
      : [build(null, [])];
  }

  /** An artifact: a document with a tab, as the figures draw a release. */
  function artifact(cx, cy, cls) {
    const x = cx - ART_W / 2;
    const y = cy - ART_H / 2;
    return `<path class="${cls}" d="M${x},${y} h9 l3,4 h${ART_W - 12} v${ART_H - 4} h-${ART_W} z"/>`;
  }

  function tip(m) {
    return [
      `${m.id}: ${m.state.replace('_', ' ')}${m.implied ? ' (implied by later work)' : ''}`,
      m.released ? `released ${m.released.date}: ${m.released.file}` : '',
      m.document && (!m.released || m.document !== m.released.file) ? `working on: ${m.document}` : '',
      m.waiting_on ? `waiting on: ${WAITING[m.waiting_on] || m.waiting_on}` : '',
      (m.blocked_by || []).length ? `needs: ${m.blocked_by.join(', ')}` : '',
      (m.stale_inputs || []).length ? `built on an older ${m.stale_inputs.join(', ')}` : '',
      m.target ? `target ${m.target}${m.health === 'late' ? ' — late'
        : m.health === 'at_risk' ? ' — at risk' : ''}` : '',
    ].filter(Boolean).join('\n');
  }

  function node(m, cx, cy) {
    let mark;
    let note = '';
    if (m.state === 'done') {
      mark = artifact(cx, cy, m.implied ? 'fig-implied' : 'fig-released');
    } else if (m.state === 'in_flight') {
      mark = artifact(cx, cy, 'fig-working');
      note = m.health === 'late' ? 'late' : (WAITING[m.waiting_on] || '');
    } else if (m.state === 'open' || m.state === 'unlocked') {
      mark = `<circle class="fig-ready" cx="${cx}" cy="${cy}" r="4.5"/>`;
    } else {
      mark = `<circle class="fig-dot" cx="${cx}" cy="${cy}" r="3"/>`;
    }
    const label = note
      ? `<text class="fig-note" x="${cx}" y="${cy + ART_H / 2 + 11}" text-anchor="middle">${escHtml(note)}</text>` : '';
    return `<g><title>${escHtml(tip(m))}</title>${mark}${label}</g>`;
  }

  function trackRow(t, i, cols) {
    const cy = HEAD_H + i * ROW_H + ROW_H / 2 - 4;
    const xs = cols.map((c, j) => LABEL_W + j * COL_W + COL_W / 2);
    const present = cols.map((c, j) => ({ j, m: t.cells[c.key] })).filter(e => e.m);

    // The path: solid into a step the work has reached, dashed beyond it.
    let path = '';
    for (let k = 1; k < present.length; k += 1) {
      const a = present[k - 1];
      const b = present[k];
      path += `<line class="${rank(b.m) >= 2 ? 'fig-path' : 'fig-path-ahead'}"
        x1="${xs[a.j] + ART_W / 2 + 3}" y1="${cy}" x2="${xs[b.j] - ART_W / 2 - 3}" y2="${cy}"/>`;
    }
    const sub = t.venue
      ? `<text class="fig-sub" x="12" y="${cy + 12}">${escHtml(t.venue)}${t.outcome
        ? ` · ${escHtml(t.outcome)}` : ''}</text>` : '';
    return `<g class="${t.archived ? 'fig-archived' : ''}">
      <text class="fig-name" x="12" y="${t.venue ? cy - 2 : cy + 4}">${escHtml(t.project)}</text>
      ${sub}
      ${path}${present.map(e => node(e.m, xs[e.j], cy)).join('')}</g>`;
  }

  function header(cols, height) {
    const at = cols.findIndex(c => c.key === 'onepager');
    const fork = at > 0
      ? `<line class="fig-rule" x1="${LABEL_W + at * COL_W}" y1="${HEAD_H - 40}"
          x2="${LABEL_W + at * COL_W}" y2="${height}"/>
         <text class="fig-group" x="${LABEL_W + at * COL_W + 6}" y="${HEAD_H - 32}">the paper — one track per venue</text>` : '';
    return fork + cols.map((c, j) => `<text class="fig-col" x="${LABEL_W + j * COL_W + COL_W / 2}"
        y="${HEAD_H - 12}" text-anchor="middle">${escHtml(c.label)}</text>`).join('');
  }

  function legend() {
    const item = (mark, text, x) => `<g transform="translate(${x},0)">${mark}
      <text class="fig-sub" x="18" y="4">${escHtml(text)}</text></g>`;
    return `<svg class="fig-legend" viewBox="0 0 720 22" role="img" aria-label="legend">
      <g transform="translate(14,11)">
        ${item(artifact(0, 0, 'fig-released'), 'released', 0)}
        ${item(artifact(0, 0, 'fig-implied'), 'done, implied by later work', 100)}
        ${item(artifact(0, 0, 'fig-working'), 'in progress', 300)}
        ${item('<circle class="fig-ready" cx="0" cy="0" r="4.5"/>', 'ready to start', 420)}
        ${item('<circle class="fig-dot" cx="0" cy="0" r="3"/>', 'not reached', 560)}
      </g></svg>`;
  }

  registerView('/milestones', async (el) => {
    // A chart, not prose: it takes the window's width rather than the column
    // the reading pages keep to.
    el.classList.add('wide');
    el.innerHTML = '<h1>Milestones</h1><p class="lede">Reading…</p>';
    const data = await api.get('/milestones');

    const tracks = data.projects.filter(p => p.has_status).flatMap(tracksOf);
    tracks.sort((a, b) => (a.archived - b.archived) || (b.reach - a.reach)
      || a.project.localeCompare(b.project));
    const missing = data.projects.filter(p => !p.has_status);

    if (!tracks.length) {
      el.innerHTML = `<h1>Milestones</h1>
        <div class="info">No HAARPi project has a status file yet.</div>`;
      return;
    }

    // A column no pathway passes through is left out: the methods review is
    // opt-in, and most projects never take it.
    const cols = COLUMNS.filter(c => tracks.some(t => t.cells[c.key]));
    const width = LABEL_W + cols.length * COL_W + 12;
    const height = HEAD_H + tracks.length * ROW_H;

    el.innerHTML = `
      <h1>Milestones</h1>
      <p class="lede">Each track's pathway to its deliverable, furthest along
        first. From each project's <code>.haarpi/status.json</code>; hover a
        step for its documents.</p>
      <div class="card">
        ${legend()}
        <div class="fig-scroll">
          <svg class="fig-pathways" viewBox="0 0 ${width} ${height}"
               style="min-width:${Math.round(width * 0.75)}px" role="img"
               aria-label="Milestone pathways, one row per track">
            ${header(cols, height)}
            ${tracks.map((t, i) => trackRow(t, i, cols)).join('')}
          </svg>
        </div>
        ${missing.length ? `<p class="muted">No status file yet:
          ${escHtml(missing.map(p => p.name).join(', '))}.</p>` : ''}
      </div>`;
  });
})();
