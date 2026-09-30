/**
 * Diagnostics view — the read-only boundary, made visible.
 *
 * A mount that is writable is rendered as a defect, not a convenience: raDash's
 * boundary is that it writes to nothing it observes, and this page is where
 * that claim is checkable by eye rather than only by test.
 */
registerView('/diagnostics', async () => {
  const [m, cfg] = await Promise.all([api.get('/mounts'), api.get('/config')]);

  const rows = m.mounts.map(x => `
    <tr>
      <td><strong>${escHtml(x.key)}</strong><br><span class="muted">${escHtml(x.what)}</span></td>
      <td><code>${escHtml(x.path)}</code></td>
      <td><span class="badge badge-${escHtml(x.state)}">${escHtml(x.state)}</span></td>
    </tr>`).join('');

  const warn = m.writable_sources.length
    ? `<div class="error"><strong>Defect:</strong> these sources are writable and must not be:
       ${escHtml(m.writable_sources.join(', '))}. Check the <code>:ro</code> flags in
       docker-compose.yml.</div>`
    : `<div class="ok">No source is writable from this process.</div>`;

  return `
    <h1>Diagnostics</h1>
    <p class="lede">What raDash can see, and what it is allowed to do to it.</p>
    ${warn}
    <div class="card">
      <h2>Read-only sources</h2>
      <table>
        <thead><tr><th>Source</th><th>Path</th><th>State</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">A <code>missing</code> mount is not an error — raDash runs
      with sources absent and degrades the panels that need them.</p>
    </div>
    <div class="card">
      <h2>Writable location</h2>
      <table class="kv">
        <tr><th>output</th><td><code>${escHtml(m.output_dir)}</code></td></tr>
      </table>
      <p class="muted">The only place raDash writes. Briefs and corpus fill
      lists land here; nothing is written into a project folder or Zotero.</p>
    </div>
    <div class="card">
      <h2>Configuration</h2>
      <table class="kv">
        <tr><th>trundlr</th><td><code>${escHtml(cfg.trundlr_url)}</code></td></tr>
        <tr><th>OpenAlex authors</th><td><code>${escHtml(cfg.openalex_author_ids.join(', ') || '—')}</code></td></tr>
        <tr><th>contact email</th><td>${escHtml(cfg.contact_email || '—')}</td></tr>
        <tr><th>public CV</th><td>${cfg.cv_url
          ? `<a href="${escHtml(cfg.cv_url)}" target="_blank" rel="noopener noreferrer">${escHtml(cfg.cv_url)}</a>`
          : '<span class="muted">unset — no public claim is compared against the ledger</span>'}</td></tr>
        <tr><th>database</th><td><code>${escHtml(cfg.database_url)}</code></td></tr>
      </table>
      <p class="muted">API keys are read from the environment and never returned
      by the API, masked or otherwise.</p>
    </div>`;
});
