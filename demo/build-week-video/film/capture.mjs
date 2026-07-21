// Frame-accurate capture of the deterministic film timeline.
//
// Drives window.render(t) once per frame and screenshots the result, so every
// frame is exact and reproducible (no CSS-transition timing to race).
//
//   node capture.mjs --phase intro --duration 30.3 --fps 30 --out build/frames/intro
//
import { chromium } from "../../video/node_modules/playwright-core/index.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const HERE = import.meta.dirname;

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i !== -1 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const phase = arg("phase", "intro");
const duration = parseFloat(arg("duration", "30"));
const fps = parseInt(arg("fps", "30"), 10);
const width = parseInt(arg("width", "1920"), 10);
const height = parseInt(arg("height", "1080"), 10);
const scale = parseFloat(arg("scale", "1"));
const out = path.resolve(arg("out", path.join(HERE, "..", "build", "frames", phase)));
const filmUrl = "file://" + path.join(HERE, "film.html") + `?phase=${phase}`;

await fs.rm(out, { recursive: true, force: true });
await fs.mkdir(out, { recursive: true });

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--hide-scrollbars", "--force-color-profile=srgb"],
});
const context = await browser.newContext({
  viewport: { width, height },
  deviceScaleFactor: scale,
  colorScheme: "dark",
});
const page = await context.newPage();
await page.goto(filmUrl, { waitUntil: "load" });
await page.waitForFunction(() => window.__ready === true, null, { timeout: 15000 });
// Let webfonts/blur settle before the first frame.
await page.waitForTimeout(400);

const frames = Math.ceil(duration * fps);
const clip = { x: 0, y: 0, width, height };
for (let i = 0; i < frames; i++) {
  const t = i / fps;
  await page.evaluate((tt) => window.render(tt), t);
  await page.screenshot({
    path: path.join(out, `frame_${String(i).padStart(5, "0")}.png`),
    clip,
    animations: "disabled",
  });
  if (i % 30 === 0) process.stdout.write(`\r${phase}: ${i + 1}/${frames}`);
}
process.stdout.write(`\r${phase}: ${frames}/${frames} done\n`);
await browser.close();
