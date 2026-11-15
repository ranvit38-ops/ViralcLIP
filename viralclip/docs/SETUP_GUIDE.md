# ViralClip: complete setup, start to finish

Do the parts in order. Total time is about 1–1.5 hours, mostly clicking through the Google and TikTok websites.
The end result: **Twitch stream → clips → YouTube + TikTok, automatically.**

The app runs on **your computer**. Run `autostart.ps1` once (Part 7) and it keeps running in the background, with no window needed, whenever the PC is on.

---

## Part 1: Install and start ViralClip (15 min)

**Windows**
1. Open **PowerShell** and run:
   ```
   winget install Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg Git.Git
   ```
   Then close and reopen PowerShell.
2. Download the code:
   ```
   git clone -b claude/dreamy-johnson-i1qde4 https://github.com/ranvit38-ops/ViralcLIP.git
   ```
   The repo is private. If git asks you to sign in, a browser window opens; log in with your GitHub account.
   Alternatively, use **GitHub Desktop** → File → Clone → `ranvit38-ops/ViralcLIP` → then switch to branch `claude/dreamy-johnson-i1qde4`.
3. Open the `ViralcLIP\viralclip` folder and **double-click `start.bat`**.
   - The first run takes 5–15 minutes to set up, then opens **http://localhost:3000**.
4. Create your account on that page.

**macOS:** `brew install python@3.12 node ffmpeg git`, clone as in step 2, then run `./start.sh` in `viralclip/`.

Check it works: upload any video with talking, go to **Generate clips** → **Render** a clip → **Download** it.

The first run also creates `viralclip\.env`. That's where every key below goes.
Open it with Notepad. After editing, **close the ViralClip window and double-click `start.bat` again.**

---

## Part 2: Twitch keys (5 min)

1. Twitch → profile picture → **Creator Dashboard → Settings → Stream** → turn on **Store past broadcasts**.
2. Go to <https://dev.twitch.tv/console/apps>, log in (Twitch may ask you to turn on 2-factor), then click **Register Your Application**:
   - Name: anything unique, e.g. `viralclip-yourname`
   - OAuth Redirect URLs: `http://localhost:3000/api/twitch/callback`
   - Category: **Application Integration**. Client type: **Confidential**.
3. Click **Manage**, copy the **Client ID**, then click **New Secret** and copy the secret.
4. In `.env`:
   ```
   TWITCH_CLIENT_ID=paste-here
   TWITCH_CLIENT_SECRET=paste-here
   ```

---

## Part 3: YouTube keys (20 min)

1. Go to <https://console.cloud.google.com> → project picker → **New project** → name it `viralclip` → **Create**. Make sure the new project is selected.
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **OAuth consent screen** (newer consoles call it *Google Auth Platform*):
   - User type **External**. Fill in the app name and your email.
   - **Scopes / Data access** → add `.../auth/youtube.upload` and `.../auth/youtube.readonly`.
   - **Audience / Test users** → add the Google account of **every YouTube channel** you'll post to.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Type: **Web application**
   - Authorized redirect URI: `http://localhost:3000/api/youtube/callback`
   - Copy the **Client ID** and **Client secret**.
5. In `.env`:
   ```
   GOOGLE_CLIENT_ID=paste-here
   GOOGLE_CLIENT_SECRET=paste-here
   ```

**Know this:**
- Until Google **audits** your project, videos it uploads are **private**. Search "YouTube API Services audit and quota extension form" and submit it; it can take weeks.
- In "Testing" mode, YouTube logins expire every 7 days, so you'll reconnect weekly until the app is published or audited.

---

## Part 3b: YouTube research key (optional, 5 min)

ViralClip can look up recent Shorts on a similar topic that got a lot of views (official YouTube Data API v3, `search.list` + `videos.list`; no page scraping) and use their **style patterns and hashtags** when writing your titles and hashtags. Titles still only describe what your clip says. This copies patterns from popular videos; it does **not** guarantee views.

**Why a separate project:** quota is per Google Cloud project, 10,000 units a day by default. One search costs about 101 units, one upload about 1,600. If research used your upload project, 20 searches (2,020 units) would cost you one or two uploads a day. A second project has its own 10,000 units, so research never touches upload quota.

1. <https://console.cloud.google.com> → project picker → **New project** → name it `viralclip-research` → **Create**, and select it.
2. **APIs & Services → Library** → **YouTube Data API v3** → **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key**. Copy it.
4. Click the new key → **API restrictions → Restrict key** → tick only **YouTube Data API v3** → **Save**.
5. In `.env`:
   ```
   YOUTUBE_RESEARCH_API_KEY=paste-here
   ```
   and restart ViralClip. **Settings → AI** shows "YouTube research: on".
6. In **Settings → AI**, set **Niche / game** (e.g. `GTA 6`, `Valorant`, `podcast`) so searches match your content.

**Know this:**
- Up to 90 searches a day with this key (`YOUTUBE_RESEARCH_DAILY_SEARCHES` to change it; 90 × 101 ≈ 9,100 units). Results are cached for 24 hours, so repeated clips on the same topic cost nothing.
- Without the key, ViralClip borrows your connected channel's quota: at most 20 searches a day, and only while today's uploads (done and queued) leave room. Uploads always come first.
- If Google reports the quota is used up, research pauses until midnight Pacific time. Metadata is still written as usual, just without the examples.
- The clip editor shows which Shorts inspired a clip under **"Inspired by N similar Shorts"**.

---

## Part 4: TikTok keys (20 min)

1. Go to <https://developers.tiktok.com> → log in → **Manage apps → Connect an app**.
2. Fill in the app details. TikTok wants a terms-of-service URL and a privacy-policy URL; any page you control works, even a public GitHub page.
3. **Products**: add **Login Kit** (Web) and **Content Posting API** (turn on **Direct Post**).
4. **Scopes**: `user.info.basic`, `video.upload`, `video.publish`, `video.list`. `video.list` lets ViralClip check that a pasted TikTok link is one of your own videos, and read the view counts of what it posted. If you connected TikTok before this scope was added, connect it again.
5. **Sandbox**: create one and add **your TikTok account** as a test/target user.
6. **Redirect URI.** First try `http://localhost:3000/api/tiktok/callback`.
   - **If TikTok refuses it** (it probably requires `https://`), use a free tunnel:
     1. Install cloudflared: `winget install Cloudflare.cloudflared`
     2. With ViralClip running, run `cloudflared tunnel --url http://localhost:3000`. It prints an address like `https://something.trycloudflare.com`.
     3. Register `https://something.trycloudflare.com/api/tiktok/callback` in TikTok, and put the same value in `.env` as `TIKTOK_REDIRECT_URI`.
     4. Restart ViralClip, then **open ViralClip through the trycloudflare address** (not localhost), log in, and click *Connect TikTok*.
     5. After connecting you can close the tunnel. Posting doesn't need it.
7. Copy the **Client key** and **Client secret**. In `.env`:
   ```
   TIKTOK_CLIENT_KEY=paste-here
   TIKTOK_CLIENT_SECRET=paste-here
   ```

**Know this:** until TikTok **audits** your app, clips go to your **TikTok inbox as drafts**. You get a notification, open TikTok, paste the caption (ViralClip has a *Copy caption* button) and tap Post.
To post publicly by itself, apply for the audit in the developer portal (Content Posting API → *Apply for audit*), then set `TIKTOK_AUDITED=true`.

---

### Import from a link

Paste any link to one of **your own** videos and ViralClip downloads it:
- a Twitch VOD (`twitch.tv/videos/…`)
- a YouTube video (`youtube.com/watch?v=…`, `youtu.be/…`, `/shorts/…`, `/live/…`)
- a TikTok post (`tiktok.com/@you/video/…` or a `vm.tiktok.com/…` share link)

It checks the video belongs to an account you connected; anyone else's video is refused. Imported videos get the automation treatment (analyze, auto-approve, render) only while automation is switched on.

---

## Part 5: Connect everything (5 min)

Restart ViralClip after editing `.env`. Then in the app:
1. **YouTube Studio → Connect a channel**. Repeat for each channel.
2. **Automation → Connect Twitch**.
3. **TikTok → Connect TikTok**.

---

## Part 6: Test once before automating (10 min)

1. Upload or import a stream (**Automation → VODs → Import**), then **Generate clips**.
2. Approve one clip, then **Render**.
3. **Publish → YouTube** → privacy **Private** → publish now. Check that it appears in your real YouTube Studio.
4. **Publish → TikTok** → **Draft**. Check your TikTok inbox notifications.

If either step fails, screenshot the error and send it to Claude.

---

## Part 7: Turn on automation

1. **Automation page**
   - Tick **Auto-import new VODs** for your Twitch account.
   - Clip settings: 30–60 s, 6–8 clips per stream.
   - Approval: **I review clips** is safest. **Auto-approve the best clips** (e.g. top 3, minimum score 60) is fully hands-off.
   - Leave **Render approved clips automatically** on.
   - Turn **Automation on**.
2. **YouTube Studio**: enable an **auto-publish rule**, e.g. 2 clips a day at 18:00 in your time zone. The limit is about 6 uploads a day per Google project.
3. **TikTok page**: enable an **auto-publish rule** (draft mode until you're audited).
4. **Make it run in the background**, so you never need the window open. In PowerShell, in the `viralclip` folder:
   ```
   powershell -ExecutionPolicy Bypass -File .\autostart.ps1
   ```
   From now on ViralClip starts hidden whenever you log into Windows, restarts any part that crashes, and keeps automating. Open http://localhost:3000 whenever you want to look; `start.bat` just opens it.
   - Your PC must be **on and awake**. To stop it sleeping while plugged in: `powercfg /change standby-timeout-ac 0`
   - Turn it off again with `.\autostart.ps1 -Remove`.

   New streams are picked up within 30 minutes of ending.

**The automated flow:** stream ends → VOD imported → transcribed (~12 min per 2 h on a 4-core CPU) → best clips picked → (approved) → rendered with captions/split-screen/jump cuts (each clip takes about half its own length) → scheduled on YouTube and TikTok.

---

## Then: say "deploy agents"

Once you've connected things (or if anything breaks), tell Claude **"deploy agents"**, optionally with what you want, e.g. "deploy agents, my TikTok post failed with …". The agent team tests, fixes and reports back.
