/**
 * Frontier — what is being published in your regions that you do not have.
 *
 * Two honesties carried on every row.
 *
 * **Distance.** Each paper was found by one of the region's OpenAlex topics
 * and then placed in your own fitted space, and the distance between where it
 * landed and the region's centre is the check that the topic was a good proxy
 * for the region. An OpenAlex topic is much broader than one of these regions
 * — the solar region resolves to "Energy and Environment Impacts", which is
 * most of energy research — so a paper marked off-target was found by a topic
 * this region only nominally has.
 *
 * **Age.** Each set is dated and nothing is fetched on load. A frontier feed
 * depends on somebody else's API being up, which makes it the panel where
 * stale-and-labelled most clearly beats absent.
 */
(function () {
  'use strict';

  function itemRow(i) {
    const d = i.distance === null || i.distance === undefined
      ? '<span class="muted">—</span>'
      : `${i.distance.toFixed(3)}${i.off_target
          ? ' <span class="badge badge-stale">off-target</span>' : ''}`;
    return `
      <tr>
        <td><a href="${escHtml(i.url)}" target="_blank" rel="noopener noreferrer"
              >${escHtml(i.title)}</a>
          ${i.venue ? `<br><span class="counts">${escHtml(i.venue)}</span>` : ''}</td>
        <td class="num">${escHtml(i.year ?? '—')}</td>
        <td class="num">${escHtml(i.cited_by ?? '—')}</td>
        <td class="num">${d}</td>
      </tr>`;
  }

  function region(r) {
    const name = r.name || (r.terms || []).slice(0, 3).join(' · ')
      || `region ${r.cluster}`;
    const topics = (r.topics || []).map(t =>
      `${escHtml(t.name)} <span class="counts">${(t.share * 100).toFixed(0)}% of members</span>`
    ).join(' · ');
    if (!r.items.length) {
      return `<div class="card">
        <h2>${escHtml(name)}</h2>
        <p class="muted">Nothing gathered yet.${r.topics.length ? ''
          : ' No OpenAlex topic describes enough of this region to query on,'
            + ' so it was not asked about rather than guessed at.'}</p>
      </div>`;
    }
    const off = r.items.filter(i => i.off_target).length;
    return `<div class="card">
      <h2>${escHtml(name)}
        <span class="counts">${r.age_days === null ? 'age unknown'
          : `${r.age_days.toFixed(0)} days old`}</span></h2>
      <p class="muted">Found through ${topics || 'no resolved topic'}.
        ${off ? `${escHtml(off)} of ${escHtml(r.items.length)} landed outside
        this region and are marked; they were found by a topic broader than the
        region is.` : 'All of these landed inside the region.'}</p>
      <table>
        <thead><tr><th>Paper</th><th class="num">Year</th>
          <th class="num">Cited</th><th class="num">Distance</th></tr></thead>
        <tbody>${r.items.map(itemRow).join('')}</tbody>
      </table>
    </div>`;
  }

  async function render(el) {
    el.innerHTML = '<h1>Frontier</h1><p class="lede">Reading…</p>';
    const [data, reading] = await Promise.all([
      api.get('/frontier'), api.get('/status/reading?limit=25'),
    ]);

    if (!data.regions.length) {
      const P0 = window.raPanels;
      el.innerHTML = `<h1>Frontier</h1>
        <div class="info">${escHtml(data.note || 'No regions yet.')}
        Fit the map first — regions are resolved to OpenAlex topics by the
        papers already in them.</div>
        <div class="viz-tip" hidden></div>
        ${P0 ? P0.readingList(reading) : ''}`;
      if (P0) P0.wireHover(el);
      return;
    }

    const gathered = data.regions.filter(r => r.items.length).length;
    const P = window.raPanels;
    el.innerHTML = `
      <h1>Frontier</h1>
      <p class="lede">What is being published in your regions that you do not
      have — and, from it and your own backlog together, what to read next.</p>
      <div class="viz-tip" hidden></div>
      ${P ? P.readingList(reading) : ''}
      <div class="card">
        <p>${escHtml(gathered)} of ${escHtml(data.regions.length)} regions carry
        a gathered set.</p>
        <p class="muted">Each paper is found through a region's OpenAlex topics
        and then placed in your own fitted space. <strong>Distance</strong> is
        how far it landed from the region's centre, and it is the check that
        the topic was a fair proxy for the region: an OpenAlex topic is much
        broader than one of these regions, so a topic covering most of a field
        returns that field's news rather than yours. Past twice the region's
        own spread a paper is marked <strong>off-target</strong> — found by one
        of the region's topics, but it did not land in the region.</p>
        <p class="muted">Gathering goes to the regions you are working in
        first, not the ones you collected most of: size is a record of the
        past, and a direction you have retired can be the largest region on
        the map. A region with no project brief and no recent publication is
        left until last and reported as dormant. Papers already in your
        library are left out of the per-region sets below — they are backlog,
        and they rank beside these in <em>What to read next</em>, above.</p>
        <div class="actions">
          <button class="action" id="gather-btn">Gather</button>
          <span class="counts out"></span>
        </div>
        <p class="muted">Gathering is one polite request per region, paced.
        Nothing is fetched when this page loads.</p>
      </div>
      ${data.regions.map(region).join('')}`;

    if (P) P.wireHover(el);

    const btn = el.querySelector('#gather-btn');
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
  }

  registerView('/frontier', async (el) => { await render(el); });
})();
