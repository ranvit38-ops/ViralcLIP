/* eslint-disable @typescript-eslint/no-require-imports -- plain Node CommonJS script */
/*
 * Throwaway mock of the Link Queue / weekly status / chat status contracts, plus just enough of the rest of the API
 * (auth, dashboard, one clip) for the pages around them to render. Used by e2e/queue.e2e.cjs until the real
 * endpoints land. Unmocked endpoints return 404 (pages must cope with that).
 *   PORT=8140 node e2e/mock-api.cjs
 * Test controls: POST /__mock/chat {"off":true} makes /api/chat/status 404; POST /__mock/reset restores the seed data.
 */
const http = require("http");

const now = () => new Date().toISOString();
let state;
function reset() {
  state = {
    nextId: 4, chatOff: false,
    settings: {
      enabled: true, twitch_account_ids: [], youtube_account_ids: [1], min_source_minutes: 20,
      analyze: { min_len: 20, max_len: 45, count: 8, model: null }, auto_approve: { enabled: false, top_n: 3, min_score: 70 },
      auto_render: true, delete_source_after_days: null, enabled_at: now(), queue_buffer_days: 3,
    },
    items: [
      { id: 1, url: "https://www.youtube.com/watch?v=done1", platform: "youtube", status: "done", note: "8 clips, 6 scheduled", project_id: 7, created_at: now() },
      { id: 2, url: "https://www.twitch.tv/videos/222", platform: "twitch", status: "started", note: "Downloading", project_id: 8, created_at: now() },
      { id: 3, url: "https://www.youtube.com/watch?v=bad", platform: "youtube", status: "failed", note: "Video is private", project_id: null, created_at: now() },
    ],
  };
}
reset();

function platformOf(url) {
  if (/(^|\.)youtube\.com\/|youtu\.be\//.test(url)) return "youtube";
  if (/twitch\.tv\/videos\//.test(url)) return "twitch";
  if (/tiktok\.com\//.test(url)) return "tiktok";
  return null;
}

const clip = {
  id: 1, project_id: 7, project_name: "Stream highlights", start: 600, end: 630, duration: 30,
  transcript_excerpt: "and that is how we won the round", score: 82,
  score_breakdown: { hook: 0.8, standalone: 0.7, energy: 0.9, method: "heuristic" },
  reason: "Strengths: strong opening hook, high vocal energy. Twitch chat spiked at 10:04 (4.2x the usual rate). Ends on a complete thought.",
  review_status: "suggested", title: "How we won the round", title_options: ["How we won the round"], title_edited: false,
  description: "", hashtags: ["gaming"], render_status: "none", caption_overrides: [],
  render_settings: { aspect: "9:16", layout: "auto", crop_x: null, captions: true, preset: "bold", position: "bottom",
    style: { font: "Inter", size: 64, color: "#ffffff", highlight: "#ffcc00", outline: "#000000", outline_width: 4, shadow: 0, uppercase: false, words: 3, box: false, pop: true, bold: true },
    remove_pauses: false, remove_retakes: false, remove_fillers: false, zoom: false, hook: false, hook_text: null },
  video_url: null, thumbnail_url: "", created_at: now(), job: null,
};
const project = { id: 7, name: "Stream highlights", status: "ready", archived: false, error: null, settings: {}, source_filename: "s.mp4",
  upload_size: 1, bytes_received: 1, duration: 3600, width: 1920, height: 1080, fps: 30, video_codec: "h264", audio_codec: "aac",
  has_audio: true, created_at: now(), updated_at: now(), thumbnail_url: null, jobs: [] };

function status() {
  const count = (s) => state.items.filter((i) => i.status === s).length;
  const waiting = count("waiting");
  return {
    enabled: state.settings.enabled,
    queue: { waiting, started: count("started"), done: count("done"), skipped: count("skipped"), failed: count("failed") },
    scheduled_posts: 9, last_scheduled_at: new Date(Date.now() + 1.5 * 86400e3).toISOString(), posts_per_day: 6, runway_days: 1.5,
    buffer_days: state.settings.queue_buffer_days, add_more_links: waiting === 0,
    failures: [
      { kind: "queue", name: "https://www.youtube.com/watch?v=bad", error: "Video is private", project_id: null, at: now() },
      { kind: "project", name: "Stream highlights", error: "Render failed: ffmpeg exited 1", project_id: 7, at: now() },
    ],
  };
}

function route(method, path, body) {
  if (method === "POST" && path === "/__mock/reset") { reset(); return [200, { ok: true }]; }
  if (method === "POST" && path === "/__mock/chat") { state.chatOff = !!body.off; return [200, { ok: true }]; }
  if (path === "/api/auth/me") return [200, { id: 1, email: "e2e@example.com" }];
  if (path === "/api/dashboard") return [200, { projects: 1, clips: 1, clips_awaiting_review: 1, active_jobs: 0, failed_jobs: 0, recent_jobs: [], recent_projects: [project] }];
  if (path === "/api/projects") return [200, [project]];
  if (path === "/api/projects/7") return [200, project];
  if (path === "/api/clips/1") return [200, clip];
  if (path === "/api/clips/1/captions") return [200, { edited: false, lines: [{ start: 0, end: 2, text: "and that is how" }] }];
  if (path === "/api/automation" && method === "GET") return [200, { settings: state.settings, twitch_configured: false, twitch_missing: [], twitch_accounts: [], youtube_accounts: [], tiktok_accounts: [], waiting_review: 0, events: [] }];
  if (path === "/api/automation" && method === "PUT") {
    const d = body.queue_buffer_days;
    if (!Number.isInteger(d) || d < 1 || d > 14) return [422, { detail: "queue_buffer_days must be 1-14" }];
    if (!("analyze" in body)) return [422, { detail: "whole settings object required" }];
    state.settings = body; return [200, state.settings];
  }
  if (path === "/api/automation/status") return [200, status()];
  if (path === "/api/chat/status") {
    if (state.chatOff) return [404, { detail: "Not Found" }];
    return [200, { recording: true, channels: [
      { login: "mystream", recording: true, messages_24h: 1834, last_message_at: now() },
      { login: "altchannel", recording: false, messages_24h: 0, last_message_at: null },
    ] }];
  }
  if (path === "/api/queue" && method === "GET") return [200, { items: state.items, buffer_days: state.settings.queue_buffer_days, automation_enabled: state.settings.enabled }];
  if (path === "/api/queue" && method === "POST") {
    const links = body.links;
    if (!Array.isArray(links) || links.length > 50) return [422, { detail: "Send 1-50 links" }];
    const added = [], rejected = [];
    for (const url of links) {
      const platform = platformOf(url);
      if (!platform) rejected.push({ url, reason: "Not a YouTube, Twitch or TikTok video link" });
      else if (url.includes("someone-else")) rejected.push({ url, reason: "Not from a channel you connected" });
      else if (state.items.some((i) => i.url === url)) rejected.push({ url, reason: "Already in the queue" });
      else {
        const it = { id: state.nextId++, url, platform, status: "waiting", note: null, project_id: null, created_at: now() };
        state.items.push(it); added.push({ id: it.id, url, platform, status: it.status });
      }
    }
    return [200, { added, rejected }];
  }
  const del = method === "DELETE" && path.match(/^\/api\/queue\/(\d+)$/);
  if (del) {
    const it = state.items.find((i) => i.id === +del[1]);
    if (!it) return [404, { detail: "Not found" }];
    if (!["waiting", "skipped", "failed"].includes(it.status)) return [409, { detail: "Only waiting, skipped or failed links can be removed" }];
    state.items = state.items.filter((i) => i !== it);
    return [204, null];
  }
  return [404, { detail: "Not Found" }];
}

http.createServer((req, res) => {
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch { /* empty */ }
    const [code, out] = route(req.method, req.url.split("?")[0], body);
    res.writeHead(code, { "Content-Type": "application/json" });
    res.end(out == null ? "" : JSON.stringify(out));
  });
}).listen(+(process.env.PORT || 8140), "127.0.0.1", () => console.log("mock api on", process.env.PORT || 8140));
