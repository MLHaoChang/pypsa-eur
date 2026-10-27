const { chromium } = require('playwright');
const card = (cases, solves) => ({ version: 1, kind: 'sweep', tool: 'run_sensitivity_sweep',
  baseline: { project_id: 'p-base', name: 'Campus baseline' }, solves, digest: 'd'.repeat(64),
  says: `${cases.length} sensitivity case${cases.length === 1 ? '' : 's'} from Campus baseline: ${cases.map(c => c.name).join('; ')}; queues ${solves} solve${solves === 1 ? '' : 's'}`, cases });
const rows = [
  { component_class: 'Store', name: 'Battery', field: 'capital_cost', label: 'Capital cost', base: 100000, case: 70000, unit: 'EUR/MW', display: { base: 100, case: 70, unit: 'EUR/kW' } },
  { component_class: 'Line', name: 'L1', field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' },
  { component_class: 'Line', name: 'L2', field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' }];
const one = card([{ name: 'battery-capex-70pct-lines-500', derived: true, existing: false, rows }], 1);
const many = card(['gas-mc-150pct', 'lines-500', 'h2-tank-e-nom-extendable-off', 'battery-capex-70pct'].map((name, i) => ({ name, derived: true, existing: i === 3,
  rows: Array.from({ length: 10 }, (_, k) => ({ component_class: 'Line', name: `L${k + 1}`, field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' })) })), 3);
(async () => {
  const S = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  await require(S + '/login.js')(p);
  for (const [file, mode, c] of [['card-expert-one', 'expert', one], ['card-guided-one', 'guided', one], ['card-expert-capped', 'expert', many], ['card-guided-capped', 'guided', many]]) {
    await p.evaluate(async ([mode, c]) => {
      const { useChatStore } = await import('/src/store/chatStore.ts');
      const { useUIStore } = await import('/src/store/uiStore.ts');
      useUIStore.setState({ uiMode: mode, assistantDockOpen: true });
      useChatStore.setState({ pending: { tool_use_id: 'call-1', tool_name: 'run_sensitivity_sweep',
        args: { baseline_project_id: 'p-base', cases: [] }, safety_tier: 'execution', confirmation_token: 'tok-' + mode + c.solves,
        ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300000, sweep_card: c } });
    }, [mode, c]);
    await p.waitForTimeout(1200);
    const el = p.locator('[data-testid="chat-sweep-card"]').first();
    console.log(file, await el.count());
    await p.screenshot({ path: `${S}/shots/${file}.png` });
  }
  await b.close();
})();
