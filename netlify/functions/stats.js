/* =====================================================================
   Private donation-stats API for the owner dashboard (admin.html).

   Runs on the SERVER (Netlify Functions). Your Stripe secret key and the
   dashboard token live here as environment variables and are NEVER sent to
   the browser — that is what makes this safe.

   Set these in Netlify -> Site settings -> Environment variables:
     STRIPE_SECRET_KEY = sk_live_xxx   (or sk_test_xxx while testing)
     ADMIN_TOKEN       = a long random secret you invent (your dashboard password)

   The dashboard sends the token in the "x-admin-token" header; we compare it
   here on the server before returning any numbers.
   ===================================================================== */

const crypto = require("crypto");

const ZERO_DECIMAL = new Set(["bif","clp","djf","gnf","jpy","kmf","krw","mga","pyg","rwf","ugx","vnd","vuv","xaf","xof","xpf"]);
const money = (minor, cur) => ZERO_DECIMAL.has((cur || "").toLowerCase()) ? minor : minor / 100;

function safeEqual(a, b) {
  const ab = Buffer.from(String(a)); const bb = Buffer.from(String(b));
  if (ab.length !== bb.length) return false;
  try { return crypto.timingSafeEqual(ab, bb); } catch { return false; }
}

const json = (statusCode, body) => ({
  statusCode,
  headers: { "content-type": "application/json", "cache-control": "no-store", "x-content-type-options": "nosniff" },
  body: JSON.stringify(body)
});

exports.handler = async (event) => {
  const expected = process.env.ADMIN_TOKEN;
  const provided = (event.headers && (event.headers["x-admin-token"] || event.headers["X-Admin-Token"])) || "";

  if (!expected) return json(500, { error: "ADMIN_TOKEN is not configured on the server." });
  if (!safeEqual(provided, expected)) return json(401, { error: "Unauthorized" });

  const sk = process.env.STRIPE_SECRET_KEY;
  if (!sk) {
    return json(200, { count: 0, totalsByCurrency: [], recent: [], generatedAt: new Date().toISOString(),
      note: "Add STRIPE_SECRET_KEY to show live totals." });
  }

  try {
    // Page through recent charges (cap pages to keep the function fast).
    const byCur = {};                 // currency -> { amount, count }
    let count = 0, monthCount = 0, monthMinorUsdish = 0;
    const recent = [];
    const startOfMonth = new Date(); startOfMonth.setUTCDate(1); startOfMonth.setUTCHours(0, 0, 0, 0);
    const monthTs = Math.floor(startOfMonth.getTime() / 1000);

    let startingAfter = null, pages = 0;
    while (pages < 10) {
      pages++;
      const params = new URLSearchParams({ limit: "100" });
      if (startingAfter) params.set("starting_after", startingAfter);
      const res = await fetch("https://api.stripe.com/v1/charges?" + params.toString(), {
        headers: { Authorization: "Bearer " + sk }
      });
      if (!res.ok) {
        const t = await res.text();
        return json(502, { error: "Stripe API error (" + res.status + ")", detail: t.slice(0, 300) });
      }
      const data = await res.json();
      for (const c of data.data || []) {
        if (c.status !== "succeeded" || !c.paid || c.refunded) continue;
        const cur = (c.currency || "").toLowerCase();
        const amt = money(c.amount_captured != null ? c.amount_captured : c.amount, cur);
        byCur[cur] = byCur[cur] || { amount: 0, count: 0 };
        byCur[cur].amount += amt; byCur[cur].count++; count++;
        if (c.created >= monthTs) monthCount++;

        if (recent.length < 25) {
          let method = "card";
          const pd = c.payment_method_details || {};
          if (pd.type && pd.type !== "card") method = pd.type;          // e.g. paypal, link, etc.
          const wallet = pd.card && pd.card.wallet && pd.card.wallet.type;
          if (wallet === "apple_pay") method = "applepay";
          else if (wallet === "google_pay") method = "gpay";
          recent.push({
            created: c.created,
            amount: amt,
            currency: cur,
            name: (c.billing_details && c.billing_details.name) || "Donor",
            method,
            status: c.status
          });
        }
      }
      if (!data.has_more) break;
      startingAfter = data.data[data.data.length - 1].id;
    }

    const totalsByCurrency = Object.keys(byCur).map(cur => ({ currency: cur, amount: Math.round(byCur[cur].amount * 100) / 100, count: byCur[cur].count }))
      .sort((a, b) => b.count - a.count);
    const primaryCurrency = totalsByCurrency[0] ? totalsByCurrency[0].currency : "usd";

    return json(200, {
      count,
      totalsByCurrency,
      primaryCurrency,
      monthCount,
      recent,
      generatedAt: new Date().toISOString(),
      note: pages >= 10 ? "Showing the most recent ~1000 payments; older ones are in your Stripe dashboard." : undefined
    });
  } catch (e) {
    return json(500, { error: "Failed to load stats", detail: String(e && e.message || e).slice(0, 300) });
  }
};
