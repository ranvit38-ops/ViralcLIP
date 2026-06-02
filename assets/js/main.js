/* =====================================================================
   Ready To Serve Foundation — interactions & UPI donation logic
   ===================================================================== */
(function () {
  "use strict";
  var cfg = window.RTS_CONFIG || {};
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var state = { amount: cfg.defaultAmount || 500, note: "" };

  /* ---------- Helpers ---------- */
  function fmt(n) { return Number(n).toLocaleString("en-IN"); }

  /* Build a UPI deep link (works with GPay / PhonePe / Paytm / BHIM, etc.) */
  function upiLink(amount) {
    var p = new URLSearchParams();
    p.set("pa", cfg.upiId || "");                       // payee address
    p.set("pn", cfg.upiPayeeName || cfg.orgName || ""); // payee name
    p.set("cu", "INR");
    if (amount && amount > 0) p.set("am", String(amount));
    p.set("tn", "Donation to " + (cfg.orgName || "charity")); // note
    return "upi://pay?" + p.toString();
  }

  /* ---------- Render impact cards ---------- */
  function renderImpact() {
    var grid = $("#impactGrid");
    if (!grid || !cfg.presets) return;
    grid.innerHTML = cfg.presets.map(function (p) {
      return '<button class="impact-card" type="button" data-amount="' + p.amount + '">' +
        '<div class="impact-card__amt">₹' + fmt(p.amount) + '</div>' +
        '<div class="impact-card__lbl">' + p.label + '</div>' +
        '<div class="impact-card__cta">Give this →</div>' +
        '</button>';
    }).join("");
    $$(".impact-card", grid).forEach(function (c) {
      c.addEventListener("click", function () {
        setAmount(Number(c.dataset.amount));
        document.getElementById("donate").scrollIntoView({ behavior: "smooth" });
      });
    });
  }

  /* ---------- Render amount buttons in the card ---------- */
  function renderAmountButtons() {
    var grid = $("#amountGrid");
    if (!grid || !cfg.presets) return;
    grid.innerHTML = cfg.presets.map(function (p) {
      return '<button class="amount-btn" type="button" data-amount="' + p.amount + '">' +
        '<span class="amount-btn__amt">₹' + fmt(p.amount) + '</span>' +
        '<span class="amount-btn__lbl">' + p.label + '</span>' +
        '</button>';
    }).join("");
    $$(".amount-btn", grid).forEach(function (b) {
      b.addEventListener("click", function () { setAmount(Number(b.dataset.amount)); });
    });
  }

  /* ---------- Set / sync the chosen amount everywhere ---------- */
  function setAmount(amount, fromInput) {
    state.amount = amount && amount > 0 ? amount : 0;

    // find matching preset label
    var match = (cfg.presets || []).filter(function (p) { return p.amount === amount; })[0];
    state.note = match ? match.label : "";

    // highlight buttons
    $$(".amount-btn").forEach(function (b) {
      b.classList.toggle("is-active", Number(b.dataset.amount) === amount);
    });

    // custom input
    var input = $("#customAmount");
    if (input && !fromInput) input.value = amount > 0 ? amount : "";

    // labels
    var payAmt = $("#payAmount"); if (payAmt) payAmt.textContent = fmt(state.amount || 0);
    var sticky = $("#stickyAmount"); if (sticky) sticky.textContent = fmt(state.amount || 0);
    var noteEl = $("#amountNote");
    if (noteEl) noteEl.textContent = state.note ? "✨ " + state.note : (state.amount ? "Thank you for your generosity ❤️" : "");

    // pay button + QR
    var btn = $("#upiPayBtn");
    if (btn) {
      btn.href = upiLink(state.amount);
      btn.classList.toggle("is-disabled", !state.amount);
    }
    renderQR();
  }

  /* ---------- QR code (re-renders with the amount baked in) ---------- */
  var qrInstance = null;
  function renderQR() {
    var box = $("#qrCode");
    if (!box) return;
    if (typeof QRCode === "undefined") { box.parentElement.style.display = "none"; return; }
    box.innerHTML = "";
    qrInstance = new QRCode(box, {
      text: upiLink(state.amount),
      width: 240, height: 240,
      colorDark: "#0b3d2e", colorLight: "#ffffff",
      correctLevel: QRCode.CorrectLevel.M
    });
  }

  /* ---------- Copy UPI ID ---------- */
  function wireCopy() {
    var idText = $("#upiIdText");
    if (idText) idText.textContent = cfg.upiId || "—";
    var btn = $("#copyUpi");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var val = cfg.upiId || "";
      var done = function () {
        var m = $("#copiedMsg"); if (!m) return;
        m.hidden = false; setTimeout(function () { m.hidden = true; }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(val).then(done, done);
      } else {
        var t = document.createElement("textarea");
        t.value = val; document.body.appendChild(t); t.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(t); done();
      }
    });
  }

  /* ---------- Populate text from config ---------- */
  function hydrate() {
    var tel = (cfg.phone || "").replace(/\s+/g, "");
    var setHref = function (id, href, text) {
      var el = $(id); if (!el) return; el.href = href; if (text != null) el.textContent = text;
    };
    if (cfg.phone) {
      setHref("#phoneLink", "tel:" + tel, cfg.phone);
      setHref("#footerPhone", "tel:" + tel, "📞 " + cfg.phone);
    }
    if (cfg.email) {
      var w = $("#footerEmailWrap"); if (w) w.hidden = false;
      setHref("#footerEmail", "mailto:" + cfg.email, cfg.email);
    }
    var loc = $("#footerLocation"); if (loc) loc.textContent = cfg.location || "";
    var proj = $("#footerProject"); if (proj) proj.textContent = cfg.tagline || cfg.project || "";
    if (cfg.regNo) {
      var rw = $("#footerRegWrap"); if (rw) rw.hidden = false;
      var rg = $("#footerReg"); if (rg) rg.textContent = cfg.regNo;
    }
    setHref("#reelLink", cfg.instagramReel || "#");

    // optional card-donate button
    if (cfg.cardDonateUrl) {
      var cb = $("#cardDonateBtn");
      if (cb) { cb.hidden = false; cb.href = cfg.cardDonateUrl; cb.target = "_blank"; cb.rel = "noopener"; }
    }

    // optional bank details
    var b = cfg.bank || {};
    if (b.accountNumber && b.ifsc) {
      var det = $("#bankDetails"); if (det) det.hidden = false;
      var rows = [
        ["Account name", b.accountName],
        ["Account no.", b.accountNumber],
        ["IFSC", b.ifsc],
        ["Bank", b.bankName],
        ["Branch", b.branch]
      ].filter(function (r) { return r[1]; });
      var list = $("#bankList");
      if (list) list.innerHTML = rows.map(function (r) {
        return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>";
      }).join("");
    }

    var y = $("#year"); if (y) y.textContent = new Date().getFullYear();
  }

  /* ---------- Custom amount input ---------- */
  function wireCustomAmount() {
    var input = $("#customAmount");
    if (!input) return;
    input.addEventListener("input", function () {
      var v = parseInt(input.value, 10);
      setAmount(isNaN(v) ? 0 : v, true);
    });
  }

  /* ---------- Nav scroll state + sticky donate ---------- */
  function wireScroll() {
    var nav = $("#nav");
    var sticky = $("#stickyDonate");
    var donate = $("#donate");
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

  /* ---------- Reveal on scroll ---------- */
  function wireReveal() {
    var targets = $$(".section__head, .about__copy, .about__media, .impact-card, .story, .donate__intro, .donate__card, .finalcta__inner");
    targets.forEach(function (t) { t.setAttribute("data-reveal", ""); });
    if (!("IntersectionObserver" in window)) {
      targets.forEach(function (t) { t.classList.add("is-in"); }); return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
      });
    }, { threshold: 0.12 });
    targets.forEach(function (t) { io.observe(t); });
  }

  /* ---------- Animated stat counters ---------- */
  function wireCounters() {
    var els = $$("[data-count]");
    if (!els.length || !("IntersectionObserver" in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        var el = e.target, target = Number(el.dataset.count), start = 0, t0 = null, dur = 1400;
        var step = function (ts) {
          if (!t0) t0 = ts;
          var p = Math.min((ts - t0) / dur, 1);
          var val = Math.floor((1 - Math.pow(1 - p, 3)) * (target - start) + start);
          el.textContent = val.toLocaleString("en-IN");
          if (p < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      });
    }, { threshold: 0.6 });
    els.forEach(function (el) { io.observe(el); });
  }

  /* ---------- Stories: pause others on play ---------- */
  function wireVideos() {
    var vids = $$("video[controls]");
    vids.forEach(function (v) {
      v.addEventListener("play", function () {
        vids.forEach(function (o) { if (o !== v && !o.paused) o.pause(); });
      });
    });
  }

  /* ---------- Init ---------- */
  function init() {
    renderImpact();
    renderAmountButtons();
    hydrate();
    wireCopy();
    wireCustomAmount();
    wireScroll();
    wireReveal();
    wireCounters();
    wireVideos();
    setAmount(state.amount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
