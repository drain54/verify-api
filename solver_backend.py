"""solver_backend.py — Captcha solver backend for verify-api.

Runs on http://127.0.0.1:8877
Exposes:
  POST /solve        — solve a CAPTCHA (turnstile/recaptcha/hcaptcha/cloudflare/arkose/awswaf/datadome)
  GET  /health       — provider health, balances, supported types

Adapters implemented: Capzy (CapSolver), Anti-Captcha, DeathByCaptcha.
Routing matrix picks the cheapest/fastest provider per captcha type with auto-fallback.

Env vars (provider credentials):
  CAPZY_API_KEY, ANTICAPTCHA_API_KEY, DBC_USERNAME, DBC_PASSWORD
"""
import os, time, asyncio, httpx
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from contextlib import asynccontextmanager

# Load .env file manually (no dotenv dependency needed)
_env_path = Path(__file__).parent / ".env"
if _env_path.is_file():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()  # overwrite, not setdefault

app = FastAPI(title="Captcha Solver Backend")

# --- config (from env, loaded from .env above) ---
CAPZY_KEY = os.getenv("CAPZY_API_KEY", "")
ANTICAPTCHA_KEY = os.getenv("ANTICAPTCHA_API_KEY", "")
DBC_USER = os.getenv("DBC_USERNAME", "")
DBC_PASS = os.getenv("DBC_PASSWORD", "")

SOLVE_TIMEOUT = 45  # seconds per provider, leaves headroom inside x402_mw 120s envelope
MAX_CONCURRENT = 5
_sem = asyncio.Semaphore(MAX_CONCURRENT)

# --- request model matching x402_mw.py proxy body ---
class SolveRequest(BaseModel):
    type: str
    sitekey: str
    url: str
    action: str | None = None
    cdata: str | None = None
    rqdata: str | None = None
    user_agent: str | None = None
    preferred_provider: str | None = None
    timeout_seconds: int | None = None

# ============================================================
# Provider map: captcha_type -> list of (name, solver_async_fn)
# Ordered cheapest/fastest first
# ============================================================

async def solve_with_capzy(req: SolveRequest, timeout: int = SOLVE_TIMEOUT) -> str:
    """Capzy / CapSolver adapter — best for Turnstile + Cloudflare + Arkose."""
    task_type = {
        "turnstile": "AntiTurnstileTaskProxyLess",
        "cloudflare": "AntiCloudflareTask",
        "hcaptcha": "HCaptchaTaskProxyLess",
        "recaptcha": "RecaptchaV3TaskProxyLess",
        "arkose": "FunCaptchaTaskProxyLess",
    }.get(req.type, "AntiTurnstileTaskProxyLess")

    task_payload: dict = {  # type: ignore
        "type": task_type,
        "websiteURL": req.url,
        "websiteKey": req.sitekey,
    }
    metadata = {}
    if req.cdata:
        metadata["cdata"] = req.cdata
    if req.action:
        metadata["action"] = req.action
    if metadata:
        task_payload["metadata"] = metadata

    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            "https://api.capsolver.com/createTask",
            json={"clientKey": CAPZY_KEY, "task": task_payload},
        )
        data = res.json()
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"Capzy createTask error: {data}")

        # Poll
        t0 = time.time()
        while time.time() - t0 < timeout:
            await asyncio.sleep(2)
            r = await client.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": CAPZY_KEY, "taskId": task_id},
            )
            d = r.json()
            if d.get("status") == "ready":
                sol = d.get("solution", {})
                # return token (Turnstile/hCaptcha) or token (reCAPTCHA v3 score)
                return sol.get("token") or sol.get("gRecaptchaResponse") or sol.get("token")
            if d.get("status") == "failed" or d.get("errorId", 0) > 0:
                raise RuntimeError(f"Capzy failed: {d.get('errorDescription', d)}")
        raise TimeoutError("Capzy solve timed out")


async def solve_with_anticaptcha(req: SolveRequest, timeout: int = SOLVE_TIMEOUT) -> str:
    """Anti-Captcha adapter — best for reCAPTCHA + hCaptcha + Turnstile."""
    task_type = {
        "turnstile": "AntiTurnstileTaskProxyless",
        "recaptcha": "RecaptchaV2TaskProxyless",
        "hcaptcha": "HCaptchaTaskProxyless",
        "cloudflare": "AntiTurnstileTaskProxyless",
    }.get(req.type, "AntiTurnstileTaskProxyless")

    async with httpx.AsyncClient(timeout=15) as client:
        res = await client.post(
            "https://api.anti-captcha.com/createTask",
            json={
                "clientKey": ANTICAPTCHA_KEY,
                "task": {
                    "type": task_type,
                    "websiteURL": req.url,
                    "websiteKey": req.sitekey,
                    "userAgent": req.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                },
            },
        )
        data = res.json()
        if data.get("errorId", 0) > 0:
            raise RuntimeError(f"Anti-Captcha createTask error: {data.get('errorDescription')}")
        task_id = data.get("taskId")

        t0 = time.time()
        while time.time() - t0 < timeout:
            await asyncio.sleep(2.5)
            r = await client.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": ANTICAPTCHA_KEY, "taskId": task_id},
            )
            d = r.json()
            if d.get("status") == "ready":
                sol = d.get("solution", {})
                return sol.get("token") or sol.get("gRecaptchaResponse")
            if d.get("errorId", 0) > 0:
                raise RuntimeError(f"Anti-Captcha error: {d.get('errorDescription')}")
        raise TimeoutError("Anti-Captcha solve timed out")


async def solve_with_dbc(req: SolveRequest, timeout: int = SOLVE_TIMEOUT) -> str:
    """DeathByCaptcha adapter — fallback for reCAPTCHA + hCaptcha."""
    import json, base64
    # DBC uses different API shape per type; we handle token-based captchas
    if req.type in ("recaptcha", "turnstile", "hcaptcha"):
        method_map = {
            "recaptcha": "userrecaptcha",
            "hcaptcha": "hcaptcha",
            "turnstile": "turnstile",
        }
        params = {
            "pageurl": req.url,
            "sitekey": req.sitekey,
            "proxy": "",
            "proxytype": "",
        }
        json_payload = json.dumps(params)
        form_data = {
            "username": DBC_USER,
            "password": DBC_PASS,
            "json:json_payload": json_payload,
            "type": 12 if req.type == "turnstile" else (4 if req.type == "hcaptcha" else 1),
        }
        # DBC needs form-encoded body
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "http://api.dbcapi.me/api/captcha",
                data=form_data,
            )
            data = r.json()
            if "error" in str(data).lower() and "captcha" not in data.get("captcha_id", ""):
                raise RuntimeError(f"DBC error: {data}")
            captcha_id = data.get("captcha_id") or data.get("id")
            if not captcha_id:
                raise RuntimeError(f"DBC no captcha_id: {data}")

            t0 = time.time()
            while time.time() - t0 < timeout:
                await asyncio.sleep(3)
                # poll
                r2 = await client.get(
                    f"http://api.dbcapi.me/api/captcha/{captcha_id}",
                    auth=(DBC_USER, DBC_PASS),
                )
                polldata = r2.json()
                if polldata.get("status") == "ok" or polldata.get("text"):
                    return polldata.get("text") or polldata.get("token", "")
                if polldata.get("status") == "error" or polldata.get("error"):
                    raise RuntimeError(f"DBC poll error: {polldata}")
        raise TimeoutError("DBC solve timed out")
    raise NotImplementedError(f"DBC does not support type '{req.type}'")


# --- provider list for health ---
ALL_PROVIDERS = {
    "capzy": {"fn": solve_with_capzy, "key": CAPZY_KEY},
    "anti_captcha": {"fn": solve_with_anticaptcha, "key": ANTICAPTCHA_KEY},
    "death_by_captcha": {"fn": solve_with_dbc, "key": DBC_USER},
}

# Routing: captcha_type -> [provider_name, ...] (cheapest/fastest first)
ROUTING = {
    "turnstile": ["capzy", "anti_captcha"],
    "cloudflare": ["capzy", "anti_captcha"],
    "hcaptcha": ["anti_captcha", "capzy"],
    "recaptcha": ["anti_captcha", "death_by_captcha", "capzy"],
    "arkose": ["capzy"],
    "awswaf": ["capzy", "anti_captcha"],
    "datadome": ["anti_captcha", "capzy"],
}

# Balance cache (avoid hammering providers on every health call)
_BAL_CACHE = {"capzy": {"usd": 0.0, "ts": 0}, "anti_captcha": {"usd": 0.0, "ts": 0}, "death_by_captcha": {"usd": 0.0, "ts": 0}}
_BAL_TTL = 300  # 5 min

async def _get_balance(name: str) -> float:
    cached = _BAL_CACHE.get(name, {})
    if time.time() - cached.get("ts", 0) < _BAL_TTL:
        return cached.get("usd", 0.0)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if name == "capzy" and CAPZY_KEY:
                r = await client.post("https://api.capsolver.com/getBalance", json={"clientKey": CAPZY_KEY})
                bal = r.json().get("balance", 0.0)
            elif name == "anti_captcha" and ANTICAPTCHA_KEY:
                r = await client.post("https://api.anti-captcha.com/getBalance", json={"clientKey": ANTICAPTCHA_KEY})
                bal = r.json().get("balance", 0.0)
            elif name == "death_by_captcha" and DBC_USER:
                r = await client.get("http://api.dbcapi.me/api/balance", auth=(DBC_USER, DBC_PASS))
                bal = float(r.json().get("balance", 0.0))
            else:
                bal = 0.0
    except Exception:
        bal = 0.0
    _BAL_CACHE[name] = {"usd": round(bal, 4), "ts": time.time()}
    return _BAL_CACHE[name]["usd"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Background balance refresh on startup."""
    async def refresh_balances():
        while True:
            for name in ALL_PROVIDERS:
                await _get_balance(name)
            await asyncio.sleep(_BAL_TTL)
    task = asyncio.create_task(refresh_balances())
    yield
    task.cancel()

app = FastAPI(title="Captcha Solver Backend", lifespan=lifespan)


# ============================================================
# Endpoints (match what x402_mw.py expects)
# ============================================================

@app.post("/solve")
async def solve(req: SolveRequest):
    t0 = time.time()
    timeout = req.timeout_seconds or SOLVE_TIMEOUT

    # If single provider requested
    if req.preferred_provider:
        chain = [req.preferred_provider] if req.preferred_provider in ALL_PROVIDERS else []
    else:
        chain = ROUTING.get(req.type, ["capzy", "anti_captcha"])

    last_error = "No provider available"
    for name in chain:
        provider = ALL_PROVIDERS.get(name)
        if not provider or not provider["key"]:
            last_error = f"{name} credentials missing"
            continue
        try:
            async with _sem:
                token = await provider["fn"](req, timeout=timeout)
            if token:
                balance = await _get_balance(name)
                return {
                    "solved": True,
                    "token": token,
                    "method": f"{name}:{req.type}",
                    "elapsed": round(time.time() - t0, 2),
                    "cost_usd": 0.0,  # unit economics handled by x402 layer
                    "error": None,
                }
        except Exception as e:
            last_error = f"{name} failed: {str(e)[:200]}"
            continue  # auto-fallback

    return {
        "solved": False,
        "token": None,
        "method": "exhausted",
        "elapsed": round(time.time() - t0, 2),
        "error": last_error,
    }


@app.get("/health")
async def health():
    providers_health = {}
    for name, info in ALL_PROVIDERS.items():
        bal = await _get_balance(name)
        providers_health[name] = {
            "status": "ok" if info["key"] else "disabled",
            "balance_usd": bal,
            "latency_ms": 0,
            "supported": [k for k, v in ROUTING.items() if name in v],
        }
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TS),
        "active_solves": MAX_CONCURRENT - _sem._value,
        "providers": providers_health,
        "supported_types": list(ROUTING.keys()),
    }

START_TS = time.time()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8877)
