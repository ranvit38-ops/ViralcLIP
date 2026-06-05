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

## 💳 Donations — card, Apple Pay, Google Pay, any currency — securely

The donation card shows **Card · Apple Pay · Google Pay** first (where donors
enter their details), with **UPI / PayPal / bank** underneath. **No card data is
ever entered or stored on this website** — the card form and Apple Pay button are
served by a trusted, PCI-compliant processor. You just paste one value to switch
it on.

### To accept card + Apple Pay + Google Pay (pick ONE, ~5-min free signup)

In the **EDIT block** near the bottom of `index.html`, set one of:

```js
payments: {
  // (a) EASIEST — Stripe Payment Link (just a URL). Card, Apple Pay,
  //     Google Pay, name/email + receipt, all on Stripe's secure page.
  stripePaymentLink: "",        // "https://donate.stripe.com/xxxx"

  // (b) Donorbox — an embedded donation form (also does recurring):
  donorboxUrl: "",              // "https://donorbox.org/embed/your-campaign"

  // (c) Stripe Buy Button — embedded button (2 values):
  stripeBuyButtonId: "",        // "buy_btn_xxx"
  stripePublishableKey: "",     // "pk_live_xxx"
  ...
}
```

**Stripe Payment Link is the quickest:** dashboard.stripe.com → *Payment links* →
*New* → turn on *“Let customers choose what to pay”* → copy the URL. Apple Pay &
Google Pay appear automatically for eligible devices.

### Also available (no signup needed)

```js
  upiId: "8790815527@upi",   // INR — instant, zero-fee, direct (REPLACE w/ verified ID)
  paypalHandle: "",          // "ReadyToServe" -> donations in any currency via PayPal
```

- **UPI** handles ₹ instantly with zero fees. **PayPal** covers any currency.
- The currency list + suggested amounts are just below, editable.
- **Bank transfer** (incl. international **SWIFT**) — fill `bank.accountNumber` +
  `bank.ifsc`/`swift` → a transfer panel appears automatically.

> ⚠️ Until a provider is set, the card area shows a tidy "ready to switch on"
> preview (never a broken or fake form), and UPI/PayPal remain usable.

### Security
- The card form / Apple Pay live inside the **processor's** secure page or iframe
  (Stripe, Donorbox) — this site never sees raw card numbers (PCI-DSS safe).
- Only the trusted processor domains are allow-listed in the CSP; any other or
  non-HTTPS payment URL you paste is **rejected** and falls back to the preview.
- See the **Security** section below for the full header set.

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
