/* =====================================================================
   Ready To Serve Foundation — interactions, multi-currency donations
   Security notes:
     • No card data is ever entered or stored here; we only build links
       to PCI-compliant providers (UPI apps, PayPal, hosted pages).
     • All user input (amount) is validated as a finite positive number
       and clamped; it is encoded before being placed in any URL.
     • External links open with rel="noopener noreferrer".
   ===================================================================== */
(function () {
  "use strict";
  var cfg = window.RTS_CONFIG || {};
  var pay = cfg.payments || {};
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var MAX_AMOUNT = 1000000; // sane upper bound to reject bad/overflow input

  function currencies() { return (cfg.currencies && cfg.currencies.length) ? cfg.currencies : [{ code: "INR", symbol: "₹", locale: "en-IN", amounts: [200, 500, 1000, 5000] }]; }
  function findCurrency(code) { return currencies().filter(function (c) { return c.code === code; })[0] || currencies()[0]; }

  var state = {
    cur: currencies()[0],
    amount: 0
  };

  /* ---------- Amount helpers ---------- */
  function decimals(cur) { return cur.code === "INR" ? 0 : 2; }

  function sanitize(value, cur) {
    var n = typeof value === "number" ? value : parseFloat(String(value).replace(/[^0-9.\-]/g, ""));
    if (!isFinite(n) || n <= 0) return 0;   // reject negatives, zero, NaN, Infinity
    if (n > MAX_AMOUNT) n = MAX_AMOUNT;
    var d = decimals(cur);
    return d === 0 ? Math.round(n) : Math.round(n * 100) / 100;
  }

  function fmt(amount, cur) {
    try {
      return new Intl.NumberFormat(cur.locale, {
        style: "currency", currency: cur.code,
        minimumFractionDigits: 0, maximumFractionDigits: decimals(cur)
      }).format(amount);
    } catch (e) {
      return cur.symbol + amount.toLocaleString();
    }
  }
  function plain(amount, cur) { return amount.toFixed(decimals(cur)); }

  /* ---------- Payment routing ---------- */
  // Returns { method, enabled, href, newTab, label, hint, reason }
  function resolvePayment() {
    var amt = state.amount, cur = state.cur;
    var amtStr = amt ? fmt(amt, cur) : fmt(0, cur);

    // INR -> UPI (preferred: instant, zero-fee, direct)
    if (cur.code === "INR" && pay.upiId) {
      return {
        method: "upi", enabled: !!amt, newTab: false,
        href: upiLink(amt),
        label: amt ? "Pay " + amtStr + " with UPI app" : "Choose an amount",
        hint: "Opens Google Pay, PhonePe, Paytm or any UPI app on your phone."
      };
    }
    // Any currency -> PayPal (works in every currency)
    if (pay.paypalHandle) {
      var h = encodeURIComponent(String(pay.paypalHandle).replace(/^https?:\/\/(www\.)?paypal\.me\//i, "").replace(/^\/+|\/+$/g, ""));
      var url = "https://www.paypal.com/paypalme/" + h + (amt ? "/" + encodeURIComponent(plain(amt, cur)) + cur.code : "");
      return {
        method: "paypal", enabled: !!amt, newTab: true, href: url,
        label: amt ? "Donate " + amtStr + " via PayPal" : "Choose an amount",
        hint: "Secure checkout on PayPal — pay by card or PayPal balance. You can change the amount there too."
      };
    }
    // Any currency -> hosted donation page
    if (pay.hostedDonateUrl) {
      return {
        method: "hosted", enabled: true, newTab: true, href: pay.hostedDonateUrl,
        label: amt ? "Donate " + amtStr : "Donate securely",
        hint: "Secure checkout on our donation partner's page."
      };
    }
    // Nothing configured for this currency
    if (cur.code === "INR") {
      return { method: "none", enabled: false, href: "#",
        label: "Donations not yet enabled",
        hint: "", reason: "Add a UPI ID in the settings to enable INR donations." };
    }
    return { method: "none", enabled: false, href: "#",
      label: "International giving coming soon",
      hint: "", reason: "Add a PayPal handle or hosted donation link to accept " + cur.code + ". UPI supports INR only — switch to ₹ INR to give now." };
  }

  function upiLink(amount) {
    var p = new URLSearchParams();
    p.set("pa", pay.upiId || "");
    p.set("pn", pay.upiPayeeName || cfg.orgName || "");
    p.set("cu", "INR");
    if (amount && amount > 0) p.set("am", String(amount));
    p.set("tn", "Donation to " + (cfg.orgName || "charity"));
    return "upi://pay?" + p.toString();
  }

  /* ---------- Render currency selector ---------- */
  function renderCurrencies() {
    var sel = $("#currencySel");
    if (!sel) return;
    sel.innerHTML = currencies().map(function (c) {
      return '<option value="' + c.code + '">' + c.code + " (" + c.symbol + ")</option>";
    }).join("");
    sel.value = state.cur.code;
    sel.addEventListener("change", function () {
      state.cur = findCurrency(sel.value);
      renderAmountButtons();
      setTier(cfg.defaultTier != null ? cfg.defaultTier : 2);
    });
  }

  /* ---------- Impact cards (use INR amounts for the on-page examples) ---------- */
  function renderImpact() {
    var grid = $("#impactGrid");
    if (!grid) return;
    var inr = findCurrency("INR");
    var labels = cfg.tierLabels || [];
    grid.innerHTML = inr.amounts.map(function (a, i) {
      return '<button class="impact-card" type="button" data-tier="' + i + '">' +
        '<div class="impact-card__amt">' + fmt(a, inr) + '</div>' +
        '<div class="impact-card__lbl">' + (labels[i] || "") + '</div>' +
        '<div class="impact-card__cta">Give this →</div></button>';
    }).join("");
    $$(".impact-card", grid).forEach(function (c) {
      c.addEventListener("click", function () {
        state.cur = inr; var sel = $("#currencySel"); if (sel) sel.value = "INR";
        renderAmountButtons();
        setTier(Number(c.dataset.tier));
        var d = document.getElementById("donate"); if (d) d.scrollIntoView({ behavior: "smooth" });
      });
    });
  }

  /* ---------- Amount buttons for the active currency ---------- */
  function renderAmountButtons() {
    var grid = $("#amountGrid");
    if (!grid) return;
    var labels = cfg.tierLabels || [];
    grid.innerHTML = state.cur.amounts.map(function (a, i) {
      return '<button class="amount-btn" type="button" data-tier="' + i + '" data-amount="' + a + '">' +
        '<span class="amount-btn__amt">' + fmt(a, state.cur) + '</span>' +
        '<span class="amount-btn__lbl">' + (labels[i] || "") + '</span></button>';
    }).join("");
    $$(".amount-btn", grid).forEach(function (b) {
      b.addEventListener("click", function () { setTier(Number(b.dataset.tier)); });
    });
    var sym = $("#curSymbol"); if (sym) sym.textContent = state.cur.symbol;
  }

  function setTier(i) {
    var a = state.cur.amounts[i];
    setAmount(a);
    var input = $("#customAmount"); if (input) input.value = a;
  }

  /* ---------- Core: set amount + sync everything ---------- */
  function setAmount(value, fromInput) {
    state.amount = sanitize(value, state.cur);

    $$(".amount-btn").forEach(function (b) {
      b.classList.toggle("is-active", Number(b.dataset.amount) === state.amount && state.cur.code === findCurrency($("#currencySel") ? $("#currencySel").value : state.cur.code).code);
    });
    if (!fromInput) { var input = $("#customAmount"); if (input) input.value = state.amount || ""; }

    var note = $("#amountNote");
    if (note) note.textContent = state.amount ? "✨ Thank you for your generosity ❤️" : "";

    var p = resolvePayment();

    var btn = $("#payBtn");
    if (btn) {
      btn.textContent = p.label;
      btn.setAttribute("href", p.enabled ? p.href : "#");
      btn.setAttribute("aria-disabled", p.enabled ? "false" : "true");
      btn.classList.toggle("btn--disabled", !p.enabled);
      if (p.newTab) { btn.setAttribute("target", "_blank"); btn.setAttribute("rel", "noopener noreferrer"); }
      else { btn.removeAttribute("target"); btn.removeAttribute("rel"); }
    }
    var hint = $("#payHint"); if (hint) hint.textContent = p.hint || "";
    var reason = $("#payReason");
    if (reason) { reason.textContent = p.reason || ""; reason.hidden = !p.reason; }

    // sticky bar
    var sticky = $("#stickyAmount"); if (sticky) sticky.textContent = state.amount ? fmt(state.amount, state.cur) : "";

    // method-specific QR + UPI-ID row
    var copyRow = $("#copyUpi");
    if (copyRow) copyRow.hidden = (p.method !== "upi");
    var qrLead = $("#qrLead");
    if (qrLead) qrLead.textContent = p.method === "upi"
      ? "On a laptop? Scan to pay with any UPI app."
      : (p.enabled ? "On a laptop? Scan this to open the secure donation page on your phone." : "");

    renderQR(p.enabled ? p.href : "");
  }

  /* ---------- QR of whatever the active payment link is ---------- */
  function renderQR(text) {
    var box = $("#qrCode");
    if (!box) return;
    box.innerHTML = "";
    if (!text || typeof QRCode === "undefined") { box.style.display = text ? "" : "none"; return; }
    box.style.display = "";
    try {
      new QRCode(box, { text: text, width: 240, height: 240, colorDark: "#0b3d2e", colorLight: "#ffffff", correctLevel: QRCode.CorrectLevel.M });
    } catch (e) { box.style.display = "none"; }
  }

  /* ---------- Copy UPI ID ---------- */
  function wireCopy() {
    var idText = $("#upiIdText"); if (idText) idText.textContent = pay.upiId || "—";
    var btn = $("#copyUpi"); if (!btn) return;
    btn.addEventListener("click", function () {
      var val = pay.upiId || "";
      var done = function () { var m = $("#copiedMsg"); if (m) { m.hidden = false; setTimeout(function () { m.hidden = true; }, 1800); } };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(val).then(done, done);
      else { var t = document.createElement("textarea"); t.value = val; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(t); done(); }
    });
  }

  /* ---------- Populate text from config ---------- */
  function hydrate() {
    var tel = (cfg.phone || "").replace(/\s+/g, "");
    var setHref = function (id, href, text) { var el = $(id); if (!el) return; el.href = href; if (text != null) el.textContent = text; };
    if (cfg.phone) { setHref("#phoneLink", "tel:" + tel, cfg.phone); setHref("#footerPhone", "tel:" + tel, "📞 " + cfg.phone); }
    if (cfg.email) { var w = $("#footerEmailWrap"); if (w) w.hidden = false; setHref("#footerEmail", "mailto:" + cfg.email, cfg.email); }
    var loc = $("#footerLocation"); if (loc) loc.textContent = cfg.location || "";
    var proj = $("#footerProject"); if (proj) proj.textContent = cfg.tagline || cfg.project || "";
    if (cfg.regNo) { var rw = $("#footerRegWrap"); if (rw) rw.hidden = false; var rg = $("#footerReg"); if (rg) rg.textContent = cfg.regNo; }
    if (cfg.instagramReel) { var r = $("#reelLink"); if (r) { r.href = cfg.instagramReel; r.rel = "noopener noreferrer"; } }

    // bank details
    var b = cfg.bank || {};
    if (b.accountNumber && (b.ifsc || b.swift)) {
      var det = $("#bankDetails"); if (det) det.hidden = false;
      var rows = [["Account name", b.accountName], ["Account no.", b.accountNumber], ["IFSC", b.ifsc], ["SWIFT/BIC", b.swift], ["Bank", b.bankName], ["Branch", b.branch]].filter(function (r) { return r[1]; });
      var list = $("#bankList"); if (list) list.innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + escapeHtml(r[1]) + "</dd>"; }).join("");
    }
    var y = $("#year"); if (y) y.textContent = new Date().getFullYear();
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }

  /* ---------- Custom amount input ---------- */
  function wireCustomAmount() {
    var input = $("#customAmount"); if (!input) return;
    input.addEventListener("input", function () { setAmount(input.value, true); });
    input.addEventListener("blur", function () { if (state.amount) input.value = state.amount; });
  }

  /* ---------- Nav + sticky donate ---------- */
  function wireScroll() {
    var nav = $("#nav"), sticky = $("#stickyDonate"), donate = $("#donate");
    var onScroll = function () {
      if (nav) nav.classList.toggle("is-scrolled", window.scrollY > 40);
      if (sticky && donate) {
        var r = donate.getBoundingClientRect();
        var past = window.scrollY > window.innerHeight * 0.6;
        var inDonate = r.top < window.innerHeight && r.bottom > 0;
        sticky.classList.toggle("is-visible", past && !inDonate);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  function wireReveal() {
    var targets = $$(".section__head, .about__copy, .about__media, .impact-card, .story, .donate__intro, .donate__card, .finalcta__inner");
    targets.forEach(function (t) { t.setAttribute("data-reveal", ""); });
    if (!("IntersectionObserver" in window)) { targets.forEach(function (t) { t.classList.add("is-in"); }); return; }
    var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); } }); }, { threshold: 0.12 });
    targets.forEach(function (t) { io.observe(t); });
  }

  function wireCounters() {
    var els = $$("[data-count]"); if (!els.length || !("IntersectionObserver" in window)) return;
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return; io.unobserve(e.target);
        var el = e.target, target = Number(el.dataset.count), t0 = null, dur = 1400;
        var step = function (ts) { if (!t0) t0 = ts; var p = Math.min((ts - t0) / dur, 1); el.textContent = Math.floor((1 - Math.pow(1 - p, 3)) * target).toLocaleString("en-IN"); if (p < 1) requestAnimationFrame(step); };
        requestAnimationFrame(step);
      });
    }, { threshold: 0.6 });
    els.forEach(function (el) { io.observe(el); });
  }

  function wireVideos() {
    var vids = $$("video[controls]");
    vids.forEach(function (v) { v.addEventListener("play", function () { vids.forEach(function (o) { if (o !== v && !o.paused) o.pause(); }); }); });
  }

  function init() {
    renderCurrencies();
    renderImpact();
    renderAmountButtons();
    hydrate();
    wireCopy();
    wireCustomAmount();
    wireScroll();
    wireReveal();
    wireCounters();
    wireVideos();
    setTier(cfg.defaultTier != null ? cfg.defaultTier : 2);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
