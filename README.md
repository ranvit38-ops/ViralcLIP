# Ready To Serve Foundation — Donation Website

A warm, mobile-first website for the **Ready To Serve Foundation**, a free old age
home in Vanasthalipuram, Hyderabad. It lets anyone — on a phone or a laptop —
send money **directly** to the foundation in about 30 seconds.

It is a plain static website (HTML + CSS + a little JavaScript), so it works
**anywhere** and can be hosted for free.

---

## ✅ The one thing you must do: add the real payment details

Open **`assets/js/config.js`** and replace the placeholder values. That single
file controls everything — the charity name, contact details, suggested amounts
and, most importantly, where the money goes.

```js
upiId: "8790815527@upi",   // <-- REPLACE with the foundation's VERIFIED UPI ID
upiPayeeName: "Ready To Serve Foundation",
```

### How the money reaches the charity *directly*

The site uses **UPI** (India's instant payment system). When a donor taps
**“Pay with UPI app”** (on a phone) or **scans the QR code** (on a laptop),
their banking app opens with:

- the foundation's **UPI ID** already filled in,
- the **amount** they chose already filled in,
- a note saying “Donation to Ready To Serve Foundation”.

The money moves **bank-to-bank, instantly, with no fees and no middle-man** —
100% reaches the home. There is no server in between and nothing for you to host
or maintain.

> ⚠️ **Important:** Confirm the exact UPI ID with the foundation before going
> live (e.g. `name@okhdfcbank`, `9876543210@ybl`, etc.). A wrong handle sends
> money to the wrong place. Do a ₹1 test donation first.

### Optional extras (all in `config.js`)
- **Bank transfer** — fill in `bank.accountNumber` + `bank.ifsc` and a
  “Bank transfer (NEFT/IMPS)” panel appears automatically.
- **Card / international donors** — paste a Razorpay/Stripe payment-link URL into
  `cardDonateUrl` and a “Donate by card” button appears.
- **Email, registration/80G number, suggested amounts** — all editable there too.

---

## 🚀 Putting it online (free, ~5 minutes)

Pick any one — they all work on mobile and desktop:

| Host | How |
|------|-----|
| **GitHub Pages** | Push this repo → Settings → Pages → Deploy from branch → `/ (root)` |
| **Netlify** | Drag-and-drop this folder onto [app.netlify.com/drop](https://app.netlify.com/drop) |
| **Vercel** | `vercel` in this folder, or import the repo at vercel.com |
| **Cloudflare Pages** | Connect the repo, framework = “None”, output dir = `/` |

To preview locally:

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

---

## 📁 What's inside

```
index.html              The whole page
assets/css/styles.css   Styling (warm green + gold theme, fully responsive)
assets/js/config.js     👈 EDIT THIS — charity & payment details
assets/js/main.js       Donation logic, UPI links, live QR, animations
assets/video/           The foundation's videos (compressed for the web)
assets/img/             Poster images + favicon
```

The videos you provided are used as real testimonials in the **Our Home** and
**Stories** sections.

---

Made with care. 🙏  *“అన్నదాత! సుఖీభవ!” — blessings to every giver.*
