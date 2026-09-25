"""expander.py — Poci Autonomous Market Expansion Engine.

Poci submits itself to NEW marketplaces on its own. Only listings Poci
published itself earn the x2 metabolic bonus; markets the human registered
manually are recorded with attributed_to="human" and earn nothing.

Two mechanisms:
  1. API-capable registries  -> Poci POSTs directly (needs a token).
  2. Browser-only registries  -> Poci prepares the exact payload and emits a
     clickable approval alert so the human does the final form submit. The
     listing is NOT credited to Poci in that case.
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from forager import register_market, load_markets
import remediate as R

BASE_DIR = Path(__file__).parent
EXPANSION_LOG = BASE_DIR / "expansion.jsonl"
SERVER_JSON = BASE_DIR / ".mcp.json"
REGISTRY_BASE = "https://registry.modelcontextprotocol.io"

# Registries Poci can reach with an API token. `env_key` names the credential
# in verify-api/.env or /home/aether/.hermes/.env.
API_TARGETS = [
    {
        "name": "mcp_registry",
        "env_key": "MCP_REGISTRY_TOKEN",
        "kind": "mcp_registry",
        "url": f"{REGISTRY_BASE}/v0/servers/io.github.drain54/verify-api",
    },
    {
        "name": "smithery",
        "env_key": "SMITHERY_API_KEY",
        "kind": "smithery",
        "url": "https://api.smithery.ai/servers/indradarmawan87/verify-api",
    },
]

# Registries with no public API — Poci prepares, human submits. Never credited.
MANUAL_TARGETS = [
    {"name": "glama", "url": "https://glama.ai/mcp/connectors",
     "note": "Connector tab: Name, Description, URL"},
    {"name": "mcp_so", "url": "https://mcp.so",
     "note": "No public submission API; manual form"},
    {"name": "agentgraph", "url": "https://agentgraph.dev",
     "note": "Derived from Official MCP Registry — fixing the registry fixes this"},
    {"name": "verifymcp", "url": "https://verifymcp.io",
     "note": "Derived from Official MCP Registry — scanner, no submission"},
    {"name": "canopii", "url": "https://index.canopii.dev",
     "note": "Security index over published source — no submission"},
]


def _log(event: dict):
    try:
        with EXPANSION_LOG.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
    except Exception:
        pass


def _token(env_key: str) -> str:
    for p in (BASE_DIR / ".env", Path("/home/aether/.hermes/.env")):
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith(f"{env_key}="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def already_registered(name: str) -> bool:
    return any(m["name"] == name for m in load_markets())


# ---------------------------------------------------------------- API PUBLISH

def _github_token() -> str:
    for p in (Path("/home/aether/.hermes/.env"), BASE_DIR / ".env"):
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def _registry_token() -> str:
    """Registry tokens are short-lived. Cache the raw value on disk; refresh
    by exchanging the GitHub PAT for a fresh one when it expires."""
    tok = _token("MCP_REGISTRY_TOKEN")
    if tok:
        return tok
    return ""


def exchange_registry_token() -> dict:
    """Trade the GitHub PAT for a short-lived registry publish token."""
    gh = _github_token()
    if not gh:
        return {"ok": False, "reason": "no_GITHUB_TOKEN"}
    req = urllib.request.Request(
        f"{REGISTRY_BASE}/v0/auth/github-at",
        data=json.dumps({"github_token": gh}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
            rt = d.get("registry_token", "")
            if rt:
                # Persist for the rest of the window (expires_at is unix secs)
                try:
                    p = BASE_DIR / ".env"
                    line = f"MCP_REGISTRY_TOKEN={rt}"
                    txt = p.read_text() if p.is_file() else ""
                    if line not in txt:
                        p.write_text(txt.rstrip("\n") + "\n" + line + "\n")
                except Exception:
                    pass
            return {"ok": bool(rt), "expires_at": d.get("expires_at")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode()[:300]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def publish_mcp_registry() -> dict:
    """POST server.json to the Official MCP Registry. This is the root that
    AgentGraph / VerifyMCP / Canopii all derive from."""
    tok = _registry_token()
    if not tok:
        ex = exchange_registry_token()
        tok = _registry_token()
        if not tok:
            return {"ok": False, "reason": "no_registry_token", "exchange": ex}

    payload = json.loads(SERVER_JSON.read_text())
    req = urllib.request.Request(
        f"{REGISTRY_BASE}/v0/publish",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tok}",
                 "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
            return {"ok": True, "status": r.status, "response": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode()[:400]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def publish_smithery() -> dict:
    tok = _token("SMITHERY_API_KEY")
    if not tok:
        return {"ok": False, "reason": "no_SMITHERY_API_KEY"}
    server_json = json.loads(SERVER_JSON.read_text())
    remote = server_json["remotes"][0]["url"]
    payload = {"name": "verify-api", "namespace": "indradarmawan87",
               "transport": {"type": "streamable-http", "url": remote}}
    req = urllib.request.Request(
        "https://api.smithery.ai/servers/indradarmawan87%2Fverify-api",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tok}",
                 "User-Agent": "Mozilla/5.0"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode()[:400]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


PUBLISHERS = {"mcp_registry": publish_mcp_registry, "smithery": publish_smithery}


# ---------------------------------------------------------------- CYCLE

def expansion_cycle(alert: bool = True) -> dict:
    results = {}

    for t in API_TARGETS:
        name = t["name"]
        if already_registered(name):
            results[name] = {"skipped": "already in markets.json"}
            continue
        if not _token(t["env_key"]):
            results[name] = {"blocked": f"missing {t['env_key']}",
                             "can_auto_submit": False}
            continue
        res = PUBLISHERS[t["kind"]]()
        results[name] = res
        if res.get("ok"):
            reg = register_market(name, t["url"],
                                  evidence=f"auto-published by poci, HTTP {res.get('status')}",
                                  established=False, attributed_to="poci")
            results[name]["bonus"] = reg.get("bonus_multiplier")
            if alert:
                R.send_telegram(
                    f"🚀 *POCI MARKET EXPANSION*\n"
                    f"Poci mendaftarkan dirinya sendiri ke *{name}*.\n"
                    f"Bonus insentif: *{reg.get('bonus_multiplier')}x* selama "
                    f"{reg.get('bonus_days')} hari.")
        _log({"event": "publish_attempt", "market": name, "result": res})

    pending_manual = [t for t in MANUAL_TARGETS if not already_registered(t["name"])]
    results["_manual_pending"] = [t["name"] for t in pending_manual]

    if pending_manual and alert:
        names = ", ".join(t["name"] for t in pending_manual)
        R.send_telegram(
            f"🧭 *POCI: MARKET TANPA API*\n"
            f"Market ini tidak punya API publik, jadi Poci tidak bisa submit sendiri:\n"
            f"`{names}`\n\n"
            f"Payload siap pakai di `verify-api/.mcp.json` v{json.loads(SERVER_JSON.read_text())['version']}.\n"
            f"Sudah terdaftar manual? Bilang saja — dicatat sebagai `human`, tanpa bonus.")

    return results
