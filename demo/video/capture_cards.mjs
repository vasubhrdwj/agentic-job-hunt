import { chromium } from "playwright-core";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const OUTPUT = path.join(import.meta.dirname, "build", "cards");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const URL = pathToFileURL(path.join(import.meta.dirname, "cards.html")).href;

await fs.mkdir(OUTPUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

for (const card of ["title", "loop", "close"]) {
  await page.goto(`${URL}?card=${card}`);
  await page.screenshot({ path: path.join(OUTPUT, `${card}.png`) });
}

await browser.close();
console.log(`Captured title cards to ${OUTPUT}`);
