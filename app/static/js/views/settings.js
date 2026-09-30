/**
 * Settings — the configuration that is a judgement rather than a deployment
 * fact.
 *
 * Every value shows where it came from, because the alternative is a number
 * you cannot account for. `you` means an override stored here; `environment`
 * means the Portainer stack; `default` means the value raDash shipped with.
 * An override can always be handed back.
 *
 * What is absent is deliberate and explained on the page: source paths mirror
 * Docker bind mounts and must not diverge from them, and secrets are never
 * returned by the API at all.
 */
(function () {
  'use strict';

  function field(s) {
    const id = `set-${s.key}`;
    const origin = {
      you: '<span class="badge badge-stale">set by you</span>',
      environment: '<span class="badge badge-ok">from the environment</span>',
      default: '<span class="badge badge-missing">shipped default</span>',
    }[s.source] || '';
    return `
      <div class="setting" data-key="${escHtml(s.key)}">
        <label for="${id}"><strong>${escHtml(s.label)}</strong></label> ${origin}
        ${s.affects_ledger
          ? '<span class="badge badge-missing">rebuild after changing</span>' : ''}
        <p class="muted">${escHtml(s.help)}</p>
        ${s.kind === 'toggle'
          ? `<label class="toggle"><input id="${id}" type="checkbox"
               ${String(s.value).toLowerCase() === 'on' ? 'checked' : ''}>
             <span>${String(s.value).toLowerCase() === 'on' ? 'on' : 'off'}</span></label>`
          : `<input id="${id}" type="text" value="${escHtml(s.value ?? '')}"
               inputmode="${s.kind === 'number' ? 'decimal' : 'text'}">`}
        <div class="actions">
          <button class="action save-btn">Save</button>
          ${s.source === 'you'
            ? `<button class="action revert-btn">Use ${
                 s.environment ? 'the environment value' : 'the default'}</button>` : ''}
          <span class="counts out"></span>
        </div>
        ${s.source === 'you' && (s.environment || s.default)
          ? `<p class="counts">underneath: <code>${escHtml(s.environment || s.default)}</code></p>`
          : ''}
      </div>`;
  }

  async function render(el) {
    el.innerHTML = '<h1>Settings</h1><p class="lede">Reading…</p>';
    const data = await api.get('/settings');

    el.innerHTML = `
      <h1>Settings</h1>
      <p class="lede">Configuration that is a judgement, not a deployment fact.</p>
      ${data.groups.map(g => `
        <div class="card">
          <h2>${escHtml(g.name)}</h2>
          ${g.settings.map(field).join('')}
        </div>`).join('')}
      <div class="card">
        <h2>Not editable here</h2>
        <table class="kv">
          ${Object.entries(data.not_editable).map(([k, why]) =>
            `<tr><th>${escHtml(k.replace(/_/g, ' '))}</th><td>${escHtml(why)}</td></tr>`
          ).join('')}
        </table>
        <p class="muted">These are set in the Portainer stack's environment and
        take effect on redeploy.</p>
      </div>`;

    el.querySelectorAll('.setting').forEach(wireSetting);
  }

  function wireSetting(box) {
    const key = box.dataset.key;
    const input = box.querySelector('input');
    const out = box.querySelector('.out');
    const save = box.querySelector('.save-btn');
    const revert = box.querySelector('.revert-btn');

    // A checkbox's `value` is the string "on" whether or not it is ticked,
    // so reading it would save "on" for both states and the toggle would
    // never turn anything off.
    const current = () => (input.type === 'checkbox'
      ? (input.checked ? 'on' : 'off') : input.value);

    if (input.type === 'checkbox') {
      const label = box.querySelector('.toggle span');
      input.addEventListener('change', () => {
        if (label) label.textContent = input.checked ? 'on' : 'off';
      });
    }

    save.addEventListener('click', async () => {
      save.disabled = true;
      out.textContent = 'saving…';
      try {
        const r = await api.put(`/settings/${encodeURIComponent(key)}`,
                                { value: current() });
        out.textContent = r.note ? `saved — ${r.note}` : 'saved';
      } catch (err) {
        out.textContent = `rejected: ${err.message}`;
      }
      save.disabled = false;
    });

    if (revert) {
      revert.addEventListener('click', async () => {
        revert.disabled = true;
        try {
          const r = await api.del(`/settings/${encodeURIComponent(key)}`);
          if (input.type === 'checkbox') {
            input.checked = String(r.value).toLowerCase() === 'on';
          } else {
            input.value = r.value ?? '';
          }
          out.textContent = `now using the ${r.source} value`;
        } catch (err) {
          out.textContent = `failed: ${err.message}`;
          revert.disabled = false;
        }
      });
    }
  }

  registerView('/settings', async (el) => { await render(el); });
})();
