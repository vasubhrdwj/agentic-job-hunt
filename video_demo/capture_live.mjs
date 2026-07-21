import { chromium } from "../demo/video/node_modules/playwright-core/index.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const APP_URL = (process.env.DEMO_APP_URL ?? "https://agentic-job-hunt.vercel.app").replace(/\/$/, "");
const EMAIL = process.env.DEMO_EMAIL;
const PASSWORD = process.env.DEMO_PASSWORD;
const APPLICATION_ID = process.env.DEMO_AMAZON_APPLICATION_ID ?? "1d3abe063dd54e419d20a4bc89c32f24";
const OUTPUT = path.resolve(process.env.DEMO_CAPTURE_DIR ?? "/private/tmp/job-hunt-signal-video/capture");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

if (!EMAIL || !PASSWORD) throw new Error("Set DEMO_EMAIL and DEMO_PASSWORD. Credentials are never written to disk.");
await fs.rm(OUTPUT, { recursive: true, force: true });
await fs.mkdir(OUTPUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--hide-scrollbars", "--force-device-scale-factor=1"],
});
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  colorScheme: "dark",
  reducedMotion: "reduce",
});
const page = await context.newPage();

async function settle() {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1600);
}

async function privacyMask() {
  await page.addStyleTag({ content: `
    input[type="password"] { color: transparent !important; }
    textarea { text-shadow: 0 0 12px currentColor !important; color: transparent !important; }
    * { caret-color: transparent !important; }
  ` });
}

async function goto(route) {
  await page.goto(`${APP_URL}${route}`, { waitUntil: "domcontentloaded", timeout: 45000 });
  await settle();
  await privacyMask();
}

async function anchor(locator, offset = 120) {
  await locator.waitFor({ state: "visible", timeout: 30000 });
  await locator.evaluate((element, topOffset) => {
    const y = element.getBoundingClientRect().top + window.scrollY - Number(topOffset);
    window.scrollTo({ top: Math.max(0, y), behavior: "instant" });
  }, offset);
  await page.waitForTimeout(700);
}

async function shot(name) {
  const file = path.join(OUTPUT, `${name}.png`);
  await page.screenshot({ path: file, animations: "disabled" });
  console.log(`Captured ${file}`);
}

try {
  await goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.pathname.endsWith("/login"), { timeout: 30000 }),
    page.locator("form").getByRole("button", { name: "Sign in" }).click(),
  ]);
  await settle();

  await goto("/profile");
  await anchor(page.getByRole("heading", { name: "Review your profile" }).first(), 110);
  await shot("profile-top");
  await anchor(page.getByRole("heading", { name: "Resume-backed achievements" }).first(), 110);
  await shot("profile-achievements");

  await goto("/today");
  await page.getByRole("heading", { name: "Today", exact: true }).waitFor({ state: "visible" });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await shot("today-top");
  await anchor(page.locator("article").filter({ hasText: /Amazon/i }).first(), 92);
  await shot("today-amazon");

  await goto(`/applications/${APPLICATION_ID}`);
  await page.locator("#application-dossier-title").waitFor({ state: "visible", timeout: 30000 });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await shot("amazon-dossier");
  await anchor(page.locator("#application-pack-title"), 92);
  await shot("amazon-evidence");
  await anchor(page.locator("#application-materials-title"), 92);
  await shot("amazon-materials");
  await anchor(page.locator("#application-people-title"), 92);
  await shot("amazon-people");
  await anchor(page.locator("#application-outreach-title"), 92);
  await shot("amazon-outreach");

  await goto("/review");
  await page.getByRole("heading", { name: "Weekly review", exact: true }).waitFor({ state: "visible", timeout: 30000 });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await shot("weekly-review");

  await fs.writeFile(path.join(OUTPUT, "manifest.json"), JSON.stringify({
    captured_at: new Date().toISOString(), app_url: APP_URL, application_id: APPLICATION_ID,
    viewport: { width: 1920, height: 1080 }, note: "Credentials, cookies, and raw resume contents are not stored."
  }, null, 2));
} finally {
  await browser.close();
}

