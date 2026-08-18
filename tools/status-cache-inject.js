/**
 * Adds a "Cache:" row to the ChatGPT desktop /status panel.
 *
 * Loaded into the Electron MAIN process via NODE_OPTIONS=--require, so nothing
 * inside /Applications/ChatGPT.app is modified and the code signature stays intact.
 *
 * The app already receives cachedInputTokens per turn but only forwards
 * {percent, usedTokens, contextWindow} to the status panel. This reads the same
 * numbers from the session rollout JSONL the app writes, and injects a row.
 *
 * Unsupported. Relies on the panel's DOM, which minified builds may change.
 */

'use strict';

// Only run in the Electron main process; NODE_OPTIONS also reaches helpers/children.
if (process.type !== 'browser') return;

let electron;
try {
  electron = require('electron');
} catch {
  return; // not an Electron main process after all
}

const { app, BrowserWindow } = electron;
if (!app || typeof app.whenReady !== 'function') return;

const fs = require('fs');
const path = require('path');
const os = require('os');

const SESSIONS = path.join(os.homedir(), '.codex', 'sessions');
const POLL_MS = 4000;
const TAIL_BYTES = 512 * 1024;

function newestRollout(dir, best = { file: null, mtime: 0 }) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return best;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      best = newestRollout(full, best);
    } else if (e.name.startsWith('rollout-') && e.name.endsWith('.jsonl')) {
      try {
        const { mtimeMs } = fs.statSync(full);
        if (mtimeMs > best.mtime) best = { file: full, mtime: mtimeMs };
      } catch {}
    }
  }
  return best;
}

/** Read only the tail of the rollout and return the last token-usage snapshot. */
function readStats() {
  const { file } = newestRollout(SESSIONS);
  if (!file) return null;

  let buf;
  try {
    const fd = fs.openSync(file, 'r');
    const { size } = fs.fstatSync(fd);
    const len = Math.min(size, TAIL_BYTES);
    buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, size - len);
    fs.closeSync(fd);
  } catch {
    return null;
  }

  const lines = buf.toString('utf8').split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line.startsWith('{') || !line.includes('total_token_usage')) continue;
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      continue; // first line of a tail read is usually truncated
    }
    const info = rec?.payload?.info;
    if (!info?.total_token_usage) continue;

    const total = info.total_token_usage;
    const last = info.last_token_usage || {};
    const tIn = total.input_tokens || 0;
    const tCached = total.cached_input_tokens || 0;
    const lIn = last.input_tokens || 0;
    const lCached = last.cached_input_tokens || 0;

    return {
      hitRate: tIn ? (100 * tCached) / tIn : 0,
      cached: tCached,
      fresh: Math.max(0, tIn - tCached),
      totalInput: tIn,
      lastHitRate: lIn ? (100 * lCached) / lIn : null,
    };
  }
  return null;
}

/** Runs inside the renderer. Watches for the status panel and adds a row. */
function rendererBootstrap() {
  if (window.__cacheRowInstalled) return;
  window.__cacheRowInstalled = true;

  const fmt = (v) => Math.round(v).toLocaleString();
  const MARK = 'data-cache-row';

  function valueText() {
    const s = window.__cacheStats;
    if (!s) return null;
    let text = `${s.hitRate.toFixed(1)}% hit (${fmt(s.cached)} cached / ${fmt(s.totalInput)} in)`;
    if (s.lastHitRate != null) text += ` · turn ${s.lastHitRate.toFixed(0)}%`;
    return text;
  }

  // The row whose label is "Context:" is our anchor and structural template.
  function findContextLabel(root) {
    const nodes = root.querySelectorAll('div,span,p,td,dt,label');
    for (const el of nodes) {
      if (el.children.length) continue; // leaf only
      const t = (el.textContent || '').trim();
      if (t === 'Context:' || t === 'Context') return el;
    }
    return null;
  }

  function inject() {
    const text = valueText();
    if (!text) return;

    const label = findContextLabel(document);
    if (!label) return;

    const row = label.parentElement;
    if (!row || row.parentElement?.querySelector(`[${MARK}]`)) return;

    const clone = row.cloneNode(true);
    clone.setAttribute(MARK, '1');

    const leaves = [...clone.querySelectorAll('div,span,p,td,dt,label')].filter(
      (el) => !el.children.length && (el.textContent || '').trim()
    );
    if (leaves.length < 2) return;

    leaves[0].textContent = 'Cache:';
    leaves[1].textContent = text;
    for (const extra of leaves.slice(2)) extra.textContent = '';

    row.parentElement.insertBefore(clone, row.nextSibling);
  }

  const observer = new MutationObserver(() => {
    try {
      inject();
    } catch {}
  });
  observer.observe(document.body, { childList: true, subtree: true });
  try {
    inject();
  } catch {}
}

function attach(contents) {
  if (!contents || contents.__cacheWired) return;
  contents.__cacheWired = true;

  const push = () => {
    if (contents.isDestroyed()) return;
    const stats = readStats();
    if (!stats) return;
    contents
      .executeJavaScript(`window.__cacheStats = ${JSON.stringify(stats)};`, true)
      .catch(() => {});
  };

  contents.on('dom-ready', () => {
    contents
      .executeJavaScript(`(${rendererBootstrap.toString()})();`, true)
      .then(push)
      .catch(() => {});
  });

  const timer = setInterval(push, POLL_MS);
  contents.on('destroyed', () => clearInterval(timer));
}

app.whenReady().then(() => {
  for (const w of BrowserWindow.getAllWindows()) attach(w.webContents);
  app.on('browser-window-created', (_e, w) => attach(w.webContents));
});
