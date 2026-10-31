/* Rebuild the offline HTML and PDF from the canonical architecture.md. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

function loadMarked() {
  const bundled = path.join(path.dirname(process.execPath), '..', 'node_modules', 'marked');
  for (const candidate of ['marked', bundled]) {
    try { return require(candidate).marked; } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND') throw error;
    }
  }
  throw new Error('The Markdown renderer is missing. Install marked with npm, then rerun this script.');
}

const docs = __dirname;
const source = fs.readFileSync(path.join(docs, 'architecture.md'), 'utf8');
const css = fs.readFileSync(path.join(docs, 'guide_export.css'), 'utf8');
const htmlPath = path.join(docs, 'behavioral_grader_guide.html');
const pdfPath = path.join(docs, 'behavioral_grader_guide.pdf');
const escapeHtml = text => text.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function renderFlow(code) {
  const nodes = new Map();
  for (const match of code.matchAll(/\b([A-P])\[([^\]]+)\]/g)) {
    if (!nodes.has(match[1])) nodes.set(match[1], match[2]);
  }
  return `<div class="flow" role="list" aria-label="Processing sequence">${[...nodes.values()].map((label, index) =>
    `<div class="flow-step" role="listitem"><span>${String(index + 1).padStart(2, '0')}</span><b>${escapeHtml(label)}</b></div>`
  ).join('')}</div>`;
}

const mermaidAsHtml = source.replace(/```mermaid\r?\n([\s\S]*?)```/g, (_all, code) => renderFlow(code));
const marked = loadMarked();
let article = marked.parse(mermaidAsHtml, { gfm: true });
article = article.replace(/^<h1>[\s\S]*?<\/h1>\s*/, '');
article = article.replace(/<h2>(\d+)\. /g, (_all, number) =>
  `<h2 id="section-${number}"><span class="section-number">${number}</span> `);
const sections = [...source.matchAll(/^## (\d+)\. (.+)$/gm)];
const navigation = sections.map(([, number, name]) =>
  `<a href="#section-${number}">${number}. ${escapeHtml(name)}</a>`).join('\n');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Behavioral Grader - Learning and Architecture Guide</title>
<style>${css}</style></head><body>
<header class="hero"><div class="hero-inner"><div class="eyebrow">Technical field guide</div>
<h1>Behavioral grader<span class="accent">.</span><br><strong>From upload to decision.</strong></h1>
<p>A practical walkthrough of the inputs, two AI stages, validation, persistence, outputs, and limits of the local application.</p>
</div></header>
<div class="toolbar"><span class="toolbar-note">Offline reference &middot; Content generated from architecture.md</span><button class="print-button" type="button" onclick="window.print()">Print or save as PDF</button></div>
<main class="layout"><aside class="sidebar" aria-label="Table of contents"><h2>On this page</h2><nav>${navigation}</nav></aside>
<article class="article">${article}</article></main>
<footer class="endnote">Behavioral Grader learning guide. Source of truth: architecture.md. No third-party logos, imagery, fonts, or copy are included.</footer>
</body></html>`;
fs.writeFileSync(htmlPath, html, 'utf8');
console.log(`Wrote ${htmlPath}`);

const chromeCandidates = process.platform === 'win32' ? [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
] : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
const chrome = process.env.GUIDE_BROWSER || chromeCandidates.find(candidate => fs.existsSync(candidate));
if (!chrome) throw new Error('HTML was built, but no Chrome/Edge executable was found for the PDF export.');
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'behavioral-guide-print-'));
let result;
try {
  result = spawnSync(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
    `--user-data-dir=${profile}`, '--no-pdf-header-footer',
    `--print-to-pdf=${pdfPath}`, pathToFileURL(htmlPath).href
  ], { encoding: 'utf8', timeout: 120000 });
} finally {
  // Delete only the temporary profile this invocation created.
  const tempRoot = fs.realpathSync(os.tmpdir());
  const actualProfile = fs.realpathSync(profile);
  if (path.dirname(actualProfile) !== tempRoot || !path.basename(actualProfile).startsWith('behavioral-guide-print-')) {
    throw new Error('Refusing to remove an unexpected browser profile path.');
  }
  try { fs.rmSync(actualProfile, { recursive: true, force: true }); }
  catch (error) { console.warn(`Temporary browser profile could not be removed: ${error.message}`); }
}
if (result.status !== 0 || !fs.existsSync(pdfPath)) {
  throw new Error(`PDF export failed: ${result.stderr || result.stdout || `exit ${result.status}`}`);
}
console.log(`Wrote ${pdfPath}`);
