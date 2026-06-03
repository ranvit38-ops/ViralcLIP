/* =====================================================================
   READY TO SERVE FOUNDATION  —  SITE CONFIGURATION
   ---------------------------------------------------------------------
   This is the ONLY block you need to edit. Replace the placeholders to
   make donations land directly with the charity, in ANY currency.

   HOW MONEY IS COLLECTED (no card data ever touches this website —
   everything is handed off to PCI-compliant, HTTPS payment providers):
     • INR  -> UPI  (instant, zero-fee, straight to their bank)
     • Any currency -> PayPal  (paypal.me works in every currency)
                     -> or any hosted donation page you paste below
   ===================================================================== */

window.RTS_CONFIG = {
  /* ---- Organisation ---- */
  orgName: "Ready To Serve Foundation",
  tagline: "A loving home for elders who have no one. Your gift becomes their next warm meal.",
  project: "Free Old Age Home — Vanasthalipuram, Hyderabad",
  location: "Vanasthalipuram, Hyderabad, Telangana, India",
  phone: "8790815527",
  email: "",
  regNo: "",                              // optional Trust / 80G number

  /* =================================================================
     PAYMENT SETTINGS  — fill at least one of these in.
     ================================================================= */
  payments: {
    /* UPI — used automatically for INR (instant, zero fee, direct).
       >>> Replace with the foundation's VERIFIED UPI ID. <<<            */
    upiId: "8790815527@upi",              // e.g. name@okhdfcbank, 98765xxxxx@ybl
    upiPayeeName: "Ready To Serve Foundation",

    /* PayPal — enables donations in ANY currency. Just the handle, no
       https:// — e.g. for paypal.me/ReadyToServe put "ReadyToServe".    */
    paypalHandle: "",                     // <-- add to enable global card/PayPal giving

    /* OR a hosted donation page (Stripe Payment Link, Razorpay Page,
       Donorbox, GoFundMe, etc.). If set, non-UPI currencies open this.  */
    hostedDonateUrl: ""                   // e.g. "https://donate.stripe.com/xxxx"
  },

  /* ---- Bank transfer (optional fallback) ---- */
  bank: {
    accountName: "", accountNumber: "", ifsc: "", bankName: "", branch: "", swift: ""
  },

  /* ---- Currencies & suggested amounts ----
     The first entry is the default. Each tier label is shared across
     currencies; amounts are tuned per currency.                         */
  tierLabels: [
    "A day of meals for an elder",
    "Medicines for a week",
    "A month of food & care",
    "Sponsor a birthday celebration"
  ],
  defaultTier: 2,                          // which tier is pre-selected (0-3)
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

  instagramReel: "https://www.instagram.com/reel/DX4B9sgsQLS/"
};
