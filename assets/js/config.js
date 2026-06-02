/* =====================================================================
   READY TO SERVE FOUNDATION  —  SITE CONFIGURATION
   ---------------------------------------------------------------------
   This is the ONLY file you need to edit to make donations go directly
   to the charity. Replace the placeholder values below with the real
   ones (you said "more info will follow" — drop them in here).

   Nothing else in the codebase needs to change.
   ===================================================================== */

window.RTS_CONFIG = {
  /* ---- Organisation ---- */
  orgName: "Ready To Serve Foundation",
  tagline: "A loving home for elders who have no one. Your gift becomes their next warm meal.",
  project: "Free Old Age Home — Vanasthalipuram, Hyderabad",
  location: "Vanasthalipuram, Hyderabad, Telangana, India",
  phone: "8790815527",                 // call / WhatsApp
  email: "",                           // optional — add when available
  regNo: "",                           // optional — Trust / 80G registration number

  /* ---- DIRECT DONATIONS (India / UPI) ----
     UPI sends money INSTANTLY and DIRECTLY into the charity's bank
     account with zero fees and no middle-man. This is the most direct
     method possible.

     >>> Replace upiId with the foundation's real UPI ID. <<<
     A UPI ID looks like:  name@okhdfcbank  /  9876543210@ybl  /  charity@upi
     If their UPI is linked to the phone number above it may simply be:
         8790815527@upi   (confirm with them which bank handle).            */
  upiId: "8790815527@upi",             // <-- REPLACE with verified UPI ID
  upiPayeeName: "Ready To Serve Foundation",

  /* ---- Bank transfer (NEFT/IMPS) — optional fallback ---- */
  bank: {
    accountName: "",                   // e.g. "Ready To Serve Foundation"
    accountNumber: "",
    ifsc: "",
    bankName: "",
    branch: ""
  },

  /* ---- International cards / one-tap (optional) ----
     If you create a Razorpay / Stripe payment link or a Razorpay button,
     paste the URL here and a "Donate by card" button appears automatically. */
  cardDonateUrl: "",                   // e.g. "https://rzp.io/l/your-link"

  /* ---- Suggested amounts (in ₹) and what they pay for ---- */
  presets: [
    { amount: 200,  label: "A day of meals for one elder" },
    { amount: 500,  label: "Medicines for a week" },
    { amount: 1000, label: "Feed an elder for a month" },
    { amount: 5000, label: "Sponsor a birthday celebration" }
  ],
  defaultAmount: 1000,

  /* ---- Social / source reel ---- */
  instagramReel: "https://www.instagram.com/reel/DX4B9sgsQLS/"
};
