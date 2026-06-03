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

## 💳 Donations — works in any currency, and securely

Donors pick a **currency** on the donation card. The site then routes them to the
right **PCI-compliant** payment provider — **no card details are ever entered or
stored on this website.** All it does is build a secure outbound link:

| Currency | Goes to | Notes |
|---|---|---|
| **INR (₹)** | **UPI** | Instant, zero-fee, straight to their bank. Phone + laptop (QR). |
| **Any other** | **PayPal** | `paypal.me` works in every currency; pay by card or PayPal. |
| (alt) | **Hosted page** | Stripe / Razorpay / Donorbox / GoFundMe link, if you prefer. |

### Turn it on (the EDIT block near the bottom of `index.html`)

```js
payments: {
  upiId: "8790815527@upi",   // INR — REPLACE with the foundation's VERIFIED UPI ID
  paypalHandle: "",          // e.g. "ReadyToServe"  -> enables ALL other currencies
  hostedDonateUrl: ""        // optional: a Stripe/Razorpay/Donorbox/GoFundMe link
}
```

- Set **`upiId`** → ₹ donations work (do a ₹1 test first to confirm the handle).
- Set **`paypalHandle`** → USD/EUR/GBP/AUD/CAD/SGD/AED… all start working instantly.
- The currency list and suggested amounts are right below, also editable.
- **Bank transfer** (incl. international SWIFT) — fill `bank.accountNumber` +
  `bank.ifsc`/`swift` → a transfer panel appears automatically.

> ⚠️ Until you add `upiId` and/or `paypalHandle`, donating shows a friendly
> "not enabled yet" message instead of a broken link.

## 🔒 Security

- **No card data touches this site** — payments are delegated to UPI apps, PayPal
  and other PCI-DSS-compliant providers over HTTPS.
- **Content-Security-Policy** + `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `HSTS` and `COOP` are set (in the page
  `<meta>` and in `_headers` / `netlify.toml` for the host).
- **Input is validated**: amounts must be positive finite numbers, are clamped,
  and are URL-encoded; script/HTML can't be injected into any payment link.
- External links use `rel="noopener noreferrer"`. No trackers, no data collection.
- Host it over **HTTPS** (Netlify/Pages/Vercel/Cloudflare all give free HTTPS).

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
