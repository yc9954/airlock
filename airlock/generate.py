"""Generate many candidate landing pages, cheaply — and *evolve* them.

Offline, a local synthesizer turns a genome {layout, palette, font, tone,
features, pricing, responsive} into a genuinely different, self-contained page.
Generation 0 is random (a controlled fraction seeded with real defects, so
pruning has honest work). Later generations are *bred* from the previous
generation's elites — crossover two genomes, mutate one gene — with the defect
rate decaying each generation. Because measure.py scores quality continuously,
better genomes score higher and the best/mean fitness climbs generation over
generation. That rising curve is the product's own proof.

Set AIRLOCK_LLM_BASE/KEY to swap the synthesizer for a real open model.
"""
import json
import random
import re
import urllib.request

PALETTES = [
    {"name": "midnight", "bg": "#0f1117", "fg": "#eef1f7", "accent": "#e3b341", "muted": "#8b93a7"},
    {"name": "paper", "bg": "#faf7f0", "fg": "#1c1a17", "accent": "#c2410c", "muted": "#6b6357"},
    {"name": "mint", "bg": "#f2fbf7", "fg": "#0c2a22", "accent": "#0f9d76", "muted": "#5c7d73"},
    {"name": "royal", "bg": "#101024", "fg": "#eae8ff", "accent": "#7c6cff", "muted": "#8a86b8"},
    {"name": "sunset", "bg": "#1a1013", "fg": "#fdeef0", "accent": "#f0567a", "muted": "#a9868f"},
    {"name": "steel", "bg": "#0d1618", "fg": "#e6f0f2", "accent": "#4fb7dd", "muted": "#7d949a"},
    {"name": "slate", "bg": "#12141a", "fg": "#e8ebf2", "accent": "#c9b074", "muted": "#868da0"},
]
FONTS = [
    {"name": "grotesque", "stack": "'Helvetica Neue',Arial,sans-serif", "disp": "800"},
    {"name": "serif", "stack": "Georgia,'Times New Roman',serif", "disp": "700"},
    {"name": "mono", "stack": "'SF Mono','IBM Plex Mono',ui-monospace,monospace", "disp": "600"},
    {"name": "system", "stack": "system-ui,-apple-system,sans-serif", "disp": "700"},
]
LAYOUTS = ["centered", "split", "minimal", "bento"]
TONES = ["bold", "friendly", "technical"]
DEFECTS = ["no_viewport", "no_cta", "low_contrast", "no_alt", "no_lang", "heavy", "broken"]

HEADLINES = {
    "bold": ["{s}, 이제 다르게.", "{s}를 압도하라.", "{s}의 새로운 기준."],
    "friendly": ["{s}, 쉽게 시작해요", "당신을 위한 {s}", "{s}를 함께 만들어요"],
    "technical": ["{s} 인프라, 프로덕션 급", "{s}를 위한 런타임", "{s} at scale"],
}
SUBS = {"bold": "군더더기 없이, 결과로 증명합니다.",
        "friendly": "복잡한 설정 없이 몇 분이면 충분해요.",
        "technical": "격리 실행 · 실측 검증 · 즉시 배포까지 한 번에."}
CTAS = {"bold": "지금 시작", "friendly": "무료로 시작하기", "technical": "Get Started"}
FEATURE_POOL = [
    ("속도", "경쟁자보다 10배 빠르게"), ("신뢰", "실측으로 증명된 품질"),
    ("확장", "무한히 늘어나는 규모"), ("격리", "일회용 샌드박스 실행"),
    ("측정", "빌드·응답·속도 자동 채점"), ("배포", "챔피언만 즉시 라이브"),
    ("협업", "팀과 실시간으로"), ("안심", "언제든 되돌리기")]


def _subject(prompt):
    m = re.search(r"(?:for|위한|을 위한|의)\s+([A-Za-z가-힣0-9 ]{2,30})", prompt or "")
    if m:
        return m.group(1).strip()
    words = re.findall(r"[A-Za-z가-힣0-9]+", prompt or "")
    return " ".join(words[:3]) if words else "Your Product"


def random_genome():
    return {"layout": random.choice(LAYOUTS), "palette": random.choice(PALETTES),
            "font": random.choice(FONTS), "tone": random.choice(TONES),
            "features": random.randint(2, 5), "pricing": random.random() < 0.5,
            "responsive": random.random() < 0.7}


def crossover(a, b):
    return {k: random.choice((a[k], b[k])) for k in a}


def mutate(g):
    g = dict(g)
    gene = random.choice(list(g))
    if gene == "features":
        g["features"] = max(2, min(5, g["features"] + random.choice((-1, 1))))
    elif gene == "pricing":
        g["pricing"] = not g["pricing"]
    elif gene == "responsive":
        g["responsive"] = True if random.random() < 0.8 else not g["responsive"]  # bias good
    else:
        g[gene] = random.choice({"layout": LAYOUTS, "palette": PALETTES,
                                 "font": FONTS, "tone": TONES}[gene])
    return g


def _css(p, f, layout):
    return f"""
    :root{{--bg:{p['bg']};--fg:{p['fg']};--ac:{p['accent']};--mu:{p['muted']}}}
    *{{box-sizing:border-box}} html,body{{margin:0}}
    body{{background:var(--bg);color:var(--fg);font-family:{f['stack']};line-height:1.6}}
    .wrap{{max-width:1000px;margin:0 auto;padding:clamp(24px,5vw,64px)}}
    nav{{display:flex;justify-content:space-between;align-items:center;padding:18px 0}}
    .logo{{font-weight:{f['disp']};letter-spacing:-.02em;font-size:1.1rem}} .logo b{{color:var(--ac)}}
    .pill{{border:1px solid color-mix(in srgb,var(--fg) 25%,transparent);border-radius:999px;padding:8px 16px;font-size:.85rem;text-decoration:none;color:var(--fg)}}
    h1{{font-weight:{f['disp']};font-size:clamp(2rem,6vw,4rem);line-height:1.05;letter-spacing:-.03em;margin:.2em 0}}
    .sub{{color:var(--mu);font-size:clamp(1rem,2.4vw,1.25rem);max-width:52ch}}
    .cta{{display:inline-block;background:var(--ac);color:var(--bg);font-weight:700;padding:14px 26px;border-radius:12px;text-decoration:none;margin-top:22px}}
    .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:clamp(30px,6vw,64px)}}
    .feat{{border:1px solid color-mix(in srgb,var(--fg) 14%,transparent);border-radius:14px;padding:20px;background:color-mix(in srgb,var(--fg) 4%,transparent)}}
    .feat h3{{margin:0 0 6px;color:var(--ac);font-size:1.05rem}} .feat p{{margin:0;color:var(--mu);font-size:.92rem}}
    .price{{margin-top:40px;border-top:1px solid color-mix(in srgb,var(--fg) 12%,transparent);padding-top:24px}}
    footer{{margin-top:clamp(30px,6vw,64px);color:var(--mu);font-size:.85rem;border-top:1px solid color-mix(in srgb,var(--fg) 12%,transparent);padding-top:20px}}
    {"@media (max-width:640px){.grid{grid-template-columns:1fr}}" if True else ""}
    {"" if layout != 'centered' else ".wrap{text-align:center}.sub{margin:0 auto}"}
    {"" if layout != 'minimal' else ".grid,.price{display:none}h1{font-size:clamp(2.4rem,9vw,6rem)}"}
    """


def render(genome, subject, defects):
    p, f, layout, tone = genome["palette"], genome["font"], genome["layout"], genome["tone"]
    css = _css(p, f, layout)
    if not genome["responsive"] and "no_viewport" not in defects:
        css = css.replace("clamp(2rem,6vw,4rem)", "40px").replace("clamp(1rem,2.4vw,1.25rem)", "18px")
        css = re.sub(r"@media[^}]*\{[^}]*\}\}?", "", css)   # strip responsiveness
    if "low_contrast" in defects:
        css = css.replace(p["accent"], p["bg"])
    headline = random.choice(HEADLINES[tone]).format(s=subject)
    viewport = "" if "no_viewport" in defects else '<meta name="viewport" content="width=device-width,initial-scale=1">'
    lang = "" if "no_lang" in defects else ' lang="ko"'
    cta = "" if "no_cta" in defects else f'<a class="cta" href="#start">{CTAS[tone]}</a>'
    feats = "".join(f'<div class="feat"><h3>{t}</h3><p>{d}</p></div>'
                    for t, d in random.sample(FEATURE_POOL, genome["features"]))
    grid = "" if layout == "minimal" else f'<section class="grid">{feats}</section>'
    pricing = "" if not genome["pricing"] or layout == "minimal" else \
        f'<section class="price"><h3 style="color:var(--ac)">₩0 부터</h3><p class="sub">필요할 때만 지불하세요.</p></section>'
    bloat = ("<!-- " + "x" * 30000 + "-->") if "heavy" in defects else ""  # ~30KB > weight cap, cheap to measure
    broken = "<div><span>" if "broken" in defects else ""
    return (f'<!doctype html><html{lang}><head><meta charset="utf-8">{viewport}'
            f'<title>{subject}</title><style>{css}</style></head><body>{broken}'
            f'<div class="wrap"><nav><div class="logo">{subject.split()[0]}<b>.</b></div>'
            f'<a class="pill" href="#">로그인</a></nav>'
            f'<header><h1>{headline}</h1><p class="sub">{SUBS[tone]}</p>{cta}</header>'
            f'{grid}{pricing}<footer>© 2026 {subject}. Built for the airlock.</footer></div>'
            f'{bloat}</body></html>')


class LocalGenerator:
    label = "local-synth"

    def __init__(self, cfg):
        self.cfg = cfg

    def generate(self, prompt, n, gen=0, elites=None, emit=None):
        subject = _subject(prompt)
        defect_rate = self.cfg.defect_rate * (0.5 ** gen)      # decays each generation
        out = []
        for i in range(n):
            html = None
            if elites and i < len(elites):
                genome = elites[i]["genome"]          # elitism: carry the best forward
                html = elites[i].get("html")          # unchanged, so best never regresses
            elif elites:
                a, b = random.choice(elites), random.choice(elites)
                genome = mutate(crossover(a["genome"], b["genome"]))
            else:
                genome = random_genome()
            defects = []
            if html is None:
                if random.random() < defect_rate:
                    defects = random.sample(DEFECTS, k=random.randint(1, 2))
                html = render(genome, subject, defects)
            v = {"id": f"g{gen}v{i:02d}", "gen": gen, "html": html, "genome": genome,
                 "meta": {"layout": genome["layout"], "palette": genome["palette"]["name"],
                          "font": genome["font"]["name"], "tone": genome["tone"],
                          "features": genome["features"], "headline": subject, "defects": defects}}
            out.append(v)
            if emit:
                emit(v)
        return out


class LLMGenerator:
    label = "LLM"

    def __init__(self, cfg):
        self.cfg = cfg

    def _one(self, prompt, seed, hint=""):
        sys = ("You are a senior front-end designer. Output ONE complete, self-contained "
               "HTML document for a landing page (inline CSS, responsive, accessible: lang, "
               "viewport, one h1, a clear CTA button, alt text). Return ONLY the HTML.")
        body = json.dumps({"model": self.cfg.llm_model, "temperature": 0.5 + (seed % 5) * 0.1,
                           "messages": [{"role": "system", "content": sys},
                                        {"role": "user", "content": f"Landing page for: {prompt}. Variant #{seed}. {hint}"}]}).encode()
        req = urllib.request.Request(self.cfg.llm_base.rstrip("/") + "/chat/completions",
                                     data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.cfg.llm_key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            text = json.loads(r.read())["choices"][0]["message"]["content"]
        m = re.search(r"(?is)<(?:!doctype|html).*", text)
        return m.group(0) if m else text

    def generate(self, prompt, n, gen=0, elites=None, emit=None):
        hint = "Improve clarity, contrast, and structure over prior attempts." if gen else ""
        out = []
        for i in range(n):
            try:
                html = self._one(prompt, gen * 100 + i, hint)
            except Exception:
                html = f"<!-- gen error --><html><body>variant {i} failed</body></html>"
            v = {"id": f"g{gen}v{i:02d}", "gen": gen, "html": html,
                 "genome": random_genome(),
                 "meta": {"layout": "llm", "palette": "llm", "font": "llm", "tone": "llm",
                          "features": 0, "headline": _subject(prompt), "defects": []}}
            out.append(v)
            if emit:
                emit(v)
        return out


def get_generator(cfg):
    if cfg.llm_base and cfg.llm_key:
        return LLMGenerator(cfg)
    return LocalGenerator(cfg)
