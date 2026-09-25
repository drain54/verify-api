# remediate.py — Poci Executor: Detect → Diagnose → Propose → Act
# Safe-by-default: only acts when a service is confirmed DEAD.
# Anything destructive or disruptive requires human approval via queue.
import os
import json
import time
import hmac
import base64
import shutil
import secrets
import hashlib
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
QUEUE_PATH = BASE_DIR / "remediate_queue.jsonl"
STATE_PATH = BASE_DIR / "remediate_state.json"
LOG_PATH = BASE_DIR / "remediation.jsonl"
NONCE_PATH = BASE_DIR / "remediate_nonces.json"

MW_PORT = 8012
SOLVER_PORT = 8877
PUBLIC_URL = "https://verify.drain54.my.id"
HERMES_ENV = Path("/home/aether/.hermes/profiles/jarpis-bot1/.env")
ENV_PATH = BASE_DIR / ".env"
HERMES_ENV_PATH = Path("/home/aether/.hermes/.env")

# Actions that Poci may execute WITHOUT approval (non-destructive, service already dead)
AUTO_ACTIONS = {"restart_solver", "restart_mw", "restart_tunnel"}
# Actions that ALWAYS require human approval
APPROVAL_ACTIONS = {"flush_logs", "rotate_wallet", "shutdown_mw"}


def _log(event: dict):
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
    except Exception:
        pass


def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"consecutive_failures": {}, "last_alert": {}}


def save_state(state: dict):
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def port_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3):
            return True
    except urllib.error.HTTPError:
        # Responded with an HTTP error => process IS alive
        return True
    except Exception:
        pass
    # Fallback: probe a known GET endpoint
    for path in ("/v1/captcha-pricing", "/"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3):
                return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            continue
    return False


def proc_running(pattern: str) -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return out.returncode == 0 and out.stdout.strip() != ""
    except Exception:
        return False


def tunnel_alive() -> bool:
    try:
        req = urllib.request.Request(f"{PUBLIC_URL}/.well-known/glama.json",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        pass
    return False


def tunnel_status_code():
    """Return (reachable, http_code). Used to tell a 5xx apart from a dead host."""
    try:
        req = urllib.request.Request(f"{PUBLIC_URL}/.well-known/glama.json",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return True, resp.status
    except urllib.error.HTTPError as e:
        return True, e.code
    except Exception:
        return False, None


# ---------------------------------------------------------------- ACTIONS

def action_restart_solver() -> dict:
    if proc_running("solver_backend.py"):
        return {"ok": False, "reason": "solver already running"}
    py = BASE_DIR / ".venv/bin/python"
    subprocess.Popen(
        [str(py), str(BASE_DIR / "solver_backend.py")],
        cwd=str(BASE_DIR),
        stdout=open(BASE_DIR / "solver.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"ok": True, "detail": "solver_backend.py spawned"}


def action_restart_mw() -> dict:
    """Kill any running middleware then respawn it. Safe when nothing is running."""
    if proc_running("x402_mw.py"):
        subprocess.run(["pkill", "-f", "x402_mw.py"], capture_output=True)
        time.sleep(2)
    py = BASE_DIR / ".venv/bin/python"
    subprocess.Popen(
        [str(py), str(BASE_DIR / "x402_mw.py")],
        cwd=str(BASE_DIR),
        stdout=open(BASE_DIR / "x402_mw.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"ok": True, "detail": "x402_mw.py (re)spawned"}


def action_restart_tunnel() -> dict:
    if proc_running("cloudflared tunnel"):
        subprocess.run(["pkill", "-f", "cloudflared tunnel"], capture_output=True)
        time.sleep(2)
    script = Path("/home/aether/.cloudflared/start-verify-tunnel.sh")
    if not script.is_file():
        return {"ok": False, "reason": "tunnel start script missing"}
    subprocess.Popen(["/bin/sh", str(script)],
                     stdout=open("/home/aether/.cloudflared/tunnel.log", "a"),
                     stderr=subprocess.STDOUT,
                     start_new_session=True)
    return {"ok": True, "detail": "cloudflared tunnel respawned"}


def action_flush_logs() -> dict:
    freed = 0
    for name in ("x402_mw.log", "solver.log", "usage.jsonl", "foraging.jsonl", "remediation.jsonl"):
        p = BASE_DIR / name
        if not p.is_file():
            continue
        size = p.stat().st_size
        if size > 5 * 1024 * 1024:
            lines = p.read_text(errors="ignore").splitlines()[-2000:]
            p.write_text("\n".join(lines) + "\n")
            freed += size - p.stat().st_size
    return {"ok": True, "detail": f"freed {freed} bytes"}


ACTIONS = {
    "restart_solver": action_restart_solver,
    "restart_mw": action_restart_mw,
    "restart_tunnel": action_restart_tunnel,
    "flush_logs": action_flush_logs,
}


# ---------------------------------------------------------------- ALERTING

def get_telegram_token() -> str:
    if not HERMES_ENV.is_file():
        return ""
    for line in HERMES_ENV.read_text().splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


# ------------------------------------------------- CLICKABLE APPROVAL TOKENS
# A remediation alert carries a signed, single-use, time-limited token. The
# Telegram inline button opens /v1/remediate/approve?action=..&token=.. which
# validates the HMAC before executing. No secret ever leaves the server.

APPROVAL_TTL_SECONDS = 900  # 15 minutes


def _approval_secret() -> bytes:
    for p in (ENV_PATH, HERMES_ENV_PATH):
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith("POCI_APPROVAL_SECRET="):
                    return line.split("=", 1)[1].strip().strip("\"'").encode()
    # Deterministic fallback derived from an existing secret so no new
    # credential is required for the feature to work.
    return hashlib.sha256(get_telegram_token().encode()).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_approval_token(action: str, ttl: int = APPROVAL_TTL_SECONDS) -> str:
    nonce = secrets.token_hex(8)
    exp = int(time.time()) + ttl
    payload = f"{action}|{nonce}|{exp}".encode()
    sig = hmac.new(_approval_secret(), payload, hashlib.sha256).digest()[:16]
    return f"{_b64e(payload)}.{_b64e(sig)}"


def verify_approval_token(action: str, token: str) -> dict:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(payload_b64)
        sig = _b64d(sig_b64)
    except Exception:
        return {"ok": False, "reason": "malformed_token"}
    expected = hmac.new(_approval_secret(), payload, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(sig, expected):
        return {"ok": False, "reason": "bad_signature"}
    try:
        tok_action, nonce, exp = payload.decode().split("|")
        exp = int(exp)
    except Exception:
        return {"ok": False, "reason": "malformed_payload"}
    if tok_action != action:
        return {"ok": False, "reason": "action_mismatch"}
    if time.time() > exp:
        return {"ok": False, "reason": "expired"}
    # Single-use
    used = {}
    if NONCE_PATH.is_file():
        try:
            used = json.loads(NONCE_PATH.read_text())
        except Exception:
            used = {}
    if nonce in used:
        return {"ok": False, "reason": "already_used"}
    used[nonce] = {"action": action, "ts": datetime.now(timezone.utc).isoformat()}
    try:
        NONCE_PATH.write_text(json.dumps(used))
    except Exception:
        pass
    return {"ok": True, "action": action, "nonce": nonce}


def send_telegram(text: str, thread_id: int = 576, reply_markup: dict | None = None) -> bool:
    token = get_telegram_token()
    if not token:
        _log({"event": "alert_failed", "reason": "no token"})
        return False
    body = {
        "chat_id": "-1004473785949",
        "message_thread_id": thread_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        body["reply_markup"] = reply_markup
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as e:
        _log({"event": "alert_failed", "error": str(e)})
        return False


def propose(action: str, reason: str, diagnosis: dict, auto: bool):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "reason": reason,
        "diagnosis": diagnosis,
        "auto_executed": auto,
        "status": "EXECUTED" if auto else "PENDING_APPROVAL",
    }
    with QUEUE_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    if auto:
        tag = "✅ AUTO-EXECUTED"
        send_telegram(
            f"⚠️ *POCI REMEDIATION ALERT*\n"
            f"Action: `{action}`\n"
            f"Reason: {reason}\n"
            f"Status: {tag}\n\n"
            f"```\n{json.dumps(diagnosis, indent=2)[:900]}\n```"
        )
        return entry

    # Needs approval -> attach a clickable, signed, single-use button.
    tok = mint_approval_token(action)
    approve_url = (f"{PUBLIC_URL}/v1/remediate/approve"
                   f"?action={action}&token={tok}")
    entry["approval_url"] = approve_url
    with QUEUE_PATH.open("a") as f:
        f.write(json.dumps({**entry, "event": "approval_link_issued"}) + "\n")

    send_telegram(
        f"⚠️ *POCI REMEDIATION ALERT*\n"
        f"Action: `{action}`\n"
        f"Reason: {reason}\n"
        f"Status: 🛑 AWAITING APPROVAL\n\n"
        f"```\n{json.dumps(diagnosis, indent=2)[:900]}\n```",
        reply_markup={"inline_keyboard": [[
            {"text": f"✅ Jalankan {action}", "url": approve_url},
            {"text": "❌ Abaikan", "callback_data": f"poci_ignore:{action}"},
        ]]},
    )
    return entry


# ---------------------------------------------------------------- MAIN CYCLE

def diagnose() -> dict:
    t_ok, t_code = tunnel_status_code()
    d: dict = {"mw_port": bool(port_alive(MW_PORT)),
         "solver_port": bool(port_alive(SOLVER_PORT)),
         "tunnel": t_ok,
         "tunnel_http": t_code,
         "mw_proc": bool(proc_running("x402_mw.py")),
         "solver_proc": bool(proc_running("solver_backend.py"))}
    try:
        st = os.statvfs(str(BASE_DIR))
        d["disk_free_pct"] = round((st.f_bavail / st.f_blocks) * 100, 1)
    except Exception:
        d["disk_free_pct"] = None
    return d


def _brain_advisory(d: dict) -> list:
    """Consult the lightweight brain ONLY for states the rules cannot decide.

    Returns a list of proposals (never executes). Everything the deterministic
    rules already handle never reaches the brain.
    """
    import cognition

    suggestions = []

    # TCP accepts but health fails -> hung worker, wrong route, or slow dep?
    for label, port in (("mw", MW_PORT), ("solver", SOLVER_PORT)):
        if d.get(f"{label}_proc") and not d.get(f"{label}_port"):
            v = cognition.think("port_alive_but_not_serving", d)
            if v.get("next_action") and v["next_action"] != "none":
                suggestions.append((v["next_action"], d, v))

    # Public URL returns 5xx rather than a clean failure
    if not d.get("tunnel") and d.get("tunnel_http"):
        v = cognition.think("tunnel_5xx", d)
        if v.get("next_action") and v["next_action"] != "none":
            suggestions.append((v["next_action"], d, v))

    return suggestions


def remediate_cycle(require_confirmation_rounds: int = 2) -> dict:
    d = diagnose()
    state = load_state()
    fails = state.get("consecutive_failures", {})

    def bump(key, bad):
        fails[key] = fails.get(key, 0) + 1 if bad else 0
        return fails[key]

    executed = []
    advised = []

    # Solver backend
    if bump("solver", not d["solver_port"]) >= require_confirmation_rounds:
        if not d["solver_port"] and not d["solver_proc"]:
            res = ACTIONS["restart_solver"]()
            executed.append({"action": "restart_solver", **res})
            propose("restart_solver", "Solver :8877 dead and no process found", d, auto=res.get("ok", False))
            fails["solver"] = 0

    # Middleware
    if bump("mw", not d["mw_port"]) >= require_confirmation_rounds:
        if not d["mw_port"] and not d["mw_proc"]:
            res = ACTIONS["restart_mw"]()
            executed.append({"action": "restart_mw", **res})
            propose("restart_mw", "Middleware :8012 dead and no process found", d, auto=res.get("ok", False))
            fails["mw"] = 0

    # Tunnel
    if bump("tunnel", not d["tunnel"]) >= require_confirmation_rounds * 3:
        res = ACTIONS["restart_tunnel"]()
        executed.append({"action": "restart_tunnel", **res})
        propose("restart_tunnel", "Public URL unreachable via Cloudflare", d, auto=res.get("ok", False))
        fails["tunnel"] = 0

    # Disk pressure -> requires approval (destructive: truncates logs)
    if d.get("disk_free_pct") is not None and d["disk_free_pct"] < 10:
        propose("flush_logs", f"Disk free only {d['disk_free_pct']}%", d, auto=False)

    # Ambiguous states -> brain suggests, human decides (never auto-executes)
    for action, diag, verdict in _brain_advisory(d):
        reason = (f"Brain verdict: {verdict.get('verdict')} "
                  f"(confidence {verdict.get('confidence')}) — {verdict.get('reason')}")
        propose(action, reason, diag, auto=False)
        advised.append({"action": action, "verdict": verdict})

    state["consecutive_failures"] = fails
    save_state(state)
    _log({"event": "cycle", "diagnosis": d, "executed": executed, "advised": advised})
    return {"diagnosis": d, "executed": executed, "brain_advised": advised}


def approve(action: str) -> dict:
    """Human approval path — run via CLI: python remediate.py --approve restart_tunnel"""
    if action not in ACTIONS:
        return {"ok": False, "reason": f"unknown action {action}"}
    res = ACTIONS[action]()
    _log({"event": "manual_approve", "action": action, "result": res})
    return res


if __name__ == "__main__":
    import sys
    if "--approve" in sys.argv:
        act = sys.argv[sys.argv.index("--approve") + 1]
        print(json.dumps(approve(act), indent=2))
    else:
        print(json.dumps(remediate_cycle(), indent=2))
