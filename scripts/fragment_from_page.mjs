/**
 * fragment_from_page.mjs — generate a W3C text fragment from a PAGE BODY.
 *
 * Called by cite_proof.deeplink_for. Given a cached body and a quote, it locates
 * the quote in the parsed document and hands that DOM Range to Chrome's own
 * generator (`generateFragmentFromRange`, GoogleChromeLabs text-fragments-
 * polyfill — the code Chromium iOS and the Link-to-Text extension ship).
 *
 * WHY THIS EXISTS (t786, four times over): cite_proof's matcher folds the dash
 * family and the quote family, so a quote typed with ASCII '-' or a straight
 * apostrophe proves `present` against a page serving U+2013 / U+2019. The old
 * deeplink was then built from THE TYPED QUOTE, shipping characters the page
 * does not contain, and Chromium — which folds neither — highlighted nothing
 * while every presence check stayed green. Building the fragment from the page's
 * own bytes makes that class inexpressible: the typed quote never reaches the URL.
 *
 * PROTOCOL. argv[2] is a JSON job file (NOT argv — the quote is external input
 * and must never touch a command line):
 *     {url, quote, htmlPath, timeoutMs}
 * One JSON object on stdout:
 *     {ok:true, status:"SUCCESS", fragment:{textStart,textEnd,prefix,suffix}}
 *     {ok:false, error:"<reason>"}
 * Any failure is a clean {ok:false} — the caller falls back to the whole-quote
 * deeplink, so a bad generation degrades to the old behaviour and never to a
 * fabricated link.
 *
 * SAFETY. The document is built with jsdom's DEFAULTS: page scripts are not
 * executed and no external subresource is ever loaded. Bodies here come from
 * arbitrary hosts, several of them already recorded as hostile to automation —
 * do NOT add options that turn either of those on.
 */
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);

function out(obj) {
  process.stdout.write(JSON.stringify(obj));
  process.exit(0);
}

/** Module roots to try, most specific first. ESM `import` IGNORES NODE_PATH —
 *  that variable is a CJS `require` mechanism — so the roots are resolved by
 *  hand and fed to both loaders. */
function moduleRoots() {
  const roots = [path.join(import.meta.dirname, 'node_modules')];
  for (const p of (process.env.NODE_PATH || '').split(path.delimiter)) {
    if (p) roots.push(p);
  }
  if (process.env.APPDATA) roots.push(path.join(process.env.APPDATA, 'npm', 'node_modules'));
  return roots;
}

function loadJsdom() {
  try { return require('jsdom'); } catch { /* not on the default path */ }
  for (const root of moduleRoots()) {
    try { return require(path.join(root, 'jsdom')); } catch { /* next root */ }
  }
  return null;
}

function generatorPath() {
  const rel = ['text-fragments-polyfill', 'src', 'fragment-generation-utils.js'];
  for (const root of moduleRoots()) {
    const p = path.join(root, ...rel);
    try { readFileSync(p); return p; } catch { /* next root */ }
  }
  return null;
}

// ----------------------------------------------------------------- locating
// Folds only as far as cite_proof.match_norm does, and ONLY to find the quote.
// The fragment itself is built from the page's characters, so nothing folded
// here can reach the URL.
const DASHES = /[‐‑‒–—−]/;
const CURLY = { '‘': "'", '’': "'", '‚': "'", '‛': "'",
                '“': '"', '”': '"', '„': '"', '‟': '"' };

function foldChar(ch) {
  if (DASHES.test(ch)) return '-';
  if (CURLY[ch]) return CURLY[ch];
  return ch.normalize('NFKC').toLowerCase();
}

/** Fold `s`, returning {out, map} where map[i] is the index in `s` of out[i]. */
function foldWithMap(s) {
  let folded = '', map = [], lastWasSpace = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (/\s/.test(ch)) {
      if (!lastWasSpace && folded.length) { folded += ' '; map.push(i); lastWasSpace = true; }
      continue;
    }
    lastWasSpace = false;
    for (const f of foldChar(ch)) { folded += f; map.push(i); }
  }
  // Punctuation-adjacent space fold (cite_proof t582: tag boundaries inject
  // spaces the rendered page never shows). Keeps the map aligned.
  let o2 = '', m2 = [];
  const isPunct = c => c !== undefined && /[^\w\s]/.test(c);
  for (let i = 0; i < folded.length; i++) {
    if (folded[i] === ' ' && (isPunct(o2[o2.length - 1]) || isPunct(folded[i + 1]))) continue;
    o2 += folded[i]; m2.push(map[i]);
  }
  return { out: o2, map: m2 };
}

const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD']);

function indexTextNodes(doc) {
  const walker = doc.createTreeWalker(doc.body, 4 /* SHOW_TEXT */, {
    acceptNode(n) {
      for (let p = n.parentNode; p; p = p.parentNode) {
        if (p.nodeType === 1 && SKIP.has(p.tagName)) return 2 /* REJECT */;
      }
      return 1 /* ACCEPT */;
    },
  });
  let raw = '', nodes = [], n;
  while ((n = walker.nextNode())) {
    const t = n.nodeValue;
    if (!t) continue;
    nodes.push({ node: n, start: raw.length, end: raw.length + t.length });
    raw += t;
  }
  return { raw, nodes };
}

function locate(doc, quote) {
  const { raw, nodes } = indexTextNodes(doc);
  const { out: folded, map } = foldWithMap(raw);
  const needle = foldWithMap(quote).out;
  if (!needle) return null;
  const at = folded.indexOf(needle);
  if (at < 0) return null;
  const rawStart = map[at];
  const rawEnd = map[at + needle.length - 1] + 1;
  const find = (idx, isEnd) => {
    for (const e of nodes) {
      if (isEnd ? (idx > e.start && idx <= e.end) : (idx >= e.start && idx < e.end)) {
        return { node: e.node, offset: idx - e.start };
      }
    }
    return null;
  };
  const s = find(rawStart, false), e = find(rawEnd, true);
  if (!s || !e) return null;
  const range = doc.createRange();
  range.setStart(s.node, s.offset);
  range.setEnd(e.node, e.offset);
  return range;
}

// ---------------------------------------------------------------------- run
let job;
try {
  job = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
} catch (err) {
  out({ ok: false, error: 'unreadable job file: ' + String(err).slice(0, 120) });
}

const jsdomMod = loadJsdom();
if (!jsdomMod) out({ ok: false, error: 'jsdom not installed in any known module root' });
const fguPath = generatorPath();
if (!fguPath) out({ ok: false, error: 'text-fragments-polyfill not installed' });

let dom;
try {
  dom = new jsdomMod.JSDOM(readFileSync(job.htmlPath, 'utf-8'), { url: job.url });
} catch (err) {
  out({ ok: false, error: 'parse failed: ' + String(err).slice(0, 160) });
}

// The library reads bare globals (window/document/Node/Range/...).
const w = dom.window;
for (const k of ['window', 'document', 'Node', 'NodeFilter', 'Range', 'Text',
                 'Element', 'HTMLElement', 'getComputedStyle', 'DOMRect']) {
  if (w[k] === undefined) continue;
  try { globalThis[k] = w[k]; }
  catch { Object.defineProperty(globalThis, k, { value: w[k], configurable: true }); }
}

const range = locate(w.document, job.quote || '');
if (!range) out({ ok: false, error: 'quote not located in the body' });

let fgu;
try {
  fgu = await import(pathToFileURL(fguPath).href);
} catch (err) {
  out({ ok: false, error: 'generator import failed: ' + String(err).slice(0, 160) });
}

try {
  // The library's own budget, inside the caller's subprocess timeout. Two
  // clocks on purpose: this one returns a clean TIMEOUT status, the outer one
  // survives a wedge this one cannot see (the 8.8 MB XBRL body).
  if (typeof fgu.setTimeout === 'function') fgu.setTimeout(job.timeoutMs || 20000);
  const res = fgu.generateFragmentFromRange(range);
  const name = Object.entries(fgu.GenerateFragmentStatus || {})
    .find(([, v]) => v === res.status);
  if (!res.fragment) out({ ok: false, error: 'status ' + (name ? name[0] : res.status) });
  out({ ok: true, status: name ? name[0] : String(res.status), fragment: res.fragment });
} catch (err) {
  out({ ok: false, error: 'generate threw: ' + String(err).slice(0, 160) });
}
