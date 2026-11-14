# YouTube setup

ViralClip uploads through the official **YouTube Data API v3** with **OAuth 2.0**. You need your own Google Cloud project; it's free to create. Google's console UI moves around, so the menu names below may drift a little.

## 1. Create a Google Cloud project
1. Open <https://console.cloud.google.com/> and sign in with any Google account. This doesn't have to be the channel owner.
2. Use the project picker, then **New project**. Name it (e.g. `viralclip`) and create it.

## 2. Enable the YouTube Data API v3
1. Go to **APIs & Services → Library**.
2. Search for "YouTube Data API v3", open it, and click **Enable**.

## 3. Configure the OAuth consent screen
1. Go to **APIs & Services → OAuth consent screen** (newer consoles call it **Google Auth Platform → Branding / Audience / Data access**).
2. User type: **External**. Fill in the app name, a support email and a developer contact email.
3. Under **Scopes / Data access**, add:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.readonly`
4. **Audience / Test users:** while the app is in *Testing*, add the Google account of **every channel you'll connect**. Brand Account channels are connected by signing in with the Google account that manages them.
   - Testing-mode refresh tokens expire after **7 days**, so you'll have to reconnect weekly until you publish the app (step 7).

## 4. Create the OAuth client
1. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Web application**.
3. **Authorized redirect URIs**: add exactly `http://localhost:3000/api/youtube/callback`.
   - The browser only talks to the Next.js app on port 3000, which forwards `/api/*` to the backend.
   - If you host it elsewhere, use `https://your-domain/api/youtube/callback` and set `GOOGLE_REDIRECT_URI` to the same value.
4. Copy the **client ID** and **client secret**.

## 5. Configure ViralClip
Set these environment variables for the **backend** (see `.env.example`), then restart it:

```bash
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:3000/api/youtube/callback
TOKEN_ENCRYPTION_KEY=...       # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
YOUTUBE_DAILY_UPLOAD_LIMIT=6   # per channel, per Pacific day
```

- OAuth tokens are encrypted at rest with `TOKEN_ENCRYPTION_KEY`, and they're never sent to the browser.
- If you leave the key unset, a key file is generated at `$VC_DATA_DIR/.token_key` with mode 0600. That's fine on your own machine, but not for a hosted deployment.
- Changing the key makes the stored tokens unreadable, so every channel has to reconnect.

## 6. Connect channels
1. Go to **YouTube Studio → Connect a channel**, then pick the account or channel on Google's screen and approve.
2. Repeat for each channel. Each connection is stored separately.

Also make sure the worker is running (`python -m app.worker`). Uploads, imports, auto-publish and status sync all run there.

## 7. Restrictions you cannot code around

- **Unaudited projects upload private-only.** Videos uploaded through an API project that hasn't passed Google's **YouTube API Services audit** are locked to *private*. That includes scheduled videos: they won't go public at their scheduled time.
  - To lift this, complete the audit and quota extension form: <https://support.google.com/youtube/contact/yt_api_form>. Expect a review of your app and its compliance with the YouTube API Services Terms and Developer Policies.
- **Google verification.** `youtube.upload` is a sensitive scope. Moving the consent screen from *Testing* to *In production* for other users requires Google's app verification.
- **Quota (verify the current numbers in Google's docs).** New projects historically get **10,000 units/day**. `videos.insert` costs about **1,600 units**, so that's roughly **6 uploads per day per project**, across all channels.
  - Each status sync of up to 50 videos (`videos.list`) costs 1 unit.
  - The quota resets at midnight Pacific time.
  - When Google returns `quotaExceeded`, uploads pause and retry automatically after the reset. `YOUTUBE_DAILY_UPLOAD_LIMIT` also stops ViralClip before it hits the wall.
  - **Note:** the quota is per Cloud project, while `YOUTUBE_DAILY_UPLOAD_LIMIT` is per channel. With several channels on one project, lower the per-channel limit so the total stays under your project quota.
- **Scheduling** uses `status.privacyStatus=private` plus `status.publishAt`. ViralClip only shows a video as *scheduled* when YouTube's response confirms `publishAt`.
  - Changing the time of an already-uploaded video needs the broader `youtube` scope, which ViralClip deliberately doesn't request. Do that in YouTube Studio.
- **Link import** (`YouTube Studio → Import`) only accepts videos whose channel is one of your connected channels. It checks this with `videos.list`, then downloads with **yt-dlp**.
  - yt-dlp isn't an official API. It breaks for a while whenever YouTube changes things (`pip install -U yt-dlp` usually fixes it), and YouTube may block server/datacenter IPs.
  - Downloading through it may not be permitted by YouTube's Terms of Service even for your own videos. YouTube Studio's own download button is the sanctioned route.
  - The original recording on your computer is better quality than YouTube's re-encode, so prefer uploading that.

## Testing it for real
The automated tests mock Google. To verify the real flow end to end:
1. Connect a channel.
2. Upload one approved, rendered clip as **private**, and check it appears in YouTube Studio.
3. Schedule one clip 30+ minutes ahead, and confirm YouTube Studio shows it as scheduled.

Don't consider the integration production-ready until that passes on your project.
