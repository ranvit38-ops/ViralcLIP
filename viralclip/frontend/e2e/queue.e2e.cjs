/* eslint-disable @typescript-eslint/no-require-imports -- plain Node CommonJS script */
/*
 * Browser test of the Link Queue page, the dashboard "This week" panel and the clip editor's chat-spike reason.
 * Runs against the mock API until the real endpoints land:
 *   PORT=8140 node e2e/mock-api.cjs
 *   BACKEND_URL=http://127.0.0.1:8140 npm run build && BACKEND_URL=http://127.0.0.1:8140 npx next start -p 3140
 *   BASE=http://localhost:3140 MOCK=http://127.0.0.1:8140 OUT=/tmp/vc-queue node e2e/queue.e2e.cjs
 * Against a real stack, drop MOCK: a fresh user is registered and the checks that need the mock's seed data or
 * controls are skipped.
 */
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://localhost:3140";
const MOCK = process.env.MOCK;
const OUT = process.env.OUT || "/tmp/vc-queue";
fs.mkdirSync(OUT, { recursive: true });

function check(cond, msg) {
  if (!cond) throw new Error("CHECK FAILED: " + msg);
  console.log("ok -", msg);
}
const TW = MOCK ? "https://www.twitch.tv/videos/111" : "https://www.twitch.tv/videos/111111111"; // real VOD ids are longer
const mock = (p, json) => fetch(MOCK + p, { method: "POST", body: JSON.stringify(json ?? {}) });

(async () => {
  if (MOCK) await mock("/__mock/reset");
  const browser = await chromium.launch();
  const errors = [];
  const ctx = await browser.newContext();
  if (!MOCK) {
    const r = await ctx.request.post(`${BASE}/api/auth/register`, { data: { email: `e2e-${Date.now()}@example.com`, password: "password123" } });
    check(r.ok(), "registered a fresh user on the real stack");
  }
  const run = async (width, name) => {
    const page = await ctx.newPage();
    await page.setViewportSize({ width, height: 900 });
    page.on("console", (m) => {
      // 404s from endpoints the mock doesn't implement are expected; anything else is a bug
      if (m.type() === "error" && !/Failed to load resource.*404/.test(m.text())) errors.push(`${name} ${page.url()} :: ${m.text()}`);
    });
    page.on("pageerror", (e) => errors.push(`${name} ${page.url()} :: PAGEERROR ${e.message}`));
    const shot = (n) => page.screenshot({ path: path.join(OUT, `${n}-${name}.png`), fullPage: true });
    const noHScroll = async (what) => check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: ${what} has no horizontal scroll`);
    return { page, shot, noHScroll };
  };

  // --- desktop: full flow
  {
    const { page, shot, noHScroll } = await run(1440, "desktop");
    await page.goto(`${BASE}/`);
    await page.getByRole("link", { name: "Link Queue" }).click();
    await page.waitForSelector("text=Add links");
    check(await page.getByText("clipped one at a time").isVisible(), "queue page explains itself");

    const links = [
      MOCK ? "https://www.youtube.com/watch?v=aaa" : "https://www.youtube.com/watch?v=QQQQQQQQQQ1",
      TW,
      MOCK ? "https://www.tiktok.com/@me/video/123" : "https://www.tiktok.com/@me/video/7300000000000000123",
      "https://example.com/not-a-video",
      MOCK ? "https://www.youtube.com/watch?v=someone-else" : "https://www.youtube.com/watch?v=QQQQQQQQQQ2",
    ];
    await page.getByLabel("Links, one per line").fill(links.join("\n"));
    check(await page.getByText("5 links").isVisible(), "counts pasted links");
    await page.getByRole("button", { name: "Add to queue" }).click();
    if (!MOCK) {
      // the real API accepts other people's videos here and skips them when their turn comes (ownership needs the platform)
      await page.waitForSelector("text=4 added to the queue.");
      check(await page.getByText("1 not added").isVisible(), "shows 1 rejected");
      check(await page.getByText("Paste a Twitch VOD, YouTube video or TikTok link.").isVisible(), "rejected reason shown");
      check(await page.getByText("waiting", { exact: true }).count() === 4, "4 waiting chips");
    } else {
    await page.waitForSelector("text=3 added to the queue.");
    check(await page.getByText("2 not added").isVisible(), "shows 2 rejected");
    check(await page.getByText("Not a YouTube, Twitch or TikTok video link").isVisible(), "rejected reason 1 shown");
    check(await page.getByText("Not from a channel you connected").isVisible(), "rejected reason 2 shown");
    check((await page.getByLabel("Links, one per line").inputValue()).split("\n").length === 2, "only rejected links stay in the box");
    check(await page.getByText("waiting", { exact: true }).count() === 3, "3 waiting chips");
    check(await page.getByText("started", { exact: true }).count() === 1, "started chip");
    check(await page.getByText("Video is private").isVisible(), "item note shown");
    check(await page.getByRole("link", { name: "Open project" }).count() === 2, "project links for items with project_id");
    check(await page.getByRole("button", { name: "Remove https://www.twitch.tv/videos/222" }).count() === 0, "started item cannot be removed");
    }
    await shot("queue");

    await page.getByRole("button", { name: `Remove ${TW}` }).click();
    await page.waitForFunction(() => !document.body.innerText.includes("twitch.tv/videos/111"));
    check(await page.getByText("waiting", { exact: true }).count() === (MOCK ? 2 : 3), "removed one waiting item");

    // buffer days persists through PUT /api/automation
    const days = page.getByLabel("Buffer (days)");
    await days.fill("5");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await page.waitForSelector("text=Saved");
    await page.reload();
    await page.waitForSelector("text=Add links");
    check((await page.getByLabel("Buffer (days)").inputValue()) === "5", "queue_buffer_days persisted");
    await noHScroll("queue");

    // dashboard panel, links still waiting -> no banner
    await page.getByRole("link", { name: "Dashboard" }).click();
    await page.waitForSelector("text=This week");
    check(await page.getByText("Links waiting").isVisible(), "dashboard: links waiting tile");
    if (MOCK) {
      check(await page.getByText("Render failed: ffmpeg exited 1").isVisible(), "dashboard: failure listed");
      check(await page.getByText("mystream").isVisible(), "dashboard: chat channel listed");
    } else {
      check(await page.getByText("Twitch chat recording").count() === 0, "dashboard: chat part hidden with no Twitch channel");
    }
    check(await page.getByText("Add more links.").count() === 0, "dashboard: no banner while links wait");

    if (MOCK) {
      // empty the queue's waiting items -> add_more_links -> banner
      const q = await (await page.request.get(`${BASE}/api/queue`)).json();
      for (const i of q.items.filter((x) => x.status === "waiting")) await page.request.delete(`${BASE}/api/queue/${i.id}`);
      await page.reload();
      await page.waitForSelector("text=Add more links.");
      check(await page.getByRole("link", { name: "Open Link Queue" }).getAttribute("href") === "/links", "banner links to queue");
      await shot("dashboard");
      await noHScroll("dashboard");

      // chat endpoint missing -> chat part hidden, rest still there
      await mock("/__mock/chat", { off: true });
      await page.reload();
      await page.waitForSelector("text=This week");
      check(await page.getByText("Twitch chat recording").count() === 0, "dashboard: chat part hidden when /api/chat/status 404s");
      await mock("/__mock/chat", { off: false });

      await page.goto(`${BASE}/clips/1`);
      await page.waitForSelector("text=Twitch chat spiked");
      const spike = page.getByText("Twitch chat spiked at 10:04 (4.2x the usual rate).").first();
      check(await spike.isVisible(), "clip editor: chat spike reason visible without opening details");
      check(!(await spike.textContent()).includes("Ends on"), "clip editor: spike sentence split from the other reasons");
      await shot("clip");
    }
    await page.close();
  }

  // --- phone width
  {
    const { page, shot, noHScroll } = await run(390, "phone");
    await page.goto(`${BASE}/links`);
    await page.waitForSelector("text=Add links");
    await noHScroll("queue");
    await shot("queue");
    await page.goto(`${BASE}/`);
    await page.waitForSelector("text=This week");
    await noHScroll("dashboard");
    await shot("dashboard");
    if (MOCK) {
      await page.goto(`${BASE}/clips/1`);
      await page.waitForSelector("text=Twitch chat spiked");
      await noHScroll("clip editor");
      await shot("clip");
    }
    await page.close();
  }

  await browser.close();
  if (errors.length) { console.log(errors.join("\n")); throw new Error(`${errors.length} console error(s)`); }
  console.log("all checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
