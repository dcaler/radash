/**
 * Hash-based router. View scripts call registerView('/path', fn) where fn
 * receives the #app element and may:
 *   - manage #app.innerHTML directly (return null/undefined), or
 *   - return an HTML string or Promise<string> (router sets innerHTML).
 */
const views = {};

function registerView(path, renderFn) {
  views[path] = renderFn;
}

/** Escape a value for safe insertion into HTML. */
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setActiveNav(hash) {
  document.querySelectorAll('.nav-links a').forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === `#${hash}`);
  });
}

async function navigate() {
  const raw = location.hash.slice(1) || '/status';
  const el = document.getElementById('app');

  const qIdx = raw.indexOf('?');
  const path = qIdx === -1 ? raw : raw.slice(0, qIdx);
  const query = Object.fromEntries(new URLSearchParams(qIdx === -1 ? '' : raw.slice(qIdx + 1)));

  setActiveNav(path);

  const view = views[path] || views['/status'];
  try {
    const out = await view(el, { ...query });
    if (typeof out === 'string') el.innerHTML = out;
  } catch (err) {
    el.innerHTML = `<div class="error">Failed to render: ${escHtml(err.message)}</div>`;
  }
}

/**
 * Theme. The stamp on <html> wins over the operating system in both
 * directions — a light stamp beats OS dark and vice versa — and the choice
 * survives a reload. Storage can throw in a locked-down browser, so every
 * access is guarded and the page renders correctly without it.
 */
function readStoredTheme() {
  try {
    return localStorage.getItem('radash-theme');
  } catch (_) {
    return null;
  }
}

function applyTheme(theme) {
  if (theme) {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  try {
    if (theme) localStorage.setItem('radash-theme', theme);
    else localStorage.removeItem('radash-theme');
  } catch (_) { /* a choice that cannot be stored still applies this visit */ }
}

function currentTheme() {
  return document.documentElement.getAttribute('data-theme')
    || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light');
}

applyTheme(readStoredTheme());

window.addEventListener('hashchange', navigate);
window.addEventListener('DOMContentLoaded', async () => {
  const toggle = document.getElementById('nav-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const links = document.querySelector('.nav-links');
      const open = links.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
  }
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  }
  try {
    const v = await api.get('/version');
    document.getElementById('nav-version').textContent = v.version;
  } catch (_) { /* version is decoration, never block the app */ }
  navigate();
});
