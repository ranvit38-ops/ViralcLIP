/* =====================================================================
   READY TO SERVE FOUNDATION  —  SITE CONFIGURATION
   ---------------------------------------------------------------------
   This is the ONLY block you need to edit. Fill in a payment provider to
   accept CARD, APPLE PAY, GOOGLE PAY and donor details — securely.

   ⓘ A website can never safely take raw card numbers itself (that would
     break PCI-DSS). Instead, the card form + Apple Pay are served by a
     trusted processor (Stripe / Donorbox). Pick ONE of the options below;
     each is a free ~5-minute signup, then paste a value here:

       • EASIEST card + Apple Pay  -> Stripe "Payment link"  (just a URL)
       • Embedded card form        -> Donorbox campaign      (one URL)
       • Embedded Stripe button    -> Stripe Buy Button       (2 values)
       • INR, instant & zero-fee   -> UPI                     (a UPI ID)
       • Any currency, no signup   -> PayPal                  (a handle)
   ===================================================================== */

window.RTS_CONFIG = {
  /* ---- Organisation ---- */
  orgName: "Ready To Serve Foundation",
  tagline: "A loving home for elders who have no one. Your gift becomes their next warm meal.",
  project: "Free Old Age Home — Vanasthalipuram, Hyderabad",
  location: "Vanasthalipuram, Hyderabad, Telangana, India",
  phone: "8790815527",
  email: "",
  regNo: "",

  /* =================================================================
     PAYMENT SETTINGS — fill in at least one. The donation card shows
     CARD / APPLE PAY first whenever a card provider below is set.
     ================================================================= */
  payments: {

    /* ---- CARD · APPLE PAY · GOOGLE PAY (donor enters their details) ----
       Choose ANY one of these three. All collect card/Apple Pay/Google Pay,
       name and email, and email a receipt — hosted & PCI-compliant.        */

    // (a) Stripe Payment Link — simplest. Create one at dashboard.stripe.com
    //     (Payment links → New). Turn on "Let customers choose amount" for
    //     donations, and Apple/Google Pay are automatic. Paste the URL:
    stripePaymentLink: "",        // e.g. "https://donate.stripe.com/xxxxxxxx"

    // (b) Donorbox — embedded donation form (also does recurring).
    //     Make a campaign, then copy its embed URL:
    donorboxUrl: "",              // e.g. "https://donorbox.org/embed/your-campaign"

    // (c) Stripe Buy Button — embedded button. From Stripe → Buy button:
    stripeBuyButtonId: "",        // e.g. "buy_btn_xxx"
    stripePublishableKey: "",     // e.g. "pk_live_xxx"

    /* ---- UPI (INR only — instant, zero fee, straight to their bank) ---- */
    upiId: "8790815527@upi",      // <-- REPLACE with the VERIFIED UPI ID
    upiPayeeName: "Ready To Serve Foundation",

    /* ---- PayPal (works in any currency, no signup keys needed) ---- */
    paypalHandle: ""              // e.g. "ReadyToServe"  (from paypal.me/ReadyToServe)
  },

  /* ---- Bank transfer (optional fallback) ---- */
  bank: {
    accountName: "", accountNumber: "", ifsc: "", bankName: "", branch: "", swift: ""
  },

  /* ---- Currencies & suggested amounts (used by the UPI / PayPal quick-give) ---- */
  tierLabels: [
    "A day of meals for an elder",
    "Medicines for a week",
    "A month of food & care",
    "Sponsor a birthday celebration"
  ],
  defaultTier: 2,
  currencies: [
    { code: "INR", symbol: "₹",  locale: "en-IN", amounts: [200, 500, 1000, 5000] },
    { code: "USD", symbol: "$",  locale: "en-US", amounts: [5, 15, 30, 100] },
    { code: "EUR", symbol: "€",  locale: "en-IE", amounts: [5, 15, 30, 100] },
    { code: "GBP", symbol: "£",  locale: "en-GB", amounts: [5, 10, 25, 75] },
    { code: "AUD", symbol: "A$", locale: "en-AU", amounts: [10, 25, 50, 150] },
    { code: "CAD", symbol: "C$", locale: "en-CA", amounts: [10, 25, 50, 150] },
    { code: "SGD", symbol: "S$", locale: "en-SG", amounts: [10, 25, 50, 150] },
    { code: "AED", symbol: "AED",locale: "en-AE", amounts: [20, 50, 100, 300] }
  ],

  /* =================================================================
     TRACKING & PRIVATE OWNER DASHBOARD
     ----------------------------------------------------------------
     "How many people on the site" -> add ONE analytics tool below.
       You view the numbers in that tool's own login-protected dashboard
       (only you can see it). All are free and privacy-friendly.
     "How many paid / amounts / totals" -> your payment processor's
       dashboard already shows this privately (Stripe / Donorbox), and
       the on-site /admin.html page can show live totals via a serverless
       function (see README → "Private owner dashboard").
     ================================================================= */
  analytics: {
    googleAnalyticsId: "",   // "G-XXXXXXXXXX"  (analytics.google.com)
    plausibleDomain: "",     // "yourdomain.org" (plausible.io — no cookie banner needed)
    cloudflareToken: ""      // Cloudflare Web Analytics token (cloudflare.com)
  },
  admin: {
    // Where admin.html fetches live donation totals from (your serverless
    // function). Leave as-is for Netlify; admin.html also has a demo mode.
    statsEndpoint: "/.netlify/functions/stats"
  },

  instagramReel: "https://www.instagram.com/reel/DX4B9sgsQLS/"
};

