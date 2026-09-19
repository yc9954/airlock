"""Build and serve each variant in isolation, then read runtime signals off it.

Offline, "the sandbox" is honest for this domain: a landing page's artifact *is*
the runnable thing, so we write it to the run directory and the Airlock server
serves it — the static structural fitness in measure.py is the real signal.

With DAYTONA_API_KEY set (and the `daytona` SDK installed), each variant is
written into a real Daytona sandbox that serves it, and we attach true runtime
signals — a public preview URL, the HTTP status of the served page, and how long
the sandbox took to come up. This is the seam AlphaEvolve leaves to you (running
candidates in isolation) and what Daytona's <90ms disposable computers make cheap.

The Daytona path uses the official SDK verbatim:
    Daytona() → create() → fs.upload_file(bytes, path)
              → process.exec(...) → get_preview_link(port).url → delete()
"""
import os
import time


class LocalSandbox:
    label = "local-render"

    def __init__(self, cfg):
        self.cfg = cfg

    def build_and_serve(self, run_id, variant, base_url):
        vdir = os.path.join(self.cfg.work_dir, run_id, "variants", variant["id"])
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, "index.html"), "w", encoding="utf-8") as f:
            f.write(variant["html"])
        return {
            "url": f"{base_url}/runs/{run_id}/variants/{variant['id']}/index.html",
            "runtime": {"served": True, "status": 200, "sandbox": "local"},
        }

    def teardown(self):
        pass


# ---- serve a static page inside a sandbox and confirm it answers ----
SERVE = ("bash -lc 'cd /home/daytona/site 2>/dev/null || cd ~/site; "
         "(nohup python3 -m http.server {port} >/tmp/airlock-srv.log 2>&1 &) ; "
         "for i in $(seq 1 20); do "
         "code=$(curl -s -o /dev/null -w %{{http_code}} http://localhost:{port}/ || true); "
         "[ \"$code\" = 200 ] && break; sleep 0.25; done; echo $code'")


class DaytonaSandbox(LocalSandbox):
    label = "Daytona"
    PORT = 8080

    def __init__(self, cfg):
        super().__init__(cfg)
        from daytona import Daytona, DaytonaConfig      # raises if SDK missing
        self.client = Daytona(DaytonaConfig(api_key=cfg.daytona_key)) if cfg.daytona_key \
            else Daytona()
        self.sandboxes = {}                             # variant_id -> sandbox handle

    def build_and_serve(self, run_id, variant, base_url):
        local = super().build_and_serve(run_id, variant, base_url)   # keep a local copy too
        t0 = time.time()
        try:
            sb = self.client.create()
            self.sandboxes[variant["id"]] = sb
            sb.fs.upload_file(variant["html"].encode("utf-8"), "site/index.html")
            resp = sb.process.exec(SERVE.format(port=self.PORT))
            status = (getattr(resp, "result", "") or "").strip().splitlines()[-1:] or ["?"]
            status = status[0].strip()
            link = sb.get_preview_link(self.PORT)
            preview = getattr(link, "url", None)
            # Keep local['url'] for the iframe (byte-identical artifact, always
            # embeddable); the real Daytona preview is private (token) and can't be
            # iframed, so it travels as proof in runtime, shown in the log/badges.
            local["runtime"] = {
                "served": status == "200", "status": int(status) if status.isdigit() else status,
                "sandbox": "daytona", "sandbox_id": getattr(sb, "id", None),
                "preview_url": preview, "preview_token": getattr(link, "token", None),
                "boot_ms": round((time.time() - t0) * 1000)}
        except Exception as exc:                         # degrade to local, never crash a run
            local["runtime"] = {"served": True, "status": 200,
                                "sandbox": f"local (daytona error: {exc})"}
        return local

    def teardown(self):
        """Delete every sandbox this run created. Call when finalists are done —
        in a live demo you would delete the culled ones right after pruning and
        keep only the finalists' sandboxes alive for the human to click through."""
        for vid, sb in list(self.sandboxes.items()):
            try:
                (getattr(sb, "delete", None) or (lambda: self.client.delete(sb)))()
            except Exception:
                pass
            self.sandboxes.pop(vid, None)


def get_sandbox(cfg):
    if cfg.daytona_key:
        try:
            return DaytonaSandbox(cfg)
        except Exception as exc:
            print(f"  [sandbox] Daytona unavailable ({exc}); using local render. "
                  f"Install the SDK with `pip install daytona`.")
    return LocalSandbox(cfg)
