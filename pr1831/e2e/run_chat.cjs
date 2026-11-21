const { chromium } = require('playwright');
const fs = require('fs'); const os = require('os');
const S = process.argv[2];
const setTheme = (p, t) => p.evaluate(t => { document.documentElement.dataset.theme = t; }, t);
(async () => {
  const pw = fs.readFileSync(os.homedir() + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim();
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  const p = await ctx.newPage();
  let frames = [];
  p.on('response', async r => { const u = r.url(); if (u.includes('/api/chat')) { try { const t = await r.text(); frames.push({ url: u, status: r.status(), body: t.slice(0, 300000) }); } catch (e) {} } });
  await p.goto('http://127.0.0.1:5173/');
  await p.fill('input[type=email]', 'admin@example.com');
  await p.fill('input[type=password]', pw);
  await p.keyboard.press('Enter');
  await p.waitForURL(/projects/); await p.waitForTimeout(5000);
  await p.locator('a', { hasText: 'Open acpf_e2e' }).first().click({ force: true });
  await p.waitForTimeout(8000);
  console.log('url', p.url());
  const st = await p.request.get('http://127.0.0.1:5173/api/results/ac_pf/status');
  console.log('acpf after open', st.status(), (await st.text()).slice(0, 300));
  console.log('sim', (await (await p.request.get('http://127.0.0.1:5173/api/simulation/status')).text()).slice(0, 200));
  await p.screenshot({ path: S + '/after_open.png' });

  async function ask(tag, msg) {
    frames = [];
    const ta = p.locator('textarea').last();
    if (!(await ta.isVisible())) { console.log('textarea hidden; trying to open dock'); }
    await ta.click(); await ta.fill(msg); await p.keyboard.press('Enter');
    const card = p.locator('[data-testid="checked-evidence-card"]').last();
    try { await card.waitFor({ timeout: 240000 }); } catch (e) { console.log(tag, 'no card', e.message); }
    await p.waitForTimeout(4000);
    if (await card.count()) {
      const open = await card.evaluate(d => d.open);
      if (!open) await card.locator('summary').click();
      await p.waitForTimeout(800); await card.scrollIntoViewIfNeeded();
      console.log(`[${tag}] CARD TEXT:\n` + await card.innerText());
      console.log(`[${tag}] WARN ROWS:`, JSON.stringify(await card.locator('[data-testid="checked-warning-slot"]').allInnerTexts()));
      for (const t of ['light', 'dark']) {
        await setTheme(p, t); await p.waitForTimeout(500);
        await p.screenshot({ path: `${S}/${tag}_${t}.png` });
        await card.screenshot({ path: `${S}/${tag}_card_${t}.png` });
      }
      await setTheme(p, 'light');
    } else { await p.screenshot({ path: `${S}/${tag}_nocard.png` }); }
    fs.writeFileSync(`${S}/${tag}_frames.json`, JSON.stringify(frames, null, 1));
    const ids = new Set(); for (const f of frames) for (const m of f.body.matchAll(/"answer_id"\s*:\s*"([^"]+)"/g)) ids.add(m[1]);
    for (const id of ids) {
      const r = await p.request.get('http://127.0.0.1:5173/api/chat/answers/' + encodeURIComponent(id));
      const t = await r.text(); fs.writeFileSync(`${S}/${tag}_receipt_${id.replace(/[^\w-]/g, '_')}.json`, t);
      console.log(`[${tag}] answer ${id} -> ${r.status()} saved`);
    }
    // companion: collapse dock
    const col = p.getByRole('button', { name: 'Collapse the assistant' }).first();
    if (await col.count() && await col.isVisible()) {
      await col.click(); await p.waitForTimeout(2500);
      const co = p.locator('[data-testid="companion-outcome"]').last();
      if (await co.count()) {
        console.log(`[${tag}] COMPANION:`, JSON.stringify(await co.innerText()));
        for (const t of ['light', 'dark']) { await setTheme(p, t); await p.waitForTimeout(500);
          await p.screenshot({ path: `${S}/${tag}_companion_${t}.png` }); }
        await setTheme(p, 'light');
      } else { console.log(`[${tag}] no companion-outcome after collapse`); await p.screenshot({ path: `${S}/${tag}_collapsed.png` }); }
      // reopen dock
      const reopen = p.getByRole('button', { name: /assistant|chat/i }).first();
      await p.locator('textarea').last().isVisible() || await reopen.click().catch(() => {});
      await p.waitForTimeout(1500);
    }
  }
  await ask('warn', 'Show AC branch loading for line Line_BC');
  const nc = p.getByRole('button', { name: 'Start a new chat' }).first();
  if (await nc.count() && await nc.isVisible()) { await nc.click(); await p.waitForTimeout(1500); }
  else console.log('new chat button not visible');
  await ask('clean', 'Show AC branch loading for line Line_BC; from 2030-01-01T00:00:00 to 2030-01-01T01:00:00');
  const lf = p.getByRole('tab', { name: /load flow/i }).or(p.getByRole('button', { name: /^load flow$/i })).or(p.getByRole('link', { name: /^load flow$/i })).first();
  if (await lf.count()) { await lf.click(); await p.waitForTimeout(4000); await p.screenshot({ path: S + '/loadflow_tab.png' }); console.log('loadflow shot'); }
  else console.log('no load flow tab found');
  await b.close();
})();
