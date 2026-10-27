// Copyright 2026 The Cerium Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.
//
// Renders branding/cerium/logo.svg (Chromium's logo in Cerium's colours) into
// the PNG files that replace Chromium's logos (branding/overlay/, mirroring
// the Chromium source tree) plus a few standalone sizes in branding/cerium/. utils/rebrand.py copies the overlay
// into the source tree at build time, so building needs no renderer.
//
// Only needed after changing the logo:
//   npm install playwright && npx playwright install chromium
//   node branding/render.js
//
// The wordmarks use the Inter Display font (SIL Open Font License), which
// must be installed.

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = __dirname;
// Chromium's own logo (chrome/app/theme/chromium/product_logo*.svg) with its
// colours swapped: #1967D2 -> #8F969D and #669DF6 -> #B8BEC4 (silver-gray),
// #AECBFA -> #F7E7A6 (pale yellow), #1A73E8 -> #F28A2E (orange); white stays.
const LOGO = fs.readFileSync(path.join(ROOT, 'cerium', 'logo.svg'), 'utf8');
const LOGO_ANIMATION =
    fs.readFileSync(path.join(ROOT, 'cerium', 'logo-animation.svg'), 'utf8');

const TEXT_DARK = '#5F6368';   // Same greys Chromium uses for its wordmark.
const TEXT_WHITE = '#FFFFFF';

const dataUrl = (svg) =>
  'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');

// Square logos: [output path, size in pixels].
const LOGOS = [
  ['chrome/app/theme/chromium/product_logo_16.png', 16],
  ['chrome/app/theme/chromium/product_logo_24.png', 24],
  ['chrome/app/theme/chromium/product_logo_48.png', 48],
  ['chrome/app/theme/chromium/product_logo_64.png', 64],
  ['chrome/app/theme/chromium/product_logo_128.png', 128],
  ['chrome/app/theme/chromium/product_logo_256.png', 256],
  ['chrome/app/theme/chromium/linux/product_logo_24.png', 24],
  ['chrome/app/theme/chromium/linux/product_logo_48.png', 48],
  ['chrome/app/theme/chromium/linux/product_logo_64.png', 64],
  ['chrome/app/theme/chromium/linux/product_logo_128.png', 128],
  ['chrome/app/theme/chromium/linux/product_logo_256.png', 256],
  ['chrome/app/theme/default_100_percent/chromium/product_logo_16.png', 16],
  ['chrome/app/theme/default_100_percent/chromium/product_logo_32.png', 32],
  ['chrome/app/theme/default_100_percent/chromium/linux/product_logo_16.png', 16],
  ['chrome/app/theme/default_100_percent/chromium/linux/product_logo_32.png', 32],
  ['chrome/app/theme/default_200_percent/chromium/product_logo_16.png', 32],
  ['chrome/app/theme/default_200_percent/chromium/product_logo_32.png', 64],
  ['components/resources/default_100_percent/chromium/favicon_product.png', 16],
  ['components/resources/default_200_percent/chromium/favicon_product.png', 32],
];

// Logo with the name next to it: [output path, width, height, text colour].
const WORDMARKS = [
  ['chrome/app/theme/default_100_percent/chromium/product_logo_name_22.png', 97, 22, TEXT_DARK],
  ['chrome/app/theme/default_100_percent/chromium/product_logo_name_22_white.png', 97, 22, TEXT_WHITE],
  ['chrome/app/theme/default_200_percent/chromium/product_logo_name_22.png', 194, 44, TEXT_DARK],
  ['chrome/app/theme/default_200_percent/chromium/product_logo_name_22_white.png', 194, 44, TEXT_WHITE],
  ['components/resources/default_100_percent/chromium/product_logo.png', 171, 32, TEXT_DARK],
  ['components/resources/default_100_percent/chromium/product_logo_white.png', 171, 32, TEXT_WHITE],
  ['components/resources/default_200_percent/chromium/product_logo.png', 342, 64, TEXT_DARK],
  ['components/resources/default_200_percent/chromium/product_logo_white.png', 342, 64, TEXT_WHITE],
];

// Standalone files for packaging and the README.
const STANDALONE = [
  ['cerium/logo_256.png', 256],
  ['cerium/logo_512.png', 512],
];

async function render(page, html, width, height, file) {
  await page.setViewportSize({ width, height });
  await page.setContent(
      `<html><body style="margin:0;background:transparent">${html}</body></html>`);
  await page.evaluate(() => document.fonts.ready);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await page.screenshot({ path: file, omitBackground: true });
}

const img = (svg, size) =>
  `<img src="${dataUrl(svg)}" width="${size}" height="${size}" style="display:block">`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const overlay = path.join(ROOT, 'overlay');

  for (const [file, size] of LOGOS) {
    await render(page, img(LOGO, size), size, size, path.join(overlay, file));
  }
  for (const [file, size] of STANDALONE) {
    await render(page, img(LOGO, size), size, size, path.join(ROOT, file));
  }

  for (const [file, width, height, color] of WORDMARKS) {
    // Logo as tall as the image, then the name, like Chromium's own wordmark.
    const fontSize = Math.round(height * 0.86);
    const html = `
      <div style="display:flex;align-items:center;height:${height}px;width:${width}px;
                  gap:${Math.round(height * 0.24)}px">
        ${img(LOGO, height)}
        <span style="font-family:'Inter Display';font-weight:500;font-size:${fontSize}px;
                     line-height:1;color:${color};letter-spacing:-0.01em;
                     transform:translateY(-${Math.round(height * 0.04)}px)">cerium</span>
      </div>`;
    await render(page, html, width, height, path.join(overlay, file));
  }

  // Vector logos used by internal pages.
  for (const [file, svg] of [
           ['chrome/app/theme/chromium/product_logo.svg', LOGO],
           ['chrome/app/theme/chromium/product_logo_animation.svg', LOGO_ANIMATION]]) {
    fs.mkdirSync(path.dirname(path.join(overlay, file)), { recursive: true });
    fs.writeFileSync(path.join(overlay, file), svg);
  }

  await browser.close();
})();
