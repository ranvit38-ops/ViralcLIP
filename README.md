# Ready To Serve Foundation — Donation Website

A warm, mobile-first website for the **Ready To Serve Foundation**, a free old age
home in Vanasthalipuram, Hyderabad. Anyone — on a phone or a laptop — can send
money **directly** to the foundation by UPI in about 30 seconds.

## ✅ How to open / test it

**`index.html` is the whole website in one self-contained file** — the design,
the donation logic, the QR engine and the images are all baked in, so it can
never load "half-broken." Just **double-click `index.html`** and it opens fully
styled and working in any browser. (Keep the `assets/video` folder next to it so
the videos play; everything else works even on its own.)

To put it online so you can open it on your **phone**, see _Going live_ below.

## ✅ The one thing to change before going live: the UPI ID

Open **`index.html`**, scroll to the clearly-marked **EDIT** block near the
bottom (`window.RTS_CONFIG = { ... }`), and replace:

```js
upiId: "8790815527@upi",   // <-- REPLACE with the foundation's VERIFIED UPI ID
```

with the foundation's **real, verified UPI ID** (confirm the exact handle with
them — e.g. `name@okhdfcbank`, `9876543210@ybl`, `charity@upi`).

### How donations reach them directly
When a donor taps **“Pay with UPI app”** (phone) or **scans the QR** (laptop),
their banking app opens pre-filled with the foundation's UPI ID, the amount they
chose, and a donation note. The money moves **bank-to-bank, instantly, with no
fees and no middle-man** — 100% reaches the home. No server, nothing to host.

> ⚠️ Do a ₹1 test transfer to the UPI ID first to confirm it lands in the right
> account before sharing the site publicly.

### Optional extras (same EDIT block)
- **Bank transfer** — fill `bank.accountNumber` + `bank.ifsc` → a NEFT/IMPS panel appears.
- **Card / international** — paste a Razorpay/Stripe link into `cardDonateUrl` → a card button appears.
- **Email, registration/80G number, suggested amounts** — all editable there too.

## 🚀 Going live (free, ~1 minute, gives a link that works on phones)

Easiest: drag this folder onto **https://app.netlify.com/drop** — you instantly
get a free public link. No sign-up, no settings.

Other free hosts: **GitHub Pages** (Settings → Pages → Source = GitHub Actions;
note: private repos need a paid plan, so make the repo public or use Netlify),
**Vercel**, **Cloudflare Pages**.

## 📁 What's inside

```
index.html              👈 THE WEBSITE — open this. Self-contained; edit the config block to set the UPI ID.
assets/video/           The foundation's videos (compressed for the web)
assets/img/             Poster images + favicon
index.src.html          Source template used to build index.html (for developers)
assets/css, assets/js   Editable source parts (CSS / JS / config / QR engine)
build_standalone.py     Rebuilds index.html from the source parts:  python3 build_standalone.py
.github/workflows/      Optional GitHub Pages auto-deploy
```

Most people only ever touch the EDIT block inside `index.html`. The `index.src.html`
+ `build_standalone.py` route is only if a developer wants to change the layout
and regenerate the single file.

---

Made with care. 🙏  *“అన్నదాత! సుఖీభవ!” — blessings to every giver.*
