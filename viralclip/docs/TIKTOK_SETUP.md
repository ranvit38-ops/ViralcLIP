# TikTok setup

ViralClip posts clips to TikTok through TikTok's official **Login Kit v2** (to connect your account) and the
**Content Posting API** (to upload). You need a free TikTok developer app.

> Written without access to developers.tiktok.com (the network blocked it), from TikTok's public docs.
> Items marked **(verify)** are the ones I'm least sure of. Check them against the current TikTok docs while setting up.

## 1. Create the app
1. Go to <https://developers.tiktok.com>, log in, and open **Manage apps → Connect an app**.
2. Fill in the app details: name, icon, category, description, and terms of service and privacy policy URLs.
   TikTok wants real URLs even for review. A page on your own site or a public GitHub page is fine.
3. Under **Products**, add:
   - **Login Kit** (Web platform).
   - **Content Posting API**. Turn on **Direct Post** if you want ViralClip to publish straight to your profile.
     Without it, only draft mode (videos sent to your TikTok inbox) works.
4. Under **Scopes**, add `user.info.basic`, `video.upload`, `video.publish` and `video.list` (link import of your own videos, and view counts).
5. Under Login Kit, add the **Redirect URI** (see section 2), then copy the **Client key** and **Client secret**.

### Sandbox and test users
New apps start unreviewed. Create a **Sandbox** in the app page and add your own TikTok account under
**Target users / test users** (verify: the exact menu name changes between portal versions).
Sandbox apps can log in and post only for those listed accounts, which is all you need for your own channel.

## 2. Redirect URI: does TikTok accept http://localhost?
**Most likely not.** As far as I know, TikTok requires Login Kit web redirect URIs to be **HTTPS**, absolute and
without query parameters, and rejects `http://localhost` **(verify)**. Try the default first:

```
http://localhost:3000/api/tiktok/callback
```

If the portal refuses it, put ViralClip behind a free HTTPS tunnel. **Cloudflare Tunnel** needs no account for a quick test:

```bash
# install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:3000
# prints e.g. https://random-words-1234.trycloudflare.com
```

1. Register `https://random-words-1234.trycloudflare.com/api/tiktok/callback` as the redirect URI in the TikTok portal.
2. Set `TIKTOK_REDIRECT_URI` to the same value, then restart the backend.
3. **Open ViralClip through the tunnel URL** and log in there before clicking *Connect TikTok*.
   The login cookie belongs to the address you're on: if you start at `localhost`, the callback arrives at the
   tunnel address without your session and you land on the login page.
4. You need the tunnel only while connecting. Posting runs from the worker and doesn't need it.

Quick-tunnel addresses change every time `cloudflared` restarts, so you'd re-register the URI each time you reconnect.
For a stable address, use a named Cloudflare tunnel on a domain you own, or any HTTPS reverse proxy (Caddy, nginx).
**(verify)** whether the Next.js dev server accepts requests for the tunnel's host name. The production build
(`npm run build && npm start`) does.

## 3. Configure ViralClip
Set these for the backend and worker (see `.env.example`), then restart both:

```bash
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=http://localhost:3000/api/tiktok/callback   # or your https tunnel URL
TIKTOK_AUDITED=false          # set true only after TikTok approves your app's audit
TIKTOK_DAILY_POST_LIMIT=15    # posts per TikTok account per day (UTC)
TOKEN_ENCRYPTION_KEY=...      # the same key as YouTube/Twitch; tokens are encrypted at rest
```

## 4. Audit: why unaudited posts are private or drafts
TikTok restricts apps that haven't passed its **Content Posting API audit**:
- **Direct posts from an unaudited app can only be private** (`SELF_ONLY`), visible only to you, and the account
  may need to be set to private **(verify)**. ViralClip enforces this: with `TIKTOK_AUDITED=false` only `SELF_ONLY` is offered.
- That's why **draft mode is the default** while unaudited. The clip lands in your TikTok inbox (you get a notification),
  and you open the TikTok app, add sounds or effects, choose who can see it, and post it publicly yourself.
  TikTok's API doesn't let ViralClip set the caption in draft mode, so you type it in the app.
- To post publicly from ViralClip, apply for the audit in the developer portal (Content Posting API → **Apply for audit**).
  TikTok reviews how your app shows the creator's nickname, privacy options and interaction toggles before posting,
  which ViralClip's TikTok page does. Once approved, set `TIKTOK_AUDITED=true`.
- Audited or not, TikTok asks apps to show what will be posted and get the creator's explicit consent. Auto-publish
  rules post without a per-video confirmation, which an auditor may not accept **(verify)**.

## 5. Posting caps and limits
- **About 15 posts per creator per day** through the API, shared across all apps **(verify: TikTok calls it approximate)**.
  ViralClip stops at `TIKTOK_DAILY_POST_LIMIT` per account per UTC day and waits until the next day.
  If TikTok refuses anyway (`spam_risk_too_many_posts`), the post waits an hour and tries again.
- Rate limits (approximate): 6 init requests per minute per user, 20 creator-info requests per minute.
  A rate-limited post waits 5 minutes and retries by itself.
- Video: MP4/H.264, which is what ViralClip renders. The maximum length per account is read from TikTok
  (`max_video_post_duration_sec`) and checked before posting.
- Captions: up to 2,200 characters. ViralClip uses the clip title plus its hashtags.
- Unaudited apps also have a cap on how many users can post through them (verify the current number). That doesn't
  matter for your own account.

## 6. Scheduling only works while ViralClip runs
TikTok's API has **no "publish at" field**. When you schedule a TikTok post, ViralClip's worker posts it at that time.
If the worker isn't running then, the post goes out when it starts again; if that's more than 12 hours late, it
isn't posted and is marked failed so you can decide. YouTube scheduling is different: YouTube holds the video and
publishes it on its side.

## 7. Connect and automate
1. Open **TikTok → Connect TikTok**, sign in and approve. You can connect several accounts.
2. To post: select approved, rendered clips → **Post to TikTok** → choose draft or direct and, for direct, a privacy level.
3. To automate: set an auto-publish rule on the account (time of day, clips per day, mode). The worker gives each
   approved, rendered clip that isn't on that account yet the next free daily slot.
   Combined with Automation (Twitch VOD → clips → auto-approve → render), this runs hands-off.

## What wasn't verified
All tests run against a mocked TikTok. Before trusting it, connect your account in sandbox and send one clip as a
draft, then one direct `SELF_ONLY` post.
