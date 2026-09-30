/**
 * Status — what changed, and what the numbers rest on.
 *
 * Difference leads. The cadence is weekly and nothing here rewards watching,
 * so the levels live beside the things they describe — citation accrual on
 * Your work, regions on the Landscape — and this page answers the only
 * question a weekly glance can: what is not the same as last week, and how
 * much of the picture is missing.
 */
(function () {
  'use strict';

  registerView('/status', async (el) => {
    el.innerHTML = '<h1>Status</h1><p class="lede">Reading…</p>';
    const s = await api.get('/status');
    const P = window.raPanels;

    el.innerHTML = `
      <h1>Status</h1>
      <p class="lede">What changed, and what the numbers rest on.
        ${s.build ? `<span class="build-stamp">build
        <code>${escHtml(s.build.version)}</code>, up since
        ${escHtml(s.build.started_at)}</span>` : ''}</p>
      <div class="viz-tip" hidden></div>
      ${P.changes(s.changes)}
      ${P.headline(s.headline)}
      ${P.drift(s.drift)}
      ${P.coverage(s.coverage)}`;
    P.wireHover(el);
  });
})();
