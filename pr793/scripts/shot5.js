const { chromium } = require('playwright');
(async () => {
  const S = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await require(S + '/login.js')(p);
  const ids = await p.evaluate(async () => {
    const { CSRF_HEADER, readCsrfToken } = await import('/src/api/csrf.ts');
    const H = { 'Content-Type': 'application/json', [CSRF_HEADER]: readCsrfToken() };
    const j = async (r) => { const t = await r.text(); try { return JSON.parse(t) } catch { return t } };
    const base = await j(await fetch('/api/projects/' + encodeURIComponent('Campus baseline'), { method: 'POST', headers: H }));
    const out = [JSON.stringify(base).slice(0, 200)];
    for (const n of ['battery-capex-70pct-lines-500', 'gas-mc-150pct']) {
      const r = await j(await fetch('/api/projects/' + encodeURIComponent('Campus baseline') + '/scenarios', { method: 'POST', headers: H, body: JSON.stringify({ name: n, description: 'Harness sensitivity scenario' }) }));
      out.push(r.id || JSON.stringify(r).slice(0, 200));
    }
    return out;
  });
  console.log(ids.join('\n'));
  await b.close();
})();
