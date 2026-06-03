#!/usr/bin/env python3
"""Assemble a fully self-contained index.html (inline CSS/JS/QR + base64 posters
and pre-rendered UI) from the source parts, so the site renders and works even
when opened as a lone file with no asset folder and no JavaScript."""
import base64, pathlib, re

root = pathlib.Path(__file__).parent
read = lambda p: (root / p).read_text(encoding="utf-8")

css      = read("assets/css/styles.css")
qrcode   = read("assets/js/qrcode.min.js")
config   = read("assets/js/config.js")
main     = read("assets/js/main.js")
html     = read("index.src.html")

def datauri(p):
    b = (root / p).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode()

posters = {
    "assets/img/poster-celebration.jpg": datauri("assets/img/poster-celebration.jpg"),
    "assets/img/poster-testimonial.jpg": datauri("assets/img/poster-testimonial.jpg"),
    "assets/img/poster-clip1.jpg":       datauri("assets/img/poster-clip1.jpg"),
    "assets/img/poster-clip2.jpg":       datauri("assets/img/poster-clip2.jpg"),
}

# 1) inline poster images
for path, uri in posters.items():
    html = html.replace('poster="%s"' % path, 'poster="%s"' % uri)
# og:image -> keep relative (fine for hosting)

# 2) inline stylesheet
html = html.replace(
    '<link rel="stylesheet" href="assets/css/styles.css" />',
    "<style>\n%s\n</style>" % css,
)

# 3) inline scripts (config first, then QR engine, then app logic)
scripts_block = (
    "  <!-- ============================================================\n"
    "       EDIT YOUR CHARITY / PAYMENT DETAILS IN THE BLOCK BELOW.\n"
    "       This is the only thing you need to change. Replace upiId with\n"
    "       the foundation's VERIFIED UPI ID so money lands in their account.\n"
    "       ============================================================ -->\n"
    "  <script>\n%s\n  </script>\n"
    "  <!-- QR engine (inlined so the QR works offline / anywhere) -->\n"
    "  <script>%s</script>\n"
    "  <!-- App logic (inlined) -->\n"
    "  <script>\n%s\n  </script>\n"
) % (config, qrcode, main)

html = re.sub(
    r'  <!-- QR library.*?<script src="assets/js/main\.js"></script>',
    lambda m: scripts_block,   # function repl => no backslash interpretation
    html,
    flags=re.S,
)

# 4) pre-render the donation amount buttons + impact cards so the UI is fully
#    visible even before/without JavaScript (JS re-renders identically + wires events)
presets = [
    (200,  "A day of meals for one elder"),
    (500,  "Medicines for a week"),
    (1000, "Feed an elder for a month"),
    (5000, "Sponsor a birthday celebration"),
]
inr = lambda n: format(n, ",d")  # 1000 -> 1,000

amount_btns = "\n".join(
    '          <button class="amount-btn%s" type="button" data-amount="%d">'
    '<span class="amount-btn__amt">₹%s</span>'
    '<span class="amount-btn__lbl">%s</span></button>'
    % (" is-active" if a == 1000 else "", a, inr(a), lbl)
    for a, lbl in presets
)
impact_cards = "\n".join(
    '        <button class="impact-card" type="button" data-amount="%d">'
    '<div class="impact-card__amt">₹%s</div>'
    '<div class="impact-card__lbl">%s</div>'
    '<div class="impact-card__cta">Give this →</div></button>'
    % (a, inr(a), lbl)
    for a, lbl in presets
)

html = html.replace("          <!-- preset buttons injected by JS -->", amount_btns)
html = html.replace("        <!-- filled by JS from config presets -->", impact_cards)
# default labels for no-JS view
html = html.replace('<span id="payAmount">1000</span>', '<span id="payAmount">1,000</span>')
html = html.replace('<span id="stickyAmount">1000</span>', '<span id="stickyAmount">1,000</span>')

(root / "index.html").write_text(html, encoding="utf-8")
print("Built self-contained index.html: %d KB" % (len((root/'index.html').read_bytes())//1024))
