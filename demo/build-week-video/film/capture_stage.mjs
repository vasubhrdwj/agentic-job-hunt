// Render the walkthrough-act chrome assets used to composite the live footage.
//   node capture_stage.mjs            -> build/stage/{bg,mask,frame,caption_N}.png
import { chromium } from "../../video/node_modules/playwright-core/index.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const HERE = import.meta.dirname;
const OUT = path.resolve(HERE, "..", "build", "stage");
const url = (q) => "file://" + path.join(HERE, "stage.html") + "?" + q;

await fs.mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true,
  args: ["--hide-scrollbars", "--force-color-profile=srgb"] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, colorScheme: "dark" });
const page = await context.newPage();

// Panel geometry must match stage.html (#maskRect / #panelBorder).
const PANEL = { x: 100, y: 238, width: 1720, height: 784 };

async function shot(query, file, transparent, clip) {
  await page.goto(url(query), { waitUntil: "load" });
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 10000 });
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, file), clip: clip || { x: 0, y: 0, width: 1920, height: 1080 }, omitBackground: !!transparent });
  console.log("wrote", file);
}

await shot("mode=bg", "bg.png", false);
await shot("mode=mask", "mask.png", false, PANEL);   // panel-sized: white rounded rect on black (luma = alpha)
await shot("mode=frame", "frame.png", true);
const count = await page.evaluate(() => window.CAPTIONS.length);
for (let i = 0; i < count; i++) await shot(`caption=${i}`, `caption_${i}.png`, true);

await browser.close();
console.log("stage assets:", count, "captions + bg/mask/frame");
