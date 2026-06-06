# Ready To Serve Foundation — Donation Website

A warm, mobile-first website for the **Ready To Serve Foundation**, a free old age
home in Vanasthalipuram, Hyderabad. Anyone in the world — on a phone or a laptop —
can donate by **card, Apple Pay, Google Pay** (one-time or monthly) in about
30 seconds, through an embedded **Donorbox** form.

## ✅ How to open / test it

**`index.html` is the whole website in one self-contained file** — the design,
the donation logic, the QR engine and the images are all baked in, so it can
never load "half-broken." Just **double-click `index.html`** and it opens fully
styled and working in any browser. (Keep the `assets/video` folder next to it so
the videos play; everything else works even on its own.)

To put it online so you can open it on your **phone**, see _Going live_ below.

## 💳 Donations — card, Apple Pay, Google Pay, any currency

The donation section embeds a **Donorbox** form where donors enter their details
and pay by **card, Apple Pay, Google Pay** (one-time or monthly), in 40+
currencies, from anywhere in the world. **No card data is ever entered or stored
on this website** — the form is served by Donorbox (PCI-compliant) inside a secure
iframe.

It's already wired to the campaign:

```js
payments: {
  donorboxUrl: "https://donorbox.org/ready-to-serve-foundation",  // campaign or /embed/ URL
  ...
}
```

### ⚠️ The one step left to actually receive money
In the **Donorbox dashboard** (app.donorbox.org), the campaign must have a
**payout method connected** — Donorbox walks you through connecting **Stripe**
(quick express setup) or **PayPal**. That's where the foundation's bank details
go, so donations have somewhere to land. Do a small test donation once connected.

### Make the form's colours match the site
The embedded form sits in a branded green/gold frame already. To recolour the
**form's own buttons/accents** (which live inside Donorbox and can't be styled
from this site), set them in **app.donorbox.org → your campaign → Edit →
Design/Style**, using the site's palette:

| Use | Hex |
|---|---|
| Brand / button colour (primary) | `#157347` (or deeper `#0f5132`) |
| Accent / highlight | `#E0A93B` (gold) |
| Background | `transparent` or `#FBF7EF` (cream) |
| Text | `#22281F` |

(Some colour options require Donorbox's paid plan; the brand/button colour is
available on the free plan.)

### Donate in rupees and other currencies
The site shows that donations are accepted in **₹ INR, USD, EUR, GBP, AUD, CAD,
SGD, AED** (edit `acceptedCurrencies` in the config to change the list). For
donors to actually *choose* their currency in the form, turn on **multi-currency**
in your Donorbox campaign: **app.donorbox.org → your campaign → Edit → Settings →
enable multiple currencies** (set ₹ INR as the base). Donorbox then shows a
currency selector and handles the conversion.

### "Thank you" after donating
A branded **`thank-you.html`** page is included. To greet donors after they give:
**Donorbox → your campaign → Edit → Receipts/Settings → "Redirect to your own page
after a successful donation"** and paste the URL of `thank-you.html` (e.g.
`https://yoursite/thank-you.html`). The site also shows a thank-you pop-up if a
donor returns to `index.html?donated=1` (or `#thanks`) — handy if you prefer
redirecting to the home page. Donorbox also emails every donor an automatic
receipt + thank-you, which you can customise in its dashboard.

### Other options (optional)
The same `payments` block also supports a **Stripe Payment Link**
(`stripePaymentLink`), a **Stripe Buy Button**, or **PayPal** (`paypalHandle`) —
paste any one and it takes over automatically. If none is set, the donation area
shows a tidy "ready to switch on" preview (never a broken or fake form).

### Security
- The donation form lives inside **Donorbox's** secure iframe — this site never
  sees raw card numbers (PCI-DSS safe).
- Only trusted processor domains (Donorbox/Stripe/PayPal) are allow-listed in the
  CSP; any other or non-HTTPS payment URL is **rejected**.
- See the **Security** section below for the full header set.

## 🔒 Security

- **No card data touches this site** — the donation form is served by Donorbox
  (PCI-DSS compliant) inside a secure iframe over HTTPS.
- **Content-Security-Policy** + `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `HSTS` and `COOP` are set (in the page
  `<meta>` and in `_headers` / `netlify.toml` for the host).
- Only trusted processor/analytics domains are allow-listed; any other or
  non-HTTPS payment URL is rejected.
- External links use `rel="noopener noreferrer"`. No trackers, no data collection.
- Host it over **HTTPS** (Netlify/Pages/Vercel/Cloudflare all give free HTTPS).

## 📊 Tracking & a private owner-only dashboard

Everything here is private to **you** — none of it is visible to visitors.

### 1) How many people are on the site (visitors)
Add **one** analytics tool in the EDIT block (`analytics:` in `index.html`), then
read the numbers in that tool's own **login-protected** dashboard:

```js
analytics: {
  googleAnalyticsId: "",   // "G-XXXXXXXXXX"  (analytics.google.com — live users, totals)
  plausibleDomain: "",     // "yourdomain.org" (plausible.io — simple, privacy-first)
  cloudflareToken: ""      // Cloudflare Web Analytics (free, no cookie banner)
}
```

All are free. Google Analytics even shows **live "users on site right now."**

### 2) How many paid · what they paid · totals
Your **payment processor's dashboard is the official private record** and already
shows every payment, amount, donor, receipt and refund:
- **Stripe** → dashboard.stripe.com  • **Donorbox** → app.donorbox.org  • **PayPal** → paypal.com

### 3) An on-site private dashboard (`/admin.html`)
The site includes an owner dashboard at **`admin.html`** showing **total raised,
number of donations, average gift, this month, and recent gifts**.

- Open `admin.html?demo=1` (or click **“See a demo”**) to preview it now with
  sample numbers.
- For **live** numbers, deploy the included serverless function and set two
  secrets on your host (so they stay on the **server**, never in the website):

  **Netlify** → Site settings → Environment variables:
  ```
  STRIPE_SECRET_KEY = sk_live_xxx     (from your Stripe dashboard)
  ADMIN_TOKEN       = <a long random secret you invent>   ← this is your dashboard password
  ```
  The function `netlify/functions/stats.js` reads Stripe **server-side**; the
  dashboard asks for your `ADMIN_TOKEN` and the server verifies it before
  returning any data. Anyone without the token gets **401 Unauthorized**.

> 🔒 Why it's done this way: a website's own code is downloadable by anyone, so a
> password hidden in JavaScript is **not** secure. Real protection means the secret
> key + token live on the server (the function) — which is exactly this setup. For
> extra safety you can also enable Netlify's built-in password protection / Identity
> on `/admin.html`.

## 🚀 Going live (free, ~1 minute, gives a link that works on phones)

Easiest: drag this folder onto **https://app.netlify.com/drop** — you instantly
get a free public link. No sign-up, no settings.

Other free hosts: **GitHub Pages** (Settings → Pages → Source = GitHub Actions;
note: private repos need a paid plan, so make the repo public or use Netlify),
**Vercel**, **Cloudflare Pages**.

## 📁 What's inside

```
index.html              👈 THE WEBSITE — open this. Self-contained; edit the config block to set the Donorbox URL.
thank-you.html          Branded "thank you" page shown after a donation (set as Donorbox redirect).
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
