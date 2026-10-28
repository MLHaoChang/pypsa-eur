// LC-18 shots: chat table (base + 3, one not comparable), Compare on the
// ranked tab, Compare on Line loading, and Back — in Guided and Expert.
// node shot_lc18.js <scratch> ; reads <scratch>/report.json (the real tool result).
const { chromium } = require('playwright');
const fs = require('fs');
const S = process.argv[2];
const report = JSON.parse(fs.readFileSync(S + '/report.json', 'utf8'));

// The assistant's answer, copied from the tool's own words (labels, units, display strings).
function answer(r) {
  const ok = r.cases.filter(c => c.comparable);
  const head = `| Metric | ${r.base.name} | ${ok.map(c => c.name).join(' | ')} |`;
  const sep = '|' + ' --- |'.repeat(ok.length + 2);
  const rows = r.metrics.filter(m => r.base.values[m.metric].cell).map(m => {
    const cells = ok.map(c => c.values[m.metric].cell ?? '—');
    return `| ${m.header} | ${r.base.values[m.metric].cell} | ${cells.join(' | ')} |`;
  });
  const missing = r.metrics.filter(m => !r.base.values[m.metric].cell).map(m => m.label);
  const lines = [head, sep, ...rows, ''];
  if (missing.length) lines.push(`Not saved for these runs: ${missing.join(', ')}.`, '');
  lines.push('**Not compared:**', ...r.cases.filter(c => !c.comparable).flatMap(c => c.not_comparable.map(t => `- ${t}`)), '');
  const p = r.rank.preference;
  lines.push(`**Cheapest:** ${p.name}. ${p.qualifier}.`, '', `**Why:** ${r.why.facts.join('; ')}.`, '', r.show.offer);
  return lines.join('\n');
}

(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  await require(S + '/shots/login.js')(p);
  const text = answer(report);
  fs.writeFileSync(S + '/shots/answer.md', text);
  for (const mode of ['guided', 'expert']) {
    // 1. The chat table.
    await p.evaluate(async ([mode, report, text]) => {
      const { useChatStore } = await import('/src/store/chatStore.ts');
      const { useUIStore } = await import('/src/store/uiStore.ts');
      useUIStore.setState({ uiMode: mode, assistantDockOpen: true, activeSlidePanel: mode === 'guided' ? 'hubDesign' : null, flowReturn: null });
      useChatStore.setState({ pending: null, messages: [
        { id: 'u1', role: 'user', content: 'Compare all the sensitivities with the base. Which one is cheapest and why?', ts: 1 },
        { id: 't1', role: 'tool', content: '✓ compare_sensitivities', tool_use_id: 'call-1', tool_name: 'compare_sensitivities', ts: 2, tool_result: report },
        { id: 'a1', role: 'assistant', content: text, ts: 3 }] });
    }, [mode, report, text]);
    await p.waitForTimeout(1500);
    await p.screenshot({ path: `${S}/shots/table-${mode}.png` });
    // 2. "Yes": the offered navigation, on the ranked metric's tab.
    for (const [what, tab] of [['ranked', report.show.args.compare_tab], ['loading', 'loading']]) {
      await p.evaluate(async ([args, tab]) => {
        const { applyUiNavigate } = await import('/src/components/uiNavigate.ts');
        applyUiNavigate({ kind: 'navigate', ...args, compare_tab: tab });
      }, [report.show.args, tab]);
      await p.waitForTimeout(3500);
      const ui = await p.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); const s = useUIStore.getState(); return [s.uiMode, s.activeSlidePanel, !!s.flowReturn] });
      console.log(mode, what, ui.join(' '), await p.getByText(/Back to/).count());
      await p.screenshot({ path: `${S}/shots/compare-${what}-${mode}.png` });
      if (what === 'ranked') {
        // reset the origin so the loading jump also returns to the chat view
        await p.evaluate(async (mode) => {
          const { useUIStore } = await import('/src/store/uiStore.ts');
          useUIStore.setState({ activeSlidePanel: mode === 'guided' ? 'hubDesign' : null, flowReturn: null });
        }, mode);
        await p.waitForTimeout(800);
      }
    }
    // 3. Back.
    const back = p.getByRole('button', { name: /Back to/ }).first();
    if (await back.count()) await back.click();
    else await p.evaluate(async () => { const { returnToOrigin } = await import('/src/layout/flowNavigation.ts'); returnToOrigin() });
    await p.waitForTimeout(1500);
    const after = await p.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); const s = useUIStore.getState(); return [s.uiMode, s.activeSlidePanel] });
    console.log(mode, 'back', after.join(' '));
    await p.screenshot({ path: `${S}/shots/back-${mode}.png` });
  }
  await b.close();
})();
