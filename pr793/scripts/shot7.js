const { chromium } = require('playwright');
const rows = [{ component_class: 'Line', name: 'L1', field: 's_nom', label: 'Rating', base: 300, case: 500, unit: 'MVA' }];
const card = { version: 1, kind: 'sweep', tool: 'run_sensitivity_sweep', baseline: { project_id: 'p-base', name: 'Campus baseline' },
  solves: 0, digest: 'd'.repeat(64), says: '1 sensitivity case from Campus baseline: lines-500; queues 0 solves',
  cases: [{ name: 'lines-500', derived: true, existing: true, rows }] };
const refusal = "✗ run_sensitivity_sweep — sensitivity_name_conflict: A project named 'lines-500' already exists with other inputs. The case needs another name (for example 'lines-500-2').";
(async () => {
  const S = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  await require(S + '/login.js')(p);
  for (const mode of ['expert', 'guided']) {
    for (const what of ['nosolve', 'refusal', 'link']) {
      await p.evaluate(async ([mode, what, card, refusal]) => {
        const { useChatStore } = await import('/src/store/chatStore.ts');
        const { useUIStore } = await import('/src/store/uiStore.ts');
        useUIStore.setState({ uiMode: mode, assistantDockOpen: true });
        const user = { id: 'u1', role: 'user', content: 'Try the lines at 500 MVA as a sensitivity', ts: 1 };
        if (what === 'nosolve') {
          useChatStore.setState({ messages: [user], pending: { tool_use_id: 'call-1', tool_name: 'run_sensitivity_sweep',
            args: { baseline_project_id: 'p-base', cases: [] }, safety_tier: 'execution', confirmation_token: 'tok-ns-' + mode,
            ttl_seconds: 300, expires_at_epoch_ms: Date.now() + 300000, sweep_card: card } });
        } else if (what === 'refusal') {
          useChatStore.setState({ pending: null, messages: [user,
            { id: 't1', role: 'tool', content: refusal, tool_use_id: 'call-2', tool_name: 'run_sensitivity_sweep', ts: 2 },
            { id: 'a1', role: 'assistant', content: "A project named **lines-500** already exists with other inputs. What should I call the new case — for example **lines-500-2**?", ts: 3 }] });
        } else {
          useChatStore.setState({ pending: null, messages: [user,
            { id: 't1', role: 'tool', content: '✓ run_sensitivity_sweep', tool_use_id: 'call-3', tool_name: 'run_sensitivity_sweep', ts: 2,
              tool_result: { baseline_project_id: 'p-base', cases: [{ project_id: 'p-case-1', name: 'lines-500' }, { project_id: 'p-case-2', name: 'battery-capex-70pct' }] } },
            { id: 'a1', role: 'assistant', content: 'I created **lines-500** and **battery-capex-70pct** from Campus baseline and queued their solves. Campus baseline is still the open project.', ts: 3 }] });
        }
      }, [mode, what, card, refusal]);
      await p.waitForTimeout(1200);
      console.log(mode, what, await p.getByText(/Queues no solve|Could not|sensitivity_name_conflict|Open the new cases/).count());
      await p.screenshot({ path: `${S}/shots/${what}-${mode}.png` });
    }
  }
  await b.close();
})();
