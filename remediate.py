# remediate.py — Poci Executor: Detect → Diagnose → Propose → Act
# Safe-by-default: only acts when a service is confirmed DEAD.
# Anything destructive or disruptive requires human approval via queue.
import os
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
QUEUE_PATH = BASE_DIR / "remediate_queue.jsonl"
STATE_PATH = BASE_DIR / "remediate_state.json"
LOG_PATH = BASE_DIR / "remediation.jsonl"

MW_PORT = 8012
SOLVER_PORT = 8877
PUBLIC_URL = "https://verify.drain54.my.id"
HERMES_ENV = Path("/home/aether/.hermes/profiles/jarpis-bot1/.env")

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
        return False


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
    if proc_running("x402_mw.py"):
        return {"ok": False, "reason": "middleware already running"}
    py = BASE_DIR / ".venv/bin/python"
    subprocess.Popen(
        [str(py), str(BASE_DIR / "x402_mw.py")],
        cwd=str(BASE_DIR),
        stdout=open(BASE_DIR / "x402_mw.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"ok": True, "detail": "x402_mw.py spawned"}


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


def send_telegram(text: str, thread_id: int = 576) -> bool:
    token = get_telegram_token()
    if not token:
        _log({"event": "alert_failed", "reason": "no token"})
        return False
    payload = json.dumps({
        "chat_id": "-1004473785949",
        "message_thread_id": thread_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
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
    tag = "✅ AUTO-EXECUTED" if auto else "🛑 AWAITING APPROVAL"
    send_telegram(
        f"⚠️ *POCI REMEDIATION ALERT*\n"
        f"Action: `{action}`\n"
        f"Reason: {reason}\n"
        f"Status: {tag}\n\n"
        f"```\n{json.dumps(diagnosis, indent=2)[:900]}\n```"
    )
    return entry


# ---------------------------------------------------------------- MAIN CYCLE

def diagnose() -> dict:
    d: dict = {"mw_port": bool(port_alive(MW_PORT)),
         "solver_port": bool(port_alive(SOLVER_PORT)),
         "tunnel": bool(tunnel_alive()),
         "mw_proc": bool(proc_running("x402_mw.py")),
         "solver_proc": bool(proc_running("solver_backend.py"))}
    try:
        st = os.statvfs(str(BASE_DIR))
        d["disk_free_pct"] = round((st.f_bavail / st.f_blocks) * 100, 1)
    except Exception:
        d["disk_free_pct"] = None
    return d


def remediate_cycle(require_confirmation_rounds: int = 2) -> dict:
    d = diagnose()
    state = load_state()
    fails = state.get("consecutive_failures", {})

    def bump(key, bad):
        fails[key] = fails.get(key, 0) + 1 if bad else 0
        return fails[key]

    executed = []

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

    state["consecutive_failures"] = fails
    save_state(state)
    _log({"event": "cycle", "diagnosis": d, "executed": executed})
    return {"diagnosis": d, "executed": executed}


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
