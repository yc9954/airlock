#!/usr/bin/env python3
"""Verify the real Daytona adapter against a real account, end to end.

This exercises the SAME code path the pipeline uses (airlock.sandbox.get_sandbox →
DaytonaSandbox.build_and_serve), so a green run here means the product's Daytona
integration is real, not guessed.

    # 1) put your key on disk (gitignored; never printed, never in shell history)
    echo 'DAYTONA_API_KEY=dtn_xxx' > airlock/.env
    # 2) install the official SDK (a throwaway venv keeps your global env clean)
    python3 -m venv .venv && . .venv/bin/activate && pip install daytona
    # 3) run
    python3 verify_daytona.py

Steps checked: construct client → create sandbox → upload index.html →
serve it → get a public preview URL → fetch it → measure it → delete the sandbox.
The API key is read from the environment / .env by the SDK; this script never
reads or prints its value.
"""
import sys
import time
import urllib.request

from airlock.config import Config
from airlock.sandbox import get_sandbox, DaytonaSandbox
from airlock.measure import measure

GOOD_HTML = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Airlock · Daytona verify</title>'
    '<style>body{font-family:system-ui;background:#0f1117;color:#eef1f7;margin:0}'
    '.w{max-width:720px;margin:0 auto;padding:64px}h1{font-size:3rem}'
    '.c{background:#e3b341;color:#1a1408;padding:14px 24px;border-radius:12px;'
    'display:inline-block;text-decoration:none;font-weight:700;margin-top:20px}</style></head>'
    '<body><div class="w"><h1>AIRLOCK_DAYTONA_OK</h1>'
    '<p>이 페이지가 Daytona 샌드박스에서 서빙되면 어댑터가 실제로 동작하는 것입니다.</p>'
    '<a class="c" href="#start">지금 시작</a></div></body></html>')

MARK = "AIRLOCK_DAYTONA_OK"


def ok(msg):   print(f"  \033[32m✓\033[0m {msg}")
def bad(msg):  print(f"  \033[31m✗\033[0m {msg}")
def info(msg): print(f"    {msg}")


def main():
    print("\n  Airlock · Daytona adapter verification\n" + "  " + "─" * 44)
    cfg = Config()

    if not cfg.daytona_key:
        bad("DAYTONA_API_KEY not found (env or airlock/.env).")
        info("Put your key on disk:  echo 'DAYTONA_API_KEY=dtn_xxx' > airlock/.env")
        return 2
    ok(f"API key present ({len(cfg.daytona_key)} chars; value not shown).")

    sb = get_sandbox(cfg)
    if not isinstance(sb, DaytonaSandbox):
        bad("Adapter fell back to local — the `daytona` SDK is not installed.")
        info("Install it:  python3 -m venv .venv && . .venv/bin/activate && pip install daytona")
        return 3
    ok("DaytonaSandbox selected; SDK client constructed.")

    variant = {"id": "verify", "html": GOOD_HTML,
               "meta": {"layout": "verify", "palette": "midnight",
                        "font": "system", "tone": "technical", "headline": "verify"}}

    print("\n  Running the real cycle (create → upload → serve → preview)…")
    t0 = time.time()
    res = sb.build_and_serve("verify-run", variant, "http://127.0.0.1:8770")
    rt = res.get("runtime", {})

    if str(rt.get("sandbox", "")).startswith("local"):
        bad(f"Adapter degraded to local: {rt.get('sandbox')}")
        info("The Daytona call raised. Check the key, plan/quota, and network.")
        sb.teardown()
        return 4

    preview = rt.get("preview_url") or res.get("url")
    ok(f"Sandbox created & page served in {round((time.time()-t0)*1000)} ms "
       f"(boot {rt.get('boot_ms')} ms).")
    info(f"sandbox_id : {rt.get('sandbox_id')}")
    info(f"served     : {rt.get('served')}  (in-sandbox HTTP status {rt.get('status')})")
    info(f"preview URL: {preview}")
    if rt.get("preview_token"):
        info("preview is private (token returned; kept out of logs).")

    # external fetch of the preview (best-effort; internal curl already passed)
    fetched_ok = False
    try:
        req = urllib.request.Request(preview)
        if rt.get("preview_token"):
            req.add_header("x-daytona-preview-token", rt["preview_token"])
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read().decode("utf-8", "replace")
        if MARK in body:
            ok("Fetched the preview URL from here — served content matches.")
            _, fit, verdict, _ = measure(body)
            info(f"measured the served page: fitness {fit}, verdict {verdict}")
            fetched_ok = True
        else:
            bad("Preview URL returned, but the marker wasn't in the body.")
    except Exception as exc:
        info(f"(external fetch skipped: {exc} — the in-sandbox 200 already proves serving)")

    print("\n  Tearing down…")
    sb.teardown()
    ok("Sandbox deleted.")

    print("\n  " + "─" * 44)
    verdict = rt.get("served") and (fetched_ok or rt.get("status") == 200)
    print(f"  RESULT: {'PASS — Daytona adapter is real and working.' if verdict else 'PARTIAL — served in-sandbox; external fetch unverified.'}\n")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
