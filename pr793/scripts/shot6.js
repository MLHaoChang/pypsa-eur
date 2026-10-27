const { chromium } = require('playwright');
(async () => {
  const S = process.argv[2]; const ids = process.argv.slice(3);
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await require(S + '/login.js')(p);
  await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(3000);
  await p.screenshot({ path: S + '/shots/projects.png' });
  await p.mouse.click(96, 469); await p.waitForTimeout(7000);
  console.log(p.url());
  for (const mode of ['expert', 'guided']) {
    await p.evaluate(async ([mode, ids]) => {
      const { useUIStore } = await import('/src/store/uiStore.ts');
      const { openScenariosHighlight } = await import('/src/components/uiNavigate.ts');
      useUIStore.setState({ uiMode: mode });
      useUIStore.getState().setSlidePanel(null);
      openScenariosHighlight(ids);
    }, [mode, ids]);
    await p.waitForTimeout(2500);
    console.log(mode, await p.locator('[data-testid="scenario-row-highlighted"]').count(), await p.getByText(/Back to/).count());
    await p.screenshot({ path: `${S}/shots/scenarios-${mode}.png` });
  }
  await b.close();
})();
