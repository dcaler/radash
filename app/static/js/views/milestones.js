/**
 * Milestones — where each HAARPi project stands, as HAARPi reports it.
 *
 * Every figure here comes from the project's `.haarpi/status.json`, which
 * HAARPi rewrites after each verb. raDash draws it and derives none of it: a
 * milestone is done when its release exists, or *implied* done when something
 * downstream of it is, and that judgement is HAARPi's to make.
 *
 * The layout follows the file. The shared stages run in one line; after the
 * paper's shared rungs each venue is its own track — its outline, draft,
 * submission and deck — because a paper submitted to one venue and being
 * reworked for another is two processes, and one of them has succeeded.
 *
 * The trundlr work in each row is as old as the file, so the file's age sits
 * beside it.
 */
(function () {
  'use strict';

  const STATE_LABEL = {
    done: 'done',
    in_flight: 'in flight',
    open: 'opened, no document yet',
    unlocked: 'unlocked, not opened',
    waiting: 'waiting on an earlier release',
    not_started: 'not started',
  };

  // Whose move it is. `tool` is the one to notice: markup is waiting for a
  // `haarpi next` that nothing has queued, so nothing will happen by itself.
  const WAITING_LABEL = {
    you: 'with you',
    tool: 'needs haarpi next',
    queue: 'queued',
    running: 'running',
  };

  function fileAge(p) {
    const d = p.age_days;
    if (d === null || d === undefined) return 'age unknown';
    return d < 1 / 24 ? 'just now' : d < 1 ? `${Math.round(d * 24)}h ago`
      : `${d.toFixed(0)} days ago`;
  }

  /** `paper.css2026.outline` → `outline`; the track says which venue. */
  function milestoneLabel(m) {
    const parts = m.id.split('.');
    if (parts[0] === 'paper') {
      const last = parts[parts.length - 1];
      return last === 'onepager' ? 'one-pager' : last;
    }
    if (parts[0] === 'deck') return `deck · ${parts.slice(1).join('.')}`;
    return m.id;
  }

  function milestoneChip(m) {
    const tasks = (m.open_tasks || []).map(t =>
      `  ${t.title} (${t.status}, ${t.resource || '?'}${t.hours ? `, ${t.hours.toFixed(1)}h` : ''})`);
    const tip = [
      `${m.id}: ${STATE_LABEL[m.state] || m.state}${m.implied ? ' — implied by later work' : ''}`,
      m.released ? `released: ${m.released.file} (${m.released.date})` : '',
      m.document && (!m.released || m.document !== m.released.file)
        ? `document: ${m.document}` : '',
      m.rounds ? `review rounds: ${m.rounds}` : '',
      m.waiting_on ? `waiting on: ${WAITING_LABEL[m.waiting_on] || m.waiting_on}` : '',
      (m.blocked_by || []).length ? `needs a release from: ${m.blocked_by.join(', ')}` : '',
      (m.stale_inputs || []).length ? `built on an older release of: ${m.stale_inputs.join(', ')}` : '',
      m.target ? `target: ${m.target}` : '',
      m.projected ? `projected: ${m.projected.date} (${m.projected.basis})` : '',
      m.late_by_days ? `released ${m.late_by_days} days after its target` : '',
      tasks.length ? `open on trundlr:\n${tasks.join('\n')}` : '',
    ].filter(Boolean).join('\n');

    const note = m.state === 'done'
      ? (m.implied ? 'implied' : (m.released ? m.released.date : ''))
      : (WAITING_LABEL[m.waiting_on] || '');
    const due = m.target && m.state !== 'done'
      ? ` <small>due ${escHtml(m.target)}</small>` : '';
    const health = m.health === 'late' || m.health === 'at_risk'
      ? ` <small class="ms-flag">${m.health === 'late' ? 'late' : 'at risk'}</small>` : '';
    const stale = (m.stale_inputs || []).length ? ' <small class="ms-flag">stale</small>' : '';
    const rework = m.state !== 'done' && m.released;
    const cls = `ms-chip ms-${m.state}${m.implied ? ' ms-implied' : ''}`
      + `${rework ? ' ms-rework' : ''}${m.waiting_on === 'tool' ? ' ms-stalled' : ''}`;
    return `<span class="${escHtml(cls)}" title="${escHtml(tip)}">${escHtml(milestoneLabel(m))}${note
      ? ` <small>${escHtml(note)}</small>` : ''}${due}${health}${stale}</span>`;
  }

  function ladder(ms) {
    return `<div class="ms-ladder">${ms.map(milestoneChip)
      .join('<span class="ms-arrow">›</span>')}</div>`;
  }

  /** Shared milestones, then one line per venue track. */
  function stagesCell(p) {
    const shared = p.milestones.filter(m => !m.track);
    const venues = [...new Set(p.milestones.filter(m => m.track).map(m => m.track))];
    const outcome = Object.fromEntries((p.tracks || []).map(t => [t.venue, t.outcome]));
    const tracks = venues.map(v => `<div class="ms-track">
        <strong>${escHtml(v)}</strong>${outcome[v]
          ? ` <span class="badge badge-ok">${escHtml(outcome[v])}</span>` : ''}
        ${ladder(p.milestones.filter(m => m.track === v))}</div>`).join('');
    return `${ladder(shared)}${tracks}
      <span class="counts">${escHtml(p.done)} of ${escHtml(p.total)} done
        · ${escHtml(p.released)} with a release on disk${p.current
          ? ` · next: ${escHtml(p.current)}` : ''}</span>`;
  }

  function workCell(p) {
    const rows = [];
    p.milestones.forEach(m => (m.open_tasks || []).forEach(t => rows.push({ t, m })));
    const stamp = `<span class="counts">status.json ${escHtml(fileAge(p))}${p.generated_by
      ? ` · ${escHtml(p.generated_by)}` : ''}${p.trundlr ? '' : ' · trundlr not consulted'}</span>`;
    if (!rows.length) {
      return `<span class="muted">nothing open on trundlr</span><br>${stamp}`;
    }
    const list = rows.slice(0, 4).map(({ t, m }) => `<li>${escHtml(t.title)}
        <span class="counts">${escHtml(milestoneLabel(m))}${m.track ? ` · ${escHtml(m.track)}` : ''}
          · ${escHtml(t.resource || '?')}${t.hours ? ` · ${escHtml(t.hours.toFixed(1))}h` : ''}${m.projected ? ` · projected ${escHtml(m.projected.date)}` : ''}</span>${t.status === 'blocked'
          ? ' <span class="badge badge-stale">blocked</span>' : ''}${t.status === 'failed'
          ? ' <span class="badge badge-error">failed</span>' : ''}</li>`).join('');
    const more = rows.length > 4 ? `<li class="counts">and ${escHtml(rows.length - 4)} more</li>` : '';
    return `<ul class="ms-board">${list}${more}</ul>${stamp}`;
  }

  function projectRow(p) {
    return `
      <tr class="${p.archived ? 'ms-archived' : ''}">
        <td><strong>${escHtml(p.name)}</strong>
          <br><span class="counts">${escHtml(p.folder)}${p.priority
            ? ` · priority ${escHtml(p.priority)}` : ''}${p.archived
            ? ' · archived in trundlr' : ''}</span>${p.note
            ? `<br><span class="badge badge-stale">${escHtml(p.note)}</span>` : ''}</td>
        <td>${p.has_status ? stagesCell(p) : '<span class="muted">no milestones to show</span>'}</td>
        <td>${p.has_status ? workCell(p) : ''}</td>
      </tr>`;
  }

  registerView('/milestones', async (el) => {
    el.innerHTML = '<h1>Milestones</h1><p class="lede">Reading…</p>';
    const data = await api.get('/milestones');
    const rows = data.projects;

    if (!rows.length) {
      el.innerHTML = `<h1>Milestones</h1>
        <div class="info">No HAARPi projects found. ${escHtml(data.folders_note || '')}</div>`;
      return;
    }

    const stalled = rows.filter(p => p.milestones.some(m => m.waiting_on === 'tool'));
    const late = rows.filter(p => p.health === 'late' || p.health === 'at_risk');
    const missing = rows.filter(p => !p.has_status).length;

    el.innerHTML = `
      <h1>Milestones</h1>
      <p class="lede">Each HAARPi project's milestones and the trundlr work
        toward them, as HAARPi wrote them in the project's
        <code>.haarpi/status.json</code>.</p>
      <div class="card">
        <p>${escHtml(rows.length)} HAARPi projects.${stalled.length
          ? ` <strong>${escHtml(stalled.length)} waiting on a <code>haarpi next</code>
            nobody has queued:</strong> ${escHtml(stalled.map(p => p.name).join(', '))}.` : ''}${late.length
          ? ` ${escHtml(late.length)} late or at risk against a target:
            ${escHtml(late.map(p => p.name).join(', '))}.` : ''}${missing
          ? ` ${escHtml(missing)} without a status file yet.` : ''}</p>
        <div class="ms-legend">
          <span class="ms-chip ms-done">done</span>
          <span class="ms-chip ms-done ms-implied">implied by later work</span>
          <span class="ms-chip ms-in_flight">in flight</span>
          <span class="ms-chip ms-in_flight ms-rework">reworking after a release</span>
          <span class="ms-chip ms-in_flight ms-stalled">needs haarpi next</span>
          <span class="ms-chip ms-open">opened</span>
          <span class="ms-chip ms-unlocked">unlocked</span>
          <span class="ms-chip ms-waiting">waiting</span>
        </div>
        <p class="muted">A milestone is done when its release exists — the
          document HAARPi mints once every comment is resolved, named with no
          initials (<code>260728_consGateII_litreview.docx</code>) — and
          <em>implied</em> when it has no document of its own but something
          downstream is done. After the paper's shared rungs each venue is its
          own track, with its deck; <em>submitted</em> marks a track that
          succeeded, whatever the others are doing. Targets come from
          <code>milestones:</code> in <code>haarpi.yaml</code>. The trundlr work
          is as old as the file, and the file's age is beside it. Hover a
          milestone for its documents and tasks.</p>
      </div>
      <div class="card ms-scroll">
        <table>
          <thead><tr><th>Project</th><th>Milestones</th><th>Open on trundlr</th></tr></thead>
          <tbody>${rows.map(projectRow).join('')}</tbody>
        </table>
      </div>`;
  });
})();
