import { chromium } from "playwright-core";
import fs from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "../..");
const OUTPUT_NAME = process.env.DEMO_OUTPUT_NAME ?? "product";
const OUTPUT = path.join(import.meta.dirname, "build", OUTPUT_NAME);
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const APP_URL = process.env.DEMO_APP_URL ?? "http://127.0.0.1:3000";
const RESULT_TIMEOUT_MS = Number(process.env.DEMO_RESULT_TIMEOUT_MS ?? "15000");
const COLOR_SCHEME = process.env.DEMO_COLOR_SCHEME ?? "dark";
const FPS = 6;
const FRAME_MS = 100;

await fs.rm(OUTPUT, { recursive: true, force: true });
await fs.mkdir(OUTPUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--hide-scrollbars", "--force-device-scale-factor=1"],
});

const context = await browser.newContext({
  viewport: { width: 1600, height: 900 },
  deviceScaleFactor: 1,
  colorScheme: COLOR_SCHEME,
  reducedMotion: "no-preference",
});
const page = await context.newPage();

let frame = 0;
let capturing = true;
const captureLoop = (async () => {
  while (capturing) {
    const file = path.join(OUTPUT, `frame-${String(frame).padStart(6, "0")}.png`);
    await page.screenshot({ path: file });
    frame += 1;
    await page.waitForTimeout(FRAME_MS);
  }
})();

async function pause(ms) {
  await page.waitForTimeout(ms);
}

async function smoothScroll(y, duration = 1400) {
  await page.evaluate(
    async ({ target, durationMs }) => {
      const start = window.scrollY;
      const distance = target - start;
      const started = performance.now();
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      await new Promise((resolve) => {
        const step = (now) => {
          const progress = Math.min(1, (now - started) / durationMs);
          window.scrollTo(0, start + distance * ease(progress));
          if (progress < 1) requestAnimationFrame(step);
          else resolve();
        };
        requestAnimationFrame(step);
      });
    },
    { target: y, durationMs: duration },
  );
}

await page.goto(APP_URL, { waitUntil: "networkidle" });
await pause(2200);

const resume = await fs.readFile(path.join(ROOT, "fixtures", "sample_resume.txt"), "utf8");
await page.locator("#resume").click();
await page.locator("#resume").fill(resume);
await pause(900);
await smoothScroll(420, 1100);
await pause(800);

await page.locator("#keywords").click();
await page.locator("#keywords").press("Meta+A");
await page.locator("#keywords").type("SCIM, identity, IAM, OIDC", { delay: 38 });
await pause(500);

await page.locator("#locations").click();
await page.locator("#locations").press("Meta+A");
await page.locator("#locations").type("Remote-India, Bengaluru", { delay: 38 });
await pause(600);

await page.getByRole("button", { name: "Run hunt" }).click();
await pause(1800);
await smoothScroll(0, 900);
await page.getByText("Running hunt").waitFor();
await pause(4300);

await page.getByRole("heading", { name: "Hunt review" }).waitFor({ timeout: RESULT_TIMEOUT_MS });
await pause(1900);
await smoothScroll(340, 1100);
await pause(1800);

const score = page.locator('[title^="LLM-judge composite"]').first();
await score.hover();
await pause(1700);

const copy = page.getByRole("button", { name: "Copy" }).first();
await copy.click();
await pause(1100);

await page.getByRole("link", { name: /Log outcomes/ }).click();
await page.getByRole("heading", { name: "Log outcomes" }).waitFor();
await pause(1500);
await page.getByText("Replied", { exact: true }).first().click();
await pause(900);
await page.getByRole("button", { name: "Save outcomes" }).click();
await page.getByText(/Saved 1 outcome/).waitFor();
await pause(1600);

capturing = false;
await captureLoop;
await browser.close();

await fs.writeFile(
  path.join(OUTPUT, "capture.json"),
  JSON.stringify({ fps: FPS, frames: frame, duration_seconds: frame / FPS }, null, 2),
);
console.log(`Captured ${frame} frames (${(frame / FPS).toFixed(1)}s) to ${OUTPUT}`);
