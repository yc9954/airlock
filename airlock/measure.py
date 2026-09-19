"""Real, measured fitness for a generated web page.

This is the part that must not be faked. Every signal below is computed from the
actual artifact — parsed, counted, checked — not asked of a model. A bad fitness
function selects confident garbage; the whole thesis is that fitness is *measured*
by really looking at what was produced, cheaply, at swarm scale.

Fitness is **continuous** (0..100) on purpose: evolution needs headroom to climb.
Hard rules gate survival (parse / body / CTA / viewport); everything else is a
graded quality term, so better trait combinations score measurably higher and the
best-fitness curve rises generation over generation instead of pinning at 100.
"""
import re
from html.parser import HTMLParser

HARD = ("parses", "has_body", "has_cta", "has_viewport")


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}
        self.classes = []
        self.has_body = self.has_viewport = self.has_lang = False
        self.has_nav = self.has_footer = self.has_header = False
        self.sections = self.feat = self.imgs = self.imgs_with_alt = 0
        self.h1 = self.buttons = self.links = 0
        self.title = ""
        self._in_title = False
        self.inline_style_bytes = self.media_queries = self.rel_units = self.abs_px = 0
        self._colors = []
        self.parse_error = False

    def handle_starttag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if cls:
            self.classes.append(cls)
            if "feat" in cls or "card" in cls:
                self.feat += 1
        if tag == "body":
            self.has_body = True
        if tag == "html" and a.get("lang"):
            self.has_lang = True
        if tag == "nav":
            self.has_nav = True
        if tag == "footer":
            self.has_footer = True
        if tag in ("header",):
            self.has_header = True
        if tag == "section":
            self.sections += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta" and a.get("name", "").lower() == "viewport":
            self.has_viewport = True
        if tag == "img":
            self.imgs += 1
            if a.get("alt") is not None:
                self.imgs_with_alt += 1
        if tag == "h1":
            self.h1 += 1
        if tag == "button":
            self.buttons += 1
        if tag == "a":
            self.links += 1
        self._account_css(a.get("style", "") or "")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
        if "{" in data and "}" in data and ":" in data:
            self.inline_style_bytes += len(data)
            self.media_queries += len(re.findall(r"@media", data))
            self._account_css(data)

    def _account_css(self, css):
        if not css:
            return
        self.rel_units += len(re.findall(r"\d*\.?\d+(rem|em|vw|vh|%|ch)", css))
        self.abs_px += len(re.findall(r"\d+px", css))
        self._colors += re.findall(r"#[0-9a-fA-F]{3,6}", css)

    def error(self, message):
        self.parse_error = True


# English needs word boundaries; Korean is agglutinative (시작하기, 무료로) so the
# stem is matched as a substring — \b would miss it and wrongly cull the page.
_CTA_EN = re.compile(r"\b(start|sign\s?up|get\s|buy|try|launch|join|demo|book)\b", re.I)
_CTA_KO = re.compile(r"(가입|시작|구매|무료|신청|문의|등록|체험|둘러보|받기|만들|시도|예약)")


def _cta_present(scan, html):
    if scan.buttons > 0:
        return True
    for m in re.finditer(r"(?is)<(a|button)[^>]*>(.*?)</\1>", html):
        txt = re.sub(r"<[^>]+>", " ", m.group(2))
        if _CTA_EN.search(txt) or _CTA_KO.search(txt):
            return True
    return False


def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(colors):
    lums = [l for l in (_lum(c) for c in colors) if l is not None]
    if len(lums) < 2:
        return None
    hi, lo = max(lums), min(lums)
    return (hi + 0.05) / (lo + 0.05)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def measure(html):
    """Return (signals, fitness 0..100, verdict 'pass'|'fail', reasons)."""
    s = _Scan()
    try:
        s.feed(html or "")
    except Exception:
        s.parse_error = True

    weight_kb = round(len(html.encode("utf-8")) / 1024, 1)
    node_count = sum(s.tags.values())
    contrast = _contrast(s._colors)
    responsive = s.media_queries > 0 or s.rel_units > s.abs_px

    a11y_issues = 0
    if not s.has_lang:
        a11y_issues += 1
    if s.imgs and s.imgs_with_alt < s.imgs:
        a11y_issues += 1
    if s.h1 != 1:
        a11y_issues += 1
    if contrast is not None and contrast < 3.0:
        a11y_issues += 1

    signals = {
        "parses": not s.parse_error and node_count > 3,
        "has_body": s.has_body,
        "has_cta": _cta_present(s, html or ""),
        "has_viewport": s.has_viewport,
        "responsive": responsive,
        "weight_kb": weight_kb,
        "nodes": node_count,
        "a11y_issues": a11y_issues,
        "contrast": round(contrast, 1) if contrast else None,
        "has_title": bool(s.title),
        "sections": s.sections,
        "features": s.feat,
        "semantic": sum([s.has_nav, s.has_header or s.h1 > 0, s.sections > 0, s.has_footer]),
    }

    reasons = []
    for rule in HARD:
        if not signals.get(rule):
            reasons.append(f"hard:{rule}")
    if weight_kb > 220:
        reasons.append("heavy")
    if a11y_issues >= 3:
        reasons.append("a11y")
    verdict = "pass" if not any(r.startswith("hard:") for r in reasons) else "fail"

    # ---- continuous quality (graded; full marks are hard, so evolution climbs) ----
    q = 0.0
    q += 12 * (1 if responsive else 0)                          # responsive
    q += 10 * _clamp((200 - weight_kb) / 180)                   # lightness
    q += 14 * _clamp(1 - a11y_issues / 4)                       # a11y
    q += 14 * _clamp(((contrast or 1) - 1) / 6)                 # contrast → 7:1
    q += 14 * _clamp(signals["semantic"] / 4)                   # nav/hero/section/footer
    q += 16 * _clamp(signals["features"] / 5)                   # feature richness (needs 5)
    q += 6 * _clamp((signals["sections"] - 1) / 2)             # richer structure (pricing etc.)
    q += 4 * (1 if signals["has_title"] else 0)                 # title
    q += 10 * (1 if signals["has_cta"] else 0)                  # CTA

    fitness = q if verdict == "pass" else max(0.0, q - 40)
    fitness = round(max(0.0, min(100.0, fitness)), 1)
    return signals, fitness, verdict, reasons
