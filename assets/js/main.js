/* =====================================================================
   Ready To Serve Foundation — interactions
   Donations are handled entirely by an embedded Donorbox form (cards,
   Apple Pay, Google Pay, recurring, many currencies). No card data ever
   touches this site. External links use rel="noopener noreferrer".
   ===================================================================== */
(function () {
  "use strict";
  var cfg = window.RTS_CONFIG || {};
  var pay = cfg.payments || {};
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------- small DOM helpers ---------- */
  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (html != null) e.innerHTML = html;
    return e;
  }
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  var loaded = {};
  function loadScript(src) { if (loaded[src]) return; loaded[src] = true; var s = document.createElement("script"); s.src = src; s.async = true; document.head.appendChild(s); }
  function safeUrl(u, hosts) {
    if (!u) return null;
    try {
      var x = new URL(u, location.href);
      if (x.protocol !== "https:") return null;
      if (hosts && !hosts.some(function (h) { return x.hostname === h || x.hostname.endsWith("." + h); })) return null;
      return x.href;
    } catch (e) { return null; }
  }

  /* ---------- Donation method (Donorbox / Stripe) ---------- */
  function wallets() {
    return '<span class="wallets">' +
      '<span class="wallet wallet--apple"> Pay</span>' +
      '<span class="wallet wallet--gpay">G Pay</span>' +
      '<span class="wallet wallet--card">VISA</span>' +
      '<span class="wallet wallet--card">MC</span></span>';
  }
  function secureNote(provider) {
    return el("p", { class: "cardpay__secure" },
      '🔒 Secured by ' + provider + ' · your card details are entered on their PCI-compliant page and never touch this website.');
  }
  function donorboxEmbedUrl(u) {
    var s = safeUrl(u, ["donorbox.org"]); if (!s) return null;
    var x = new URL(s);
    if (!/^\/embed\//.test(x.pathname)) x.pathname = "/embed" + x.pathname; // /slug -> /embed/slug
    return x.href;
  }
  function renderCardMethod() {
    var box = $("#cardMethod"); if (!box) return false;
    box.innerHTML = "";

    // Donorbox embedded form (cards, Apple Pay, Google Pay, recurring, many currencies)
    var dboxEmbed = donorboxEmbedUrl(pay.donorboxUrl);
    if (dboxEmbed) {
      loadScript("https://donorbox.org/widget.js"); // auto-resizes the iframe
      var wrap = el("div", { class: "cardpay__embed" });
      wrap.appendChild(el("div", { class: "cardpay__embedhead" },
        '<span class="cardpay__embedtitle">' +
          '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 1 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>' +
          'Donate securely</span>' +
        '<span class="cardpay__embedbadge">Card · Apple Pay · Google Pay</span>'));
      var bodyEl = el("div", { class: "cardpay__embedbody" });
      bodyEl.appendChild(el("iframe", {
        src: dboxEmbed, name: "donorbox", title: "Donate securely", allow: "payment",
        allowpaymentrequest: "", seamless: "seamless", frameborder: "0", scrolling: "no",
        height: "900", width: "100%", style: "max-width:100%;min-width:250px;max-height:none!important"
      }));
      wrap.appendChild(bodyEl);
      box.appendChild(wrap);
      var note = secureNote("Donorbox");
      var openLink = safeUrl(pay.donorboxUrl, ["donorbox.org"]);
      if (openLink) note.innerHTML += ' · <a href="' + openLink + '" target="_blank" rel="noopener noreferrer">Trouble seeing the form? Open it in a new tab →</a>';
      box.appendChild(note);
      return true;
    }
    // Stripe Buy Button (embedded)
    if (pay.stripeBuyButtonId && pay.stripePublishableKey) {
      loadScript("https://js.stripe.com/v3/buy-button.js");
      var sb = el("stripe-buy-button");
      sb.setAttribute("buy-button-id", pay.stripeBuyButtonId);
      sb.setAttribute("publishable-key", pay.stripePublishableKey);
      box.appendChild(el("p", { class: "cardpay__title" }, "Donate by card · Apple Pay · Google Pay"));
      box.appendChild(sb);
      box.appendChild(secureNote("Stripe"));
      return true;
    }
    // Stripe Payment Link / generic hosted checkout
    var link = safeUrl(pay.stripePaymentLink, ["stripe.com"]) || safeUrl(pay.hostedDonateUrl, null);
    if (link) {
      box.appendChild(el("a", {
        class: "btn btn--primary btn--lg btn--block cardpay__btn",
        href: link, target: "_blank", rel: "noopener noreferrer"
      }, wallets() + '<span>Donate by card · Apple&nbsp;Pay · Google&nbsp;Pay</span>'));
      box.appendChild(secureNote("Stripe"));
      return true;
    }
    // Nothing configured -> secure preview
    var prev = el("div", { class: "cardpay__preview" });
    prev.appendChild(el("div", { class: "cardpay__head" }, wallets()));
    prev.appendChild(el("p", { class: "cardpay__title" }, "Card · Apple Pay · Google Pay"));
    prev.appendChild(el("p", { class: "cardpay__note" },
      "Ready to switch on. Add a <strong>Donorbox</strong> campaign (or Stripe link) in the settings and the secure donation form appears right here."));
    box.appendChild(prev);
    return false;
  }

  /* ---------- "Your impact" example cards ---------- */
  function renderImpact() {
    var grid = $("#impactGrid"); if (!grid) return;
    var items = cfg.impact || [];
    grid.innerHTML = items.map(function (it) {
      return '<button class="impact-card" type="button">' +
        '<div class="impact-card__amt">' + escapeHtml(it.amount) + '</div>' +
        '<div class="impact-card__lbl">' + escapeHtml(it.label) + '</div>' +
        '<div class="impact-card__cta">Give this →</div></button>';
    }).join("");
    $$(".impact-card", grid).forEach(function (c) {
      c.addEventListener("click", function () { var d = $("#donate"); if (d) d.scrollIntoView({ behavior: "smooth" }); });
    });
  }

  /* ---------- Accepted-currencies acknowledgment ---------- */
  function renderCurrencies() {
    var box = $("#curChips"); if (!box) return;
    var list = cfg.acceptedCurrencies || [];
    box.innerHTML = list.map(function (c) { return '<span class="cur-chip">' + escapeHtml(c) + "</span>"; }).join("");
  }

  /* ---------- Thank-you message after donating ----------
     Shown when a donor returns with ?donated=1 (or #thanks). Set your
     Donorbox campaign's "redirect after donation" to thank-you.html, or to
     this page with ?donated=1, to greet donors here. */
  function wireThankYou() {
    var overlay = $("#thanksOverlay"); if (!overlay) return;
    var show = /[?&]donated=1\b/.test(location.search) || location.hash === "#thanks";
    var open = function () {
      overlay.hidden = false; document.body.style.overflow = "hidden";
      var btn = $("#thanksClose"); if (btn) btn.focus();
    };
    var close = function () {
      overlay.hidden = true; document.body.style.overflow = "";
      if (location.hash === "#thanks" || /donated=1/.test(location.search)) {
        history.replaceState(null, "", location.pathname);
      }
    };
    if (show) open();
    var c = $("#thanksClose"); if (c) c.addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !overlay.hidden) close(); });
  }

  /* ---------- Populate text from config ---------- */
  function hydrate() {
    var tel = (cfg.phone || "").replace(/\s+/g, "");
    var setHref = function (id, href, text) { var e = $(id); if (!e) return; e.href = href; if (text != null) e.textContent = text; };
    if (cfg.phone) { setHref("#phoneLink", "tel:" + tel, cfg.phone); setHref("#footerPhone", "tel:" + tel, "📞 " + cfg.phone); }
    if (cfg.email) { var w = $("#footerEmailWrap"); if (w) w.hidden = false; setHref("#footerEmail", "mailto:" + cfg.email, cfg.email); }
    var loc = $("#footerLocation"); if (loc) loc.textContent = cfg.location || "";
    var proj = $("#footerProject"); if (proj) proj.textContent = cfg.tagline || cfg.project || "";
    if (cfg.regNo) { var rw = $("#footerRegWrap"); if (rw) rw.hidden = false; var rg = $("#footerReg"); if (rg) rg.textContent = cfg.regNo; }
    if (cfg.instagramReel) { var r = $("#reelLink"); if (r) { r.href = cfg.instagramReel; r.rel = "noopener noreferrer"; } }

    var b = cfg.bank || {};
    if (b.accountNumber && (b.ifsc || b.swift)) {
      var det = $("#bankDetails"); if (det) det.hidden = false;
      var rows = [["Account name", b.accountName], ["Account no.", b.accountNumber], ["IFSC", b.ifsc], ["SWIFT/BIC", b.swift], ["Bank", b.bankName], ["Branch", b.branch]].filter(function (r) { return r[1]; });
      var bl = $("#bankList"); if (bl) bl.innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + escapeHtml(r[1]) + "</dd>"; }).join("");
    }
    var y = $("#year"); if (y) y.textContent = new Date().getFullYear();
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
        var node = e.target, target = Number(node.dataset.count), t0 = null, dur = 1400;
        var step = function (ts) { if (!t0) t0 = ts; var p = Math.min((ts - t0) / dur, 1); node.textContent = Math.floor((1 - Math.pow(1 - p, 3)) * target).toLocaleString("en-IN"); if (p < 1) requestAnimationFrame(step); };
        requestAnimationFrame(step);
      });
    }, { threshold: 0.6 });
    els.forEach(function (node) { io.observe(node); });
  }

  function wireVideos() {
    var vids = $$("video[controls]");
    vids.forEach(function (v) { v.addEventListener("play", function () { vids.forEach(function (o) { if (o !== v && !o.paused) o.pause(); }); }); });
  }

  /* ---------- Privacy-friendly analytics ---------- */
  function loadAnalytics() {
    var a = cfg.analytics || {};
    if (/^G-[A-Z0-9]+$/i.test(a.googleAnalyticsId || "")) {
      loadScript("https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(a.googleAnalyticsId));
      window.dataLayer = window.dataLayer || [];
      window.gtag = function () { window.dataLayer.push(arguments); };
      window.gtag("js", new Date());
      window.gtag("config", a.googleAnalyticsId, { anonymize_ip: true });
    }
    if (a.plausibleDomain) {
      var p = document.createElement("script");
      p.defer = true; p.setAttribute("data-domain", a.plausibleDomain); p.src = "https://plausible.io/js/script.js";
      document.head.appendChild(p);
    }
    if (a.cloudflareToken) {
      var c = document.createElement("script");
      c.defer = true; c.src = "https://static.cloudflareinsights.com/beacon.min.js";
      c.setAttribute("data-cf-beacon", JSON.stringify({ token: a.cloudflareToken }));
      document.head.appendChild(c);
    }
  }

  function init() {
    loadAnalytics();
    renderCardMethod();
    renderImpact();
    renderCurrencies();
    hydrate();
    wireThankYou();
    wireScroll();
    wireReveal();
    wireCounters();
    wireVideos();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
