import { chromium } from "playwright-core";
import fs from "node:fs/promises";
import path from "node:path";

const OUTPUT = path.join(import.meta.dirname, "build", "trace");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const URL = process.env.DEMO_TRACE_URL ?? "http://127.0.0.1:8000/demo/trace";
const FPS = 6;

await fs.rm(OUTPUT, { recursive: true, force: true });
await fs.mkdir(OUTPUT, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({
  viewport: { width: 1600, height: 900 },
  colorScheme: "dark",
});

let frame = 0;
let capturing = true;
const captureLoop = (async () => {
  while (capturing) {
    await page.screenshot({ path: path.join(OUTPUT, `frame-${String(frame++).padStart(6, "0")}.png`) });
    await page.waitForTimeout(100);
  }
})();

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(2600);

const draft = page.locator(".span").filter({ hasText: "draft_message" }).first();
await draft.click();
await page.waitForTimeout(3300);

await page.getByRole("button", { name: "Evaluation" }).click();
await page.waitForTimeout(2800);

await page.getByRole("button", { name: "Retrieval & exemplars" }).click();
await page.waitForTimeout(3500);

capturing = false;
await captureLoop;
await browser.close();

await fs.writeFile(
  path.join(OUTPUT, "capture.json"),
  JSON.stringify({ fps: FPS, frames: frame, duration_seconds: frame / FPS }, null, 2),
);
console.log(`Captured ${frame} trace frames (${(frame / FPS).toFixed(1)}s) to ${OUTPUT}`);
