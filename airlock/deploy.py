"""Ship the chosen champion. Local by default; DNSimple when configured.

What passes the airlock goes live. Offline, that means writing the champion to a
stable path the local server exposes. With a DNSimple token, it also provisions a
real subdomain record pointing at the host you deploy to (hosting itself is your
own box / a static host — DNSimple is the DNS/records layer, and that is exactly
the seam it fills here).
"""
import json
import os
import re
import urllib.request


def _slug(text, n=24):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "champion").strip("-")


class LocalDeployer:
    label = "local"

    def __init__(self, cfg):
        self.cfg = cfg

    def deploy(self, run_id, variant, base_url):
        """Champion HTML already lives under the run dir; expose it at a stable URL."""
        champ_dir = os.path.join(self.cfg.work_dir, run_id, "champion")
        os.makedirs(champ_dir, exist_ok=True)
        with open(os.path.join(champ_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(variant["html"])
        return {
            "url": f"{base_url}/runs/{run_id}/champion/index.html",
            "provider": "local",
            "note": "served from the Airlock host (offline deploy)",
        }


class DNSimpleDeployer(LocalDeployer):
    label = "DNSimple"

    def deploy(self, run_id, variant, base_url):
        local = super().deploy(run_id, variant, base_url)
        sub = _slug(variant.get("meta", {}).get("headline") or run_id)
        fqdn = f"{sub}.{self.cfg.dnsimple_domain}"
        api = (f"https://api.dnsimple.com/v2/{self.cfg.dnsimple_account}"
               f"/zones/{self.cfg.dnsimple_domain}/records")
        body = json.dumps({"name": sub, "type": "CNAME",
                           "content": self.cfg.host, "ttl": 3600}).encode()
        req = urllib.request.Request(api, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.cfg.dnsimple_token}",
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                rec = json.loads(r.read())
            local.update({"url": f"https://{fqdn}/", "provider": "DNSimple",
                          "record_id": rec.get("data", {}).get("id"),
                          "note": f"CNAME {fqdn} created; point host at your static server"})
        except Exception as exc:                       # never crash the demo on DNS
            local["note"] = f"DNSimple call failed ({exc}); serving locally instead"
        return local


def get_deployer(cfg):
    if cfg.dnsimple_token and cfg.dnsimple_account and cfg.dnsimple_domain:
        return DNSimpleDeployer(cfg)
    return LocalDeployer(cfg)
