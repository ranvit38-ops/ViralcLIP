# Twitch setup

ViralClip reads your past broadcasts through the official **Twitch Helix API** and downloads them with **yt-dlp**.
You only need a free Twitch developer app. No extra permissions (scopes) are requested: connecting only proves which channel is yours.

## 1. Turn on VOD storage (on Twitch)
1. Go to Twitch **Creator Dashboard → Settings → Stream** and turn on **Store past broadcasts**.
2. Twitch deletes past broadcasts after **7 days**: 14 days for Affiliates, 60 for Partners and Turbo users.
   - Keep ViralClip (the worker) running so automation grabs each VOD before it expires.

## 2. Create a Twitch application
1. Open <https://dev.twitch.tv/console/apps> and sign in. Enable two-factor authentication if Twitch asks.
2. Click **Register Your Application**:
   - **Name**: anything unique, e.g. `viralclip-yourname`.
   - **OAuth Redirect URLs**: `http://localhost:3000/api/twitch/callback`. If you host it elsewhere: `https://your-domain/api/twitch/callback`.
   - **Category**: Application Integration.
   - **Client Type**: Confidential.
3. Create it, open **Manage**, copy the **Client ID**, and click **New Secret** to get the **Client Secret**.

## 3. Configure ViralClip
Set these for the backend and worker (see `.env.example`), then restart both:

```bash
TWITCH_CLIENT_ID=...
TWITCH_CLIENT_SECRET=...
TWITCH_REDIRECT_URI=http://localhost:3000/api/twitch/callback
TOKEN_ENCRYPTION_KEY=...   # same key as YouTube; tokens are encrypted at rest
```

## 4. Connect and automate
1. Open **Automation → Connect Twitch**, sign in, and approve.
   - Connect each Twitch account you stream on. Only VODs from connected accounts can be imported.
2. Tick **Auto-import new VODs**, choose the clip settings and approval mode, and turn automation **on**.
3. To publish automatically, enable an auto-publish rule in **YouTube Studio**. Approved, rendered clips are scheduled into its daily slots, up to your daily limit (default 6).

Only VODs that finish *after* you turn automation on (plus one day back) are imported automatically. Older VODs can be imported by hand from the VOD list or by pasting a `twitch.tv/videos/…` link.

## Limits worth knowing
- **yt-dlp breaks now and then** when Twitch changes its site. If imports start failing, update it: `pip install -U yt-dlp`, or rebuild the Docker image.
- **Subscriber-only VODs** can't be downloaded. Downloading with your login cookies isn't supported.
- **Muted segments:** parts Twitch muted for copyrighted music stay silent, so those moments won't have transcribable speech.
- **Disk:** a 4-hour 1080p60 VOD is roughly 6–10 GB. Automation imports one VOD at a time and checks free space first. Turn on *Delete the full stream after N days* to reclaim the space; clips are kept.
- **Monetization:** clips of your own streams with captions and edits are fine. Fully unreviewed auto-posting can be flagged by YouTube as mass-produced content, so look at what goes out from time to time.
