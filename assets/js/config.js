/* =====================================================================
   READY TO SERVE FOUNDATION  —  SITE CONFIGURATION
   ---------------------------------------------------------------------
   This is the ONLY block you need to edit.

   Donations are handled entirely by DONORBOX (an embedded, PCI-compliant
   form). Donors can give by card, Apple Pay, Google Pay — one-time or
   monthly — in rupees (₹) and many other currencies, from any country.
   No card details ever touch this website.
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
     PAYMENTS — handled by Donorbox (already connected below).
     ================================================================= */
  payments: {
    // Your Donorbox campaign (campaign URL or its /embed/ URL — both work).
    donorboxUrl: "https://donorbox.org/ready-to-serve-foundation",

    // Optional alternatives (leave blank to keep using Donorbox):
    stripePaymentLink: "",
    stripeBuyButtonId: "",
    stripePublishableKey: "",
    hostedDonateUrl: ""
  },

  /* ---- Bank transfer (optional; shows a panel only if filled in) ---- */
  bank: { accountName: "", accountNumber: "", ifsc: "", bankName: "", branch: "", swift: "" },

  /* ---- "Your impact" examples (illustration only; donors pick the real
         amount & currency inside the donation form) ---- */
  impact: [
    { amount: "₹200",   label: "A day of meals for an elder" },
    { amount: "₹500",   label: "Medicines for a week" },
    { amount: "₹1,000", label: "A month of food & care" },
    { amount: "₹5,000", label: "Sponsor a birthday celebration" }
  ],

  /* ---- Currencies the donation form accepts (shown as an on-page note).
         Enable multi-currency in your Donorbox campaign so donors can pick. ---- */
  acceptedCurrencies: ["₹ INR", "$ USD", "€ EUR", "£ GBP", "A$ AUD", "C$ CAD", "S$ SGD", "AED"],

  /* ---- Visitor analytics (you view counts in each tool's own dashboard) ---- */
  analytics: {
    googleAnalyticsId: "",   // "G-XXXXXXXXXX"
    plausibleDomain: "",     // "yourdomain.org"
    cloudflareToken: ""      // Cloudflare Web Analytics token
  },
  admin: { statsEndpoint: "/.netlify/functions/stats" },

  instagramReel: "https://www.instagram.com/reel/DX4B9sgsQLS/"
};
