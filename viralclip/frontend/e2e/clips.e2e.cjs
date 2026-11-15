/* eslint-disable @typescript-eslint/no-require-imports -- plain Node CommonJS script */
/*
 * End-to-end browser test of the clip workflow: register -> upload -> find clips -> review ->
 * edit (captions, framing, title, hashtags, caption text) -> render -> download -> Generated Clips
 * filters/search/zip -> Settings persistence -> phone-width layout checks.
 *
 * Needs a running stack (API + worker + Next) with the Whisper "tiny" model available:
 *   cd backend && VC_DATA_DIR=/tmp/vc uvicorn app.main:app --port 8100
 *   cd backend && VC_DATA_DIR=/tmp/vc python -m app.worker
 *   cd frontend && BACKEND_URL=http://127.0.0.1:8100 npm run build && BACKEND_URL=http://127.0.0.1:8100 npx next start -p 3100
 * Then:
 *   BASE=http://localhost:3100 VIDEO=/path/to/speech.mp4 OUT=/tmp/shots node e2e/clips.e2e.cjs
 * VIDEO must contain speech (e.g. espeak-ng output muxed onto a test pattern, ~60 s).
 * Exits non-zero on the first failed check; prints console errors seen in the browser.
 */
const { chromium } = require("playwright");
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE || "http://localhost:3100";
const VIDEO = process.env.VIDEO;
const OUT = process.env.OUT || "/tmp/vc-e2e";
fs.mkdirSync(OUT, { recursive: true });

function check(cond, msg) {
  if (!cond) throw new Error("CHECK FAILED: " + msg);
  console.log("ok -", msg);
}

let page;
(async () => {
  if (!VIDEO) throw new Error("Set VIDEO=/path/to/speech.mp4");
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(`${page.url()} :: ${m.text()}`));
  page.on("pageerror", (e) => errors.push(`${page.url()} :: PAGEERROR ${e.message}`));
  const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });

  // --- register
  await page.goto(`${BASE}/login`);
  await page.getByText("No account? Create one").click();
  await page.fill("input[type=email]", `e2e-${Date.now()}@example.com`);
  await page.fill("input[type=password]", "password123");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForSelector("text=Recent projects");

  // --- settings first, so the generate panel picks them up
  await page.getByRole("link", { name: "Settings" }).click();
  await page.waitForSelector("text=Clip defaults");
  await page.getByLabel("Clip length").selectOption("15–30 s");
  await page.getByLabel("Number of clips").fill("3");
  await page.getByLabel("Tone").fill("casual");
  await page.getByLabel("Hashtags per clip").fill("4");
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.waitForSelector("text=Settings saved.");
  await page.reload();
  await page.waitForSelector("text=Clip defaults");
  check((await page.getByLabel("Number of clips").inputValue()) === "3", "settings: clip count persisted");
  check((await page.getByLabel("Tone").inputValue()) === "casual", "settings: tone persisted");
  check((await page.getByLabel("Clip length").inputValue()) === "15–30 s", "settings: length persisted");
  await shot("settings");

  // --- upload
  await page.getByRole("link", { name: "Create Project" }).click();
  await page.setInputFiles("input[type=file]", VIDEO);
  await page.getByLabel("Project name").fill("E2E talk");
  await page.getByRole("button", { name: "Upload" }).click();
  await page.waitForURL(/projects\/\d+$/, { timeout: 120000 });
  const projectUrl = page.url();

  // --- generate clips
  await page.getByRole("button", { name: "Find clips" }).waitFor();
  check((await page.getByLabel("Number of clips").inputValue()) === "3", "generate panel: default count from settings");
  check((await page.getByLabel("Clip length").inputValue()) === "15–30 s", "generate panel: default length from settings");
  await page.getByLabel("Transcription model").selectOption("tiny");
  await page.getByRole("button", { name: "Find clips" }).click();
  await page.waitForSelector("text=Processing");
  await shot("project-processing");
  await page.waitForSelector("text=/Score\\s*\\d+/", { timeout: 300000 });
  await page.waitForSelector("text=Transcript");
  const rows = await page.getByRole("button", { name: /^Preview / }).count();
  check(rows >= 1, `review list shows ${rows} clip(s)`);
  check(await page.getByText("Generate clips again").isVisible(), "generate panel offers re-run with transcript");
  check(await page.getByText(/Transcribe again/).isVisible(), "re-transcribe option visible once a transcript exists");

  // transcript seek
  const seg = page.locator("button", { hasText: /mistake/i }).first();
  await seg.click();
  await page.waitForTimeout(500);
  const t = await page.locator("video").first().evaluate((v) => v.currentTime);
  check(t > 1, `transcript click seeks the source video (t=${t.toFixed(1)})`);

  // score details
  await page.locator("summary", { hasText: "Score" }).first().click();
  check(await page.getByText("not a prediction of views").first().isVisible(), "score breakdown explains it's not a view prediction");

  // preview
  await page.getByRole("button", { name: /^Preview / }).first().click();
  check(await page.locator("video[src*='#t=']").count() >= 1, "clip preview plays the source segment");
  await shot("project-clips");

  // approve first clip
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await page.waitForSelector("text=approved");
  check(true, "approve from review list");

  // --- clip editor
  await page.getByRole("link", { name: "Edit" }).first().click();
  await page.waitForSelector("text=Trim");
  const editorUrl = page.url();
  await page.getByRole("button", { name: "1:1", exact: true }).click();
  await page.locator("button", { hasText: "karaoke" }).click();
  await page.getByLabel("Line 1 text").fill("EDITED caption line");
  const title = `E2E title ${Date.now() % 10000}`;
  await page.getByLabel(/^Title ·/).fill(title);
  await page.getByPlaceholder("Add hashtag (Enter)").fill("#e2etag");
  await page.keyboard.press("Enter");
  const firstTag = await page.getByRole("button", { name: /^Remove #/ }).first().getAttribute("aria-label");
  await page.getByRole("button", { name: /^Remove #/ }).first().click();
  check(await page.getByText("Unsaved changes").isVisible(), "editor shows unsaved state");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForSelector("text=All changes saved.");
  await page.reload();
  await page.waitForSelector("text=Trim");
  check((await page.getByLabel(/^Title ·/).inputValue()) === title, "title persisted");
  check(await page.getByText("#e2etag").isVisible(), "added hashtag persisted");
  check(!(await page.getByText(firstTag.replace("Remove ", ""), { exact: true }).count()), "removed hashtag stays removed");
  check((await page.getByLabel("Line 1 text").inputValue()) === "EDITED caption line", "caption text edit persisted");
  check(await page.getByText("edited", { exact: true }).isVisible(), "caption lines marked edited");
  await shot("editor");

  // render
  await page.getByRole("button", { name: /^(Render|Save & render)$/ }).click();
  await page.waitForSelector("text=Download MP4", { timeout: 300000 });
  const videoHref = await page.getByRole("link", { name: "Download MP4" }).getAttribute("href");
  const dl = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "Download MP4" }).click()]);
  const file = path.join(OUT, "clip.mp4");
  await dl[0].saveAs(file);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_streams", file]).toString());
  const vs = probe.streams.find((s) => s.codec_type === "video");
  check(vs.width === 1080 && vs.height === 1080, `downloaded render is 1:1 1080x1080 (${vs.width}x${vs.height})`);
  check(probe.streams.some((s) => s.codec_type === "audio"), "render has audio");
  execFileSync("ffmpeg", ["-v", "error", "-y", "-ss", "0.5", "-i", file, "-frames:v", "1", path.join(OUT, "render-frame.jpg")]);
  check(videoHref.includes("download=true"), "download link uses attachment mode");
  await shot("editor-rendered");

  // metadata regenerate
  await page.getByRole("button", { name: "Hashtags", exact: true }).click();
  await page.waitForTimeout(1500);
  check(await page.getByText(/hashtags?\./).first().isVisible(), "regenerate hashtags returns without error");
  check((await page.getByLabel(/^Title ·/).inputValue()) === title, "edited title kept");

  // trim -> outdated
  await page.getByRole("button", { name: "Start +1s" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForSelector("text=Render again");
  check(await page.getByText("out of date").isVisible(), "trim marks render outdated");
  check(!(await page.getByText("Each caption line needs").count()), "trim does not leave a spurious caption-timing error");
  check(+(await page.getByLabel("Line 1 start").inputValue()) >= 0, "caption times are clamped inside the trimmed clip");

  // --- generated clips page
  await page.getByRole("link", { name: "Generated Clips" }).click();
  await page.waitForSelector("text=Every clip across your projects.");
  await page.waitForSelector("main a[href^='/clips/']");
  const all = await page.getByRole("checkbox", { name: /^Select (?!all$)/ }).count();
  check(all >= 1, `generated clips grid shows ${all} clip(s)`);
  await page.getByLabel("Review status").selectOption("approved");
  await page.waitForURL(/review_status=approved/);
  await page.waitForTimeout(800);
  check((await page.getByRole("checkbox", { name: /^Select (?!all$)/ }).count()) === 1, "filter by review status");
  await page.getByLabel("Search clips").fill("E2E title");
  await page.waitForURL(/q=E2E/);
  await page.waitForTimeout(800);
  check((await page.getByRole("checkbox", { name: /^Select (?!all$)/ }).count()) === 1, "search by title");
  await page.getByLabel("Search clips").fill("zzzz-nothing");
  await page.waitForSelector("text=No clips match these filters");
  await page.getByText("Clear filters").click();
  await page.waitForTimeout(800);
  check((await page.getByRole("checkbox", { name: /^Select (?!all$)/ }).count()) === all, "clear filters restores grid");
  await page.getByText("Select all").click();
  const zip = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download zip" }).click()]);
  const zipFile = path.join(OUT, "clips.zip");
  await zip[0].saveAs(zipFile);
  check(fs.readFileSync(zipFile).subarray(0, 2).toString() === "PK", "bulk zip download");
  await shot("clips-grid");

  // --- project page bulk render
  await page.goto(projectUrl);
  await page.waitForSelector("text=Select all");
  await page.getByText("Select all").click();
  await page.getByRole("button", { name: "Render selected" }).click();
  await page.waitForSelector("text=/Queued \\d+ render/");
  check(true, "bulk render queued from project page");

  // dashboard link
  await page.getByRole("link", { name: "Dashboard" }).click();
  await page.getByRole("link", { name: /Awaiting review/ }).click();
  await page.waitForURL(/clips\?review_status=suggested/);
  check(true, "dashboard awaiting-review stat links to filtered clips");

  // --- phone width
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, url] of [["m-dashboard", "/"], ["m-project", projectUrl], ["m-editor", editorUrl], ["m-clips", "/clips"], ["m-settings", "/settings"]]) {
    await page.goto(url.startsWith("http") ? url : BASE + url);
    await page.waitForTimeout(1500);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 0, `${name}: no horizontal scroll at 390px (overflow ${overflow}px)`);
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  }

  // Editor with every <details> open and an "Inspired by" list (research provenance injected into the clip response,
  // since this stack has no YouTube key): long truncated titles must not widen the page.
  const clipApi = `${BASE}/api/clips/${editorUrl.split("/").pop()}`;
  await page.route(clipApi, async (route) => {
    const res = await route.fetch();
    const clip = await res.json();
    const long = "A very long similar Short title that keeps going and going so it can never fit on a phone screen at all #money";
    clip.score_breakdown = { ...clip.score_breakdown, metadata_sources: { query: "money", similar: [
      { title: long, views: 1234567, url: "https://www.youtube.com/watch?v=aaaaaaaaaaa" },
      { title: long + " 2", views: 7654, url: "https://www.youtube.com/watch?v=bbbbbbbbbbb" }] } };
    await route.fulfill({ response: res, json: clip });
  });
  await page.goto(editorUrl.startsWith("http") ? editorUrl : BASE + editorUrl);
  await page.locator("summary", { hasText: /Inspired by 2 similar Shorts/ }).waitFor();
  await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
  await page.waitForTimeout(300);
  const detailsOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(detailsOverflow <= 0, `m-editor with Inspired-by and all details open: no horizontal scroll (overflow ${detailsOverflow}px)`);
  await page.screenshot({ path: path.join(OUT, "m-editor-details.png"), fullPage: true });
  await page.unroute(clipApi);

  const real = errors.filter((e) => !/401 \(Unauthorized\)/.test(e));
  console.log(real.length ? "CONSOLE ERRORS:\n" + real.join("\n") : "no console errors");
  await browser.close();
  if (real.length) process.exit(1);
})().catch(async (e) => {
  console.error(e);
  if (page) await page.screenshot({ path: path.join(OUT, "FAILURE.png"), fullPage: true }).catch(() => {});
  process.exit(1);
});
