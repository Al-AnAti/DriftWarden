// Render every images/*.svg to images/*.png at 2x.
//
//   node images/src/render.js [name.svg ...]
//
// Uses Playwright's Chromium when the `playwright` package is installed; otherwise falls
// back to a locally installed Edge or Chrome in headless mode (set BROWSER_BIN to point at
// another Chromium-based binary). Either way the PNG is the SVG on its own slate background.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const BG = '#0f172a';
const dir = path.join(__dirname, '..');
const files = process.argv.slice(2).length
  ? process.argv.slice(2)
  : fs.readdirSync(dir).filter((f) => f.endsWith('.svg'));

function size(svg) {
  return { w: +svg.match(/width="(\d+)"/)[1], h: +svg.match(/height="(\d+)"/)[1] };
}

async function withPlaywright(chromium) {
  const browser = await chromium.launch();
  for (const f of files) {
    const svg = fs.readFileSync(path.join(dir, f), 'utf8');
    const { w, h } = size(svg);
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    await page.setContent(`<html><body style="margin:0;background:${BG}">${svg}</body></html>`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(dir, f.replace(/\.svg$/, '.png')), fullPage: false });
    await page.close();
    console.log('rendered', f, w, h);
  }
  await browser.close();
}

function findBrowser() {
  const candidates = [
    process.env.BROWSER_BIN,
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/opt/pw-browsers/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p));
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

// The browser launcher returns before the screenshot is written: wait for the file to settle.
function waitFor(file, ms = 60000) {
  const t0 = Date.now();
  let last = -1;
  while (Date.now() - t0 < ms) {
    if (fs.existsSync(file)) {
      const s = fs.statSync(file).size;
      if (s > 0 && s === last) return true;
      last = s;
    }
    sleep(250);
  }
  return false;
}

function withHeadlessBrowser(bin) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'driftwarden-render-'));
  try {
    for (const f of files) {
      const svg = fs.readFileSync(path.join(dir, f), 'utf8');
      const { w, h } = size(svg);
      const html = path.join(tmp, f.replace(/\.svg$/, '.html'));
      fs.writeFileSync(html, `<!doctype html><html><body style="margin:0;background:${BG}">${svg}</body></html>`);
      const out = path.join(dir, f.replace(/\.svg$/, '.png'));
      fs.rmSync(out, { force: true });
      execFileSync(
        bin,
        [
          '--headless=new',
          '--disable-gpu',
          '--hide-scrollbars',
          '--no-first-run',
          '--no-default-browser-check',
          `--user-data-dir=${path.join(tmp, 'profile')}`,
          '--force-device-scale-factor=2',
          `--window-size=${w},${h}`,
          `--screenshot=${out}`,
          'file:///' + html.replace(/\\/g, '/'),
        ],
        { stdio: 'ignore', timeout: 60000 },
      );
      if (!waitFor(out)) {
        console.error('render.js: no screenshot appeared for', f);
        process.exitCode = 1;
        continue;
      }
      console.log('rendered', f, w, h, '(headless', path.basename(bin) + ')');
    }
  } finally {
    // The browser may still hold its profile open for a moment; the leftovers are tiny.
    try {
      fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch {
      /* ignore */
    }
  }
}

(async () => {
  let chromium = null;
  try {
    ({ chromium } = require('playwright'));
  } catch {
    /* not installed: fall through to a local browser */
  }
  if (chromium) return withPlaywright(chromium);
  const bin = findBrowser();
  if (!bin) {
    console.error('render.js: install the playwright package or set BROWSER_BIN to a Chromium-based browser');
    process.exit(1);
  }
  withHeadlessBrowser(bin);
})();
