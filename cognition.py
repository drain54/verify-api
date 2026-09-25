# cognition.py — Poci's lightweight brain.
#
# Poci's organs (metabolism/forager/remediate/expander) are deterministic.
# This module is the ONLY place an LLM is consulted, and only when a situation
# appears that the deterministic rules have no verdict for. Silent when healthy.
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
THINK_LOG = BASE_DIR / "cognition.jsonl"
HERMES_ENV = Path("/home/aether/.hermes/.env")
LOCAL_ENV = BASE_DIR / ".env"

MODEL = os.getenv("POCI_BRAIN_MODEL", "gemini-flash-lite-latest")
API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={k}"
THINK_BUDGET_PER_DAY = 5  # hard cap: never burn more than this many calls/day

# Situations the rules can't decide. Anything not in here is handled
# deterministically and never reaches the brain.
AMBIGUOUS_CLASSES = {
    "port_alive_but_not_serving": "TCP accepts connections but the health "
                                   "endpoint errors — hung worker, wrong route, "
                                   "or slow dependency?",
    "tunnel_5xx": "Cloudflare returned 5xx — origin down, tunnel auth expired, "
                  "or origin crash-looping?",
    "registry_changed": "A known registry's response shape changed — new "
                        "version, moved field, or broken upstream?",
    "solver_balance_zero": "Solver is up but a provider reports zero balance "
                           "while solves still route to it.",
    "inflow_spike": "On-chain inflow moved far outside the 7-day range.",
    "market_gone": "A marketplace that was listed in markets.json is no longer "
                   "reachable — delisted, renamed, or transient?",
}


def _log(event: dict):
    try:
        with THINK_LOG.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
    except Exception:
        pass


def _key() -> str:
    for p in (LOCAL_ENV, HERMES_ENV):
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    v = line.split("=", 1)[1].strip().strip("\"'")
                    if v:
                        return v
    return ""


def budget_used_today() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    if not THINK_LOG.is_file():
        return 0
    try:
        for line in THINK_LOG.read_text().splitlines():
            try:
                if json.loads(line).get("ts", "").startswith(today) and "verdict" in line:
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n


def think(situation: str, evidence: dict) -> dict:
    """Ask the brain about ONE ambiguous situation. Returns a verdict dict.

    Never raises. Never loops. Returns {'skipped': reason} if out of budget.
    """
    if situation not in AMBIGUOUS_CLASSES:
        return {"skipped": "not_an_ambiguous_class"}

    key = _key()
    if not key:
        return {"skipped": "no_gemini_key"}

    if budget_used_today() >= THINK_BUDGET_PER_DAY:
        _log({"event": "budget_exhausted", "situation": situation})
        return {"skipped": "daily_budget_exhausted"}

    prompt = (
        "You are a small self-funded web service. One situation is ambiguous. "
        "Diagnose the MOST LIKELY single cause and recommend the SAFEST next "
        "action. Reply with ONLY one line of JSON, no prose, no markdown.\n"
        f"Schema: {{\"verdict\":\"<short label>\",\"confidence\":<0-1>,"
        f"\"next_action\":\"<restart_solver|restart_mw|restart_tunnel|none>\","
        f"\"reason\":\"<max 15 words>\"}}\n\n"
        f"Situation class: {situation}\n"
        f"What this means: {AMBIGUOUS_CLASSES[situation]}\n"
        f"Evidence: {json.dumps(evidence)[:1200]}\n"
        "Rules: if unsure use next_action=none. Never suggest anything "
        "destructive. Prefer none over a wrong restart."
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 200},
    }
    req = urllib.request.Request(
        API.format(m=MODEL, k=key),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        text = d["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        out = json.loads(text)
    except urllib.error.HTTPError as e:
        _log({"event": "brain_error", "status": e.code, "situation": situation})
        return {"skipped": f"http_{e.code}"}
    except Exception as e:
        _log({"event": "brain_error", "err": type(e).__name__, "situation": situation})
        return {"skipped": f"{type(e).__name__}"}

    allowed = {"restart_solver", "restart_mw", "restart_tunnel", "none"}
    if out.get("next_action") not in allowed:
        out["next_action"] = "none"
    out["situation"] = situation
    _log({"event": "verdict", "verdict": out, "evidence": evidence})
    return out
