# x402_mw.py — payment middleware + CAPTCHA solving API
import os, json, uuid, httpx, asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=", 1))

from main import verify, Req  # noqa: E402

FACILITATOR = os.getenv("X402_FACILITATOR_URL", "https://facilitator.payai.network")
WALLET = os.getenv("X402_WALLET", "")
USDC = os.getenv("X402_USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
USDC_NAME = os.getenv("X402_USDC_NAME", "USD Coin")
NET = os.getenv("X402_NETWORK", "eip155:84532")
V1_NET = "base-sepolia" if "84532" in NET else "base"
PRICES = {"standard": "1000000", "deep": "3000000"}
LOG_PATH = Path(__file__).parent / "usage.jsonl"

CAPTCHA_SOLVER_URL = "http://127.0.0.1:8877"

# Tiered pricing for CAPTCHA solving (in micro-USDC)
# Pricing: micro-USDC per solve (1 USDC = 1,000,000 micro)
# Based on 2026 market rates: CapSolver ~$0.80/1000 reCAPTCHA, 2Captcha ~$2.99/1000
# We price slightly below competitors with x402 convenience premium
CAPTCHA_PRICING = {
    "turnstile": 1000,      # $0.0010 - simplest
    "cloudflare": 1500,     # $0.0015 - moderate
    "awswaf": 1500,
    "botguard": 1500,
    "datadome": 2000,
    "perimeterx": 2000,
    "akamai": 2000,
    "aliyun": 2500,
    "arkose": 2500,         # $0.0025 - complex multi-wave
    "recaptcha": 1200,      # $0.0012
    "hcaptcha": 1200,       # $0.0012
}

# Concurrency limit + queue
MAX_CONCURRENT_SOLVES = 5
solve_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SOLVES)
solve_queue: asyncio.Queue | None = None

def _log(event: dict):
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
    except Exception:
        pass

def _payment_requirements(resource: str, amount: str, description: str = "AI infra claim verification — pay-per-query.") -> dict:
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": V1_NET,
        "maxAmountRequired": amount,
        "resource": resource,
        "description": description,
        "mimeType": "application/json",
        "payTo": WALLET,
        "maxTimeoutSeconds": 120,
        "asset": USDC,
        "extra": {"name": USDC_NAME, "version": "2"},
    }

def _get_resource(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}{request.url.path}"

async def _verify_payment(request: Request, amount: str, description: str = "AI infra claim verification — pay-per-query."):
    """Verify x402 payment. Returns (paid: bool, reason: str | None)."""
    payment_header = request.headers.get("X-PAYMENT")
    if not payment_header:
        return False, None
    resource = _get_resource(request)
    requirements = _payment_requirements(resource, amount, description)
    payload = payment_header
    if payment_header.startswith("0x"):
        payload = json.loads(bytes.fromhex(payment_header[2:]).decode())
    else:
        payload = json.loads(payment_header)
    if payload.get("accepted") and "x402Version" in payload and payload.get("x402Version", 1) != 1:
        payload = {
            "x402Version": 1,
            "scheme": payload.get("scheme", "exact"),
            "network": payload["accepted"].get("network", NET),
            "payload": payload.get("payload", {}),
        }
    if "x402Version" in payload:
        payload["x402Version"] = 1
    async with httpx.AsyncClient(timeout=30) as client:
        vr = await client.post(f"{FACILITATOR}/verify", json={
            "paymentPayload": payload,
            "paymentRequirements": {
                "scheme": "exact", "network": V1_NET, "maxAmountRequired": amount,
                "resource": resource, "description": description, "mimeType": "application/json",
                "payTo": WALLET, "maxTimeoutSeconds": 120, "asset": USDC,
                "extra": {"name": USDC_NAME, "version": "2"},
            }
        }, headers={"Content-Type": "application/json"})
        ver = vr.json()
        if not ver.get("isValid"):
            return False, ver.get("invalidReason")
        sr = await client.post(f"{FACILITATOR}/settle", json={
            "paymentPayload": payload,
            "paymentRequirements": {
                "scheme": "exact", "network": V1_NET, "maxAmountRequired": amount,
                "resource": resource, "description": description, "mimeType": "application/json",
                "payTo": WALLET, "maxTimeoutSeconds": 120,
                "asset": USDC,
                "extra": {"name": USDC_NAME, "version": "2"},
            }
        }, headers={"Content-Type": "application/json"})
        settled = sr.json()
        if not settled.get("success"):
            return False, settled.get("errorReason")
    return True, None

async def _queue_solve(captcha_type: str, body: dict, headers: dict) -> dict:
    """Queue a CAPTCHA solve with retry logic."""
    global solve_queue
    if solve_queue is None:
        solve_queue = asyncio.Queue(maxsize=100)
    
    future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
    await solve_queue.put((captcha_type, body, headers, future))
    
    try:
        result = await asyncio.wait_for(future, timeout=180)
        return result
    except asyncio.TimeoutError:
        return {"error": "CAPTCHA solve timed out in queue", "solved": False}

async def _process_queue():
    """Background task to process the solve queue."""
    global solve_queue
    while True:
        try:
            if solve_queue is None:
                await asyncio.sleep(0.1)
                continue
            captcha_type, body, headers, future = await solve_queue.get()
            async with solve_semaphore:
                try:
                    async with httpx.AsyncClient(timeout=120) as client:
                        resp = await client.post(
                            f"{CAPTCHA_SOLVER_URL}/solve",
                            content=json.dumps(body).encode(),
                            headers={k: v for k, v in headers.items() if k.lower() not in ("host", "content-length", "x-payment")},
                        )
                    result = resp.json()
                    _log({"path": "/v1/solve-captcha", "type": captcha_type, "result": "solved" if result.get("solved") else "failed"})
                    if not future.done():
                        future.set_result(result)
                except httpx.TimeoutException:
                    _log({"path": "/v1/solve-captcha", "type": captcha_type, "result": "timeout"})
                    if not future.done():
                        future.set_result({"error": "CAPTCHA solve timed out", "solved": False})
                except Exception as e:
                    _log({"path": "/v1/solve-captcha", "type": captcha_type, "result": "error", "error": str(e)})
                    if not future.done():
                        future.set_result({"error": f"solver error: {type(e).__name__}: {e}", "solved": False})
                finally:
                    solve_queue.task_done()
        except Exception as e:
            _log({"path": "/v1/solve-captcha", "result": "queue_error", "error": str(e)})
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the queue processor on startup."""
    task = asyncio.create_task(_process_queue())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

TOOLS_DEFINITION = [
    {
        "name": "verify_ai_claim",
        "description": "Verify whether an AI model, API provider, pricing claim, or infrastructure assertion is true today using live web evidence. Returns deterministic verdicts (TRUE, FALSE, PARTIALLY_TRUE, UNREACHABLE, UNVERIFIED) with cited sources and confidence scores. Requires x402 micropayment (0.01 USDC on Base).\n\nWhen to use: Fact-checking an AI provider's claims, pricing, or model availability.\nWhen NOT to use: Do NOT use for general open-ended web search, coding assistance, or non-AI claim verification.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The specific AI infrastructure claim or assertion to verify (e.g., 'Is GLM-5.3 Flash free on ZenMux?')."
                },
                "depth": {
                    "type": "string",
                    "enum": ["standard", "deep"],
                    "description": "Verification depth: 'standard' (0.01 USDC) or 'deep' (0.03 USDC)."
                }
            },
            "required": ["query"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique verification record ID"},
                "verdict": {"type": "string", "enum": ["TRUE", "FALSE", "PARTIALLY_TRUE", "CHANGED", "UNREACHABLE", "BLOCKED", "UNVERIFIED"], "description": "Deterministic evidence-backed verdict"},
                "answer": {"type": "string", "description": "Detailed explanation backed by fresh sources"},
                "confidence": {"type": "number", "description": "Confidence score between 0.0 and 1.0"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "Source URL"},
                            "title": {"type": "string", "description": "Source page title"},
                            "type": {"type": "string", "description": "Source type (official, third_party, community)"}
                        }
                    },
                    "description": "Fresh evidence sources used for verification"
                }
            },
            "required": ["verdict", "answer", "confidence", "sources"]
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "solve_captcha",
        "description": "Solve web CAPTCHA challenges (Turnstile, hCaptcha, reCAPTCHA v2, Arkose, Cloudflare) and return a valid solution token. Requires x402 payment on Base.\n\nWhen to use: Use when an agent encounters a bot wall or CAPTCHA challenge during automated web workflows.\nWhen NOT to use: Do NOT use for non-CAPTCHA auth, 2FA/OTP codes, or general login forms.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["turnstile", "hcaptcha", "recaptcha", "arkose", "cloudflare"],
                    "description": "The specific type of CAPTCHA challenge encountered on the target page."
                },
                "sitekey": {
                    "type": "string",
                    "description": "The CAPTCHA sitekey parameter extracted from the target page DOM or iframe."
                },
                "url": {
                    "type": "string",
                    "description": "The full target page URL where the CAPTCHA challenge is hosted."
                }
            },
            "required": ["type", "sitekey", "url"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "solved": {"type": "boolean", "description": "Whether the CAPTCHA challenge was successfully solved"},
                "token": {"type": "string", "description": "The resulting CAPTCHA response token to submit to the form"},
                "method": {"type": "string", "description": "Solving method or backend engine used"},
                "elapsed": {"type": "number", "description": "Time taken in seconds to solve the challenge"}
            },
            "required": ["solved", "token"]
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False
        }
    }
]

def _mcp_rpc_response(body: dict):
    method = body.get("method") if isinstance(body, dict) else None
    req_id = body.get("id") if isinstance(body, dict) else 1
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"list": {"disabled": False}}},
                "serverInfo": {"name": "verify-api", "version": "0.3.0"}
            }
        }
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS_DEFINITION
            }
        }
    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return None

@app.get("/")
@app.get("/mcp")
@app.get("/v1/verify")
async def root_mcp_info():
    return JSONResponse(content={
        "name": "io.github.drain54/verify-api",
        "status": "ok",
        "mcp_endpoint": "https://verify.drain54.my.id/v1/verify",
        "card": "https://verify.drain54.my.id/.well-known/mcp/server-card.json"
    })

@app.post("/")
@app.post("/mcp")
async def root_mcp_post(request: Request):
    try:
        body = await request.json()
        resp = _mcp_rpc_response(body)
        if resp:
            return JSONResponse(content=resp)
    except Exception:
        pass
    return JSONResponse(content={"status": "ok", "message": "Verify API MCP endpoint."})

@app.post("/v1/verify")
async def paid_verify(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    method = body.get("method") if isinstance(body, dict) else None
    remote_ip = request.client.host if request.client else "unknown"
    _log({"path": "/v1/verify", "method": method or "unknown", "remote": remote_ip, "result": "free_handshake"})
    mcp_resp = _mcp_rpc_response(body)
    if mcp_resp:
        return JSONResponse(content=mcp_resp)
    depth = (body.get("depth") or "standard").lower()
    amt = PRICES.get(depth, PRICES["standard"])
    resource = _get_resource(request)
    payment_header = request.headers.get("X-PAYMENT")
    if not payment_header:
        _log({"path": "/v1/verify", "method": body.get("method") or "unknown", "remote": request.client.host, "result": "402_no_payment"})
        return Response(content=json.dumps(_payment_requirements(resource, amt)), status_code=402, headers={"Content-Type": "application/json", "Accept": "application/x402-payment-v2+json", "Paywall": "x402"})
    try:
        paid, reason = await _verify_payment(request, amt)
        if not paid:
            detail = {"error": "payment required", "requirements": _payment_requirements(resource, amt)}
            if reason:
                detail["invalidReason"] = reason
            return JSONResponse(content=detail, status_code=402)
        req = Req(**body)
        result = await verify(req)
        _log({"path": "/v1/verify", "method": body.get("method") or "unknown", "remote": request.client.host, "result": "paid_ok"})
        return result
    except Exception as e:
        _log({"path": "/v1/verify", "method": body.get("method") or "unknown", "remote": request.client.host, "result": "500_paid_crash", "error": f"{type(e).__name__}: {e}"})
        return JSONResponse(content={"error": f"paid path crashed: {type(e).__name__}: {e}"}, status_code=500)

@app.get("/v1/captcha-pricing")
async def captcha_pricing():
    """Public endpoint: get CAPTCHA solving prices (per 1000 solves)."""
    return {
        "pricing_per_1000_solves": {k: {"price_usdc": v / 1_000_000 * 1000, "price_per_solve": v / 1_000_000} for k, v in CAPTCHA_PRICING.items()},
        "payment": "x402 on Base (USDC)",
        "note": "One free retry if solve fails",
    }

@app.get("/v1/captcha-health")
async def captcha_health():
    """Public endpoint: check CAPTCHA solver health."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{CAPTCHA_SOLVER_URL}/health")
            solver_health = r.json()
    except Exception as e:
        solver_health = {"status": "unreachable", "error": str(e)}
    return {
        "status": "ok" if solver_health.get("status") == "ok" else "degraded",
        "solver": solver_health,
        "supported_types": list(CAPTCHA_PRICING.keys()),
        "max_concurrent_solves": MAX_CONCURRENT_SOLVES,
    }

@app.post("/v1/solve-captcha")
async def solve_captcha(request: Request):
    """Solve CAPTCHA with x402 payment."""
    body = await request.json()
    captcha_type = body.get("type", "")
    
    if captcha_type not in CAPTCHA_PRICING:
        return JSONResponse(
            content={"error": f"unsupported CAPTCHA type: {captcha_type}", "supported_types": list(CAPTCHA_PRICING.keys())},
            status_code=400,
        )
    
    amount = str(CAPTCHA_PRICING[captcha_type])
    resource = f"{request.url.scheme}://{request.url.netloc}/v1/solve-captcha"
    
    # Check payment
    payment_header = request.headers.get("X-PAYMENT")
    if not payment_header:
        _log({"path": "/v1/solve-captcha", "type": captcha_type, "remote": request.client.host, "result": "402_no_payment"})
        return JSONResponse(
            content={"error": "payment required", "requirements": _payment_requirements(resource, amount, f"CAPTCHA solve — {captcha_type}.")},
            status_code=402,
        )
    
    captcha_description = f"CAPTCHA solve — {captcha_type}."
    try:
        paid, reason = await _verify_payment(request, amount, captcha_description)
        if not paid:
            detail = {"error": "payment required", "requirements": _payment_requirements(resource, amount, captcha_description)}
            if reason:
                detail["invalidReason"] = reason
            return JSONResponse(content=detail, status_code=402)
    except Exception as e:
        _log({"path": "/v1/solve-captcha", "type": captcha_type, "remote": request.client.host, "result": "500_payment_error", "error": str(e)})
        return JSONResponse(content={"error": f"payment verification failed: {e}"}, status_code=500)
    
    # Forward to captcha-solver via queue
    result = await _queue_solve(captcha_type, body, dict(request.headers))
    
    # If failed, retry once for free (x402 has no chargeback)
    if not result.get("solved") and "error" in result:
        result = await _queue_solve(captcha_type, body, dict(request.headers))
    
    status_code = 200 if result.get("solved") else 408 if "timed out" in result.get("error", "") else 500
    return JSONResponse(content=result, status_code=status_code)

@app.get("/icon.svg")
async def icon():
    return Response(
        content=open("/tmp/captcha-solve-api-icon.svg").read(),
        media_type="image/svg+xml",
    )

@app.get("/.well-known/glama.json")
async def glama_verification():
    return {
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "claim": "glama_claim_LB7BptXsCj5cX4_aN0kLEMNZVZqQiPyD"
    }

@app.get("/.well-known/mcp/server-card.json")
async def server_card():
    return {
        "name": "io.github.drain54/verify-api",
        "title": "Verify API",
        "description": "AI infrastructure claim verification + CAPTCHA solving — pay-per-use via x402. Verify AI model claims and solve Turnstile, hCaptcha, reCAPTCHA, Arkose, Cloudflare.",
        "version": "0.3.0",
        "homepage": "https://smithery.ai/servers/indradarmawan87/verify-api",
        "repository": {
            "type": "git",
            "url": "https://github.com/drain54/verify-api"
        },
        "icon": "https://verify.drain54.my.id/icon.svg",
        "remotes": [
            {"type": "streamable-http", "url": "https://verify.drain54.my.id"}
        ],
        "tools": TOOLS_DEFINITION,
        "pricing": {
            "model": "pay-per-use",
            "currency": "USDC",
            "network": "Base",
            "details": "/v1/captcha-pricing"
        }
    }

@app.get("/health")
async def health():
    return {"ok": True, "facilitator": FACILITATOR, "network": NET, "paid": bool(WALLET)}

# ponytail: path routing to provisioning service — coupled to verify-api lifecycle
PROVISIONING_URL = "http://127.0.0.1:8013"

@app.api_route("/v1/test-accounts/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_provisioning(request: Request, path: str):
    url = f"{PROVISIONING_URL}/v1/test-accounts/{path}"
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.request(method=request.method, url=url, content=body, headers=headers, params=request.query_params)
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
    except Exception as e:
        return JSONResponse(content={"error": f"provisioning unavailable: {type(e).__name__}", "detail": str(e)}, status_code=502)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8012)
