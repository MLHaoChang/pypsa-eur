// Package the design as one portable HTML file for attachment/iframe previews.
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const directory = path.dirname(fileURLToPath(import.meta.url));
const publicAssets = path.join(directory, 'reference-assets');
const [template, css, expert, sections, script, font, logo, fontLicense] = await Promise.all([
  readFile(path.join(directory, 'template.html'), 'utf8'),
  readFile(path.join(directory, 'styles.css'), 'utf8'),
  readFile(path.join(directory, 'expert-preview.js'), 'utf8'),
  readFile(path.join(directory, 'sections-preview.js'), 'utf8'),
  readFile(path.join(directory, 'prototype.js'), 'utf8'),
  readFile(path.join(publicAssets, 'fonts/Manrope-Variable.ttf')),
  readFile(path.join(publicAssets, 'gridspine-mark.svg')),
  readFile(path.join(publicAssets, 'fonts/OFL-Manrope.txt'), 'utf8'),
]);
const roseLogo=Buffer.from(logo.toString('utf8').replaceAll('#244d3d','#ff586b').replaceAll('#bee7d7','#1b1521'));
const logoUri = `data:image/svg+xml;base64,${roseLogo.toString('base64')}`;
const fontUri = `data:font/ttf;base64,${font.toString('base64')}`;
const embeddedCss = css.replace("../../pypsa-gui/frontend/public/fonts/Manrope-Variable.ttf", fontUri);
const escapeHtml = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const mediaNames=['datacenter','microgrid','hydrogen','ammonia','mining'];
const industryMedia=Object.fromEntries(await Promise.all(mediaNames.map(async name=>[name,`data:image/svg+xml;base64,${(await readFile(path.join(directory,'media',name+'.svg'))).toString('base64')}`])));
const fallbackExamples=[
 {id:'eh_datacenter',title:'Data centers',art:'datacenter',note:'50 MW campus · 40 MW connection · UPS, gensets and rooftop PV',staged:'eh_datacenter_staged'},
 {id:'eh_microgrid',title:'Island microgrids',art:'microgrid',note:'20 MW subsea tie · renewables, battery, diesel and hospital demand',staged:'eh_microgrid_staged'},
 {id:'eh_h2_hub',title:'Hydrogen production',art:'hydrogen',note:'120 MW wind · 80 MW PV · 60 MW electrolyser',staged:'eh_h2_hub_staged'},
 {id:'concept_ammonia',title:'Ammonia production',art:'ammonia',note:'50 / 100 / 150 t/day · #418 in development, not merged',status:'IN DEVELOPMENT · #418'},
 {id:'concept_mining',title:'Mining & minerals',art:'mining',note:'Planned after ammonia · template setup required',status:'PLANNED · AFTER AMMONIA'},
];
const fallbackCard=example=>`<article class="demo"><div class="demo-art"><img src="${industryMedia[example.art]}" alt="" width="960" height="600"><span class="media-badge">${example.status??'SINGLE PERIOD + STAGED GROWTH'}</span></div><div class="demo-body"><h3>${example.title}</h3><p>${example.note}</p><div class="example-actions"><button data-demo="${example.id}">${example.staged?'Single-period workflow':'Preview workflow'} →</button>${example.staged?`<button data-demo="${example.staged}">Staged growth <small>2027 / 2030 / 2035</small> →</button>`:''}</div></div></article>`;
const fallbackNav = `<div><p class="nav-label">WORKSPACE</p><nav class="home-nav" aria-label="Home sections"><button class="nav-item active" data-home-tab="overview">Overview</button><button class="nav-item" data-home-tab="examples">Examples</button><button class="nav-item" data-home-tab="projects">My projects</button></nav></div>`;
// Keep the first screen readable if an attachment viewer disables JavaScript.
// The live script replaces this small static fallback with the full design.
const fallbackMain = `<span class="eyebrow">FROM MODEL TO DECISION</span><div class="title-row"><div><h1>Start a guided workflow.</h1><p>Start with an example or build your own model. Then choose activities within energy planning, grid design and dynamic validation.</p></div></div><section class="resume"><div><h3>Data Center Energy Hub</h3><p>Energy & system planning · illustrative workflow</p></div><button class="primary" data-resume="eh_datacenter">Resume workflow →</button></section><section class="own-model"><div><h2>Build your own workflow</h2><p>Start with a generic model and choose the analyses you need.</p></div><button class="primary" data-generic>Configure workflow →</button></section><div class="section-heading"><h2>Or start with an example</h2></div><div class="demo-grid example-library">${fallbackExamples.slice(0,3).map(fallbackCard).join('')}</div><div class="section-heading"><div><h2>Next industry examples</h2><p>Design previews · backend templates are still in development or planned.</p></div></div><div class="demo-grid upcoming-grid">${fallbackExamples.slice(3).map(fallbackCard).join('')}</div><section class="expert-entry"><div><h3>Prefer to work directly with the model?</h3><p>Use the complete Expert workspace.</p></div><button class="secondary" data-view="expert">Enter Expert →</button></section><p id="static-preview-notice" class="state-note">Interactive controls are not running in this viewer. Download this HTML and open it in a browser to use the full preview.</p>`;

const html = template
  .replace('<link rel="stylesheet" href="styles.css">', `<style>${embeddedCss}</style>`)
  .replace('  <script src="expert-preview.js" defer></script>\n', '')
  .replace('  <script src="sections-preview.js" defer></script>\n', '')
  .replace('  <script src="prototype.js" defer></script>\n', '')
  .replaceAll('../../pypsa-gui/frontend/public/gridspine-mark.svg', logoUri)
  .replace('<aside id="sidebar" aria-label="Workspace navigation"></aside>', `<aside id="sidebar" aria-label="Workspace navigation">${fallbackNav}</aside>`)
  .replace('<main id="main" tabindex="-1"></main>', `<main id="main" tabindex="-1">${fallbackMain}</main>`)
  .replace('</body>', `<template id="font-license">${escapeHtml(fontLicense)}</template>\n<script>${('const industryMedia='+JSON.stringify(industryMedia)+';\n'+expert+'\n'+sections+'\n'+script).replace(/<\/script/gi, '<\\/script')}</script>\n</body>`);

assert.ok(!/(?:href|src)="(?!data:|#)[^"]+"/i.test(html), 'Bundled preview has a relative/external resource');
assert.ok(!/url\((?!['"]?data:)/i.test(html), 'Bundled CSS has a relative/external resource');
for (const name of ['GridSpine-modern.html']) await writeFile(path.join(directory, name), html);
process.stdout.write(`Portable preview built (${Buffer.byteLength(html)} bytes); styles, script, five original industry illustrations, logo, font and font license embedded.\n`);
