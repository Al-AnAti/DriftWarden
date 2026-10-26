// Render every out/*.svg to out/*.png at 2x with the pre-installed Chromium.
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
(async () => {
  const dir = path.join(__dirname, '..');
  const files = process.argv.slice(2).length ? process.argv.slice(2) : fs.readdirSync(dir).filter(f => f.endsWith('.svg'));
  // usage: node images/src/render.js [name.svg ...]
  const browser = await chromium.launch();
  for (const f of files) {
    const svg = fs.readFileSync(path.join(dir, f), 'utf8');
    const w = +svg.match(/width="(\d+)"/)[1], h = +svg.match(/height="(\d+)"/)[1];
    const transparent = f.startsWith('logo');
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    await page.setContent(`<html><body style="margin:0;background:${transparent ? 'transparent' : '#0b1120'}">${svg}</body></html>`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(dir, f.replace(/\.svg$/, '.png')), omitBackground: transparent, fullPage: false });
    await page.close();
    console.log('rendered', f, w, h);
  }
  await browser.close();
})();
