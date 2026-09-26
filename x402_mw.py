# x402_mw.py — payment middleware + CAPTCHA solving API
import os, json, uuid, httpx, asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response, Query
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from contextlib import asynccontextmanager
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=", 1))

from main import verify, Req, search  # noqa: E402
from reader_backend import extract_url  # noqa: E402
from json_extractor import extract_structured_json  # noqa: E402
from stealth_fetcher import fetch_stealth  # noqa: E402
from metabolism import metabolism  # noqa: E402
from ssrf_guard import is_safe_url  # noqa: E402
from mpp_router import router as mpp_router  # noqa: E402

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

def _single_requirement(resource: str, amount: str, description: str = "AI infra claim verification — pay-per-query.") -> dict:
    return {
        "scheme": "exact",
        "network": V1_NET,
        "maxAmountRequired": str(amount),
        "resource": resource,
        "description": description,
        "mimeType": "application/json",
        "payTo": WALLET,
        "maxTimeoutSeconds": 120,
        "asset": USDC,
        "extra": {"name": USDC_NAME, "version": "2"},
    }

def _payment_requirements(resource: str, amount: str, description: str = "AI infra claim verification — pay-per-query.") -> dict:
    return _single_requirement(resource, amount, description)

def _402_response(resource: str, amount: str, description: str, error: str = "Payment Required", invalid_reason: str | None = None) -> JSONResponse:
    challenge = {
        "x402Version": 1,
        "error": error,
        "accepts": [_single_requirement(resource, amount, description)],
    }
    if invalid_reason:
        challenge["invalidReason"] = invalid_reason
    return JSONResponse(
        content=challenge,
        status_code=402,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/x402-payment-v2+json",
            "Paywall": "x402"
        }
    )

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

async def _metabolism_loop():
    while True:
        try:
            await metabolism.refresh_balance(force=True)
        except Exception:
            pass
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the queue processor and metabolism loop on startup."""
    task = asyncio.create_task(_process_queue())
    meta_task = asyncio.create_task(_metabolism_loop())
    yield
    task.cancel()
    meta_task.cancel()

app = FastAPI(lifespan=lifespan)
app.include_router(mpp_router)

TOOLS_DEFINITION = [
    {
        "name": "verify_ai_claim",
        "description": "Verify whether an AI model, API provider, pricing claim, or infrastructure assertion is true today using live web evidence. Returns deterministic verdicts (TRUE, FALSE, PARTIALLY_TRUE, UNREACHABLE, UNVERIFIED) with cited sources and confidence scores. Requires x402 micropayment (0.01 USDC on Base).\n\nWhen to use: Fact-checking an AI provider's claims, pricing, or model availability.\nWhen NOT to use: Do NOT use for general open-ended web search, coding assistance, or non-AI claim verification.\n\nParameters:\n- `query` (string, required): The exact claim or assertion to verify (5-500 chars), e.g. 'Is GLM-5.3 Flash free on ZenMux?'.\n- `depth` (string, optional, default 'standard'): Verification depth. 'standard' executes fast single-pass search (0.01 USDC); 'deep' conducts multi-source cross-examination (0.03 USDC).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 500,
                    "description": "The specific AI infrastructure claim or assertion to verify.",
                    "examples": ["Is GLM-5.3 Flash free on ZenMux?", "Does OpenRouter still offer free tier models?"]
                },
                "depth": {
                    "type": "string",
                    "enum": ["standard", "deep"],
                    "default": "standard",
                    "description": "Verification depth: 'standard' (0.01 USDC) or 'deep' (0.03 USDC).",
                    "examples": ["standard", "deep"]
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
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "check_endpoint_health",
        "description": "Probe and verify the reachability, HTTP status code, latency, and operational health of an AI API endpoint or web service. Follows redirects with a 15-second timeout. Requires x402 micropayment (0.01 USDC on Base).\n\nWhen to use: Check if a specific API URL or model endpoint is online, responding, or returning 5xx/402 errors.\nWhen NOT to use: Do NOT use for general domain WHOIS or DNS record lookups.\n\nParameters:\n- `url` (string, required): Target HTTP/HTTPS endpoint URL to probe (8-1000 chars), e.g. 'https://api.openai.com/v1/models'.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 8,
                    "maxLength": 1000,
                    "description": "The target API or service endpoint URL to probe.",
                    "examples": ["https://openrouter.ai/api/v1/models", "https://api.together.xyz/v1/health"]
                }
            },
            "required": ["url"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["TRUE", "UNREACHABLE", "BLOCKED"], "description": "Reachability status verdict"},
                "http_status": {"type": "integer", "description": "HTTP status code returned by the target endpoint"},
                "latency_ms": {"type": "integer", "description": "Response latency in milliseconds"},
                "checked_at": {"type": "string", "description": "ISO 8601 timestamp of the check"}
            },
            "required": ["verdict", "http_status", "latency_ms"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "solve_captcha",
        "description": "Solve web CAPTCHA challenges (Turnstile, hCaptcha, reCAPTCHA v2, Arkose, Cloudflare) and return a valid solution token. Automatically retries once on failure without double charging. Requires x402 payment on Base.\n\nWhen to use: Use when an agent encounters a bot wall or CAPTCHA challenge during automated web workflows.\nWhen NOT to use: Do NOT use for non-CAPTCHA auth, 2FA/OTP codes, or general login forms.\n\nParameters:\n- `type` (string, required): CAPTCHA type ('turnstile', 'hcaptcha', 'recaptcha', 'arkose', 'cloudflare').\n- `sitekey` (string, required): Public sitekey extracted from the target page DOM.\n- `url` (string, required): Full target webpage URL hosting the challenge.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["turnstile", "hcaptcha", "recaptcha", "arkose", "cloudflare"],
                    "description": "The specific type of CAPTCHA challenge encountered on the target page.",
                    "examples": ["turnstile", "recaptcha", "hcaptcha"]
                },
                "sitekey": {
                    "type": "string",
                    "minLength": 5,
                    "maxLength": 256,
                    "description": "The CAPTCHA sitekey parameter extracted from the target page DOM or iframe.",
                    "examples": ["0x4AAAAAAAx..."]
                },
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 8,
                    "maxLength": 1000,
                    "description": "The full target page URL where the CAPTCHA challenge is hosted.",
                    "examples": ["https://example.com/login"]
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
    },
    {
        "name": "get_captcha_pricing",
        "description": "Retrieve current x402 pricing per 1,000 CAPTCHA solves across all supported types (Turnstile, hCaptcha, reCAPTCHA v2, Arkose, Cloudflare). Free endpoint with zero parameters.\n\nWhen to use: Check current rates and atomic USDC requirements before calling solve_captcha.\nWhen NOT to use: Do NOT use to submit or solve CAPTCHA challenges.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": []
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "pricing": {
                    "type": "object",
                    "description": "Map of CAPTCHA type to cost in USDC atomic units per 1,000 solves"
                },
                "currency": {"type": "string", "description": "Payment currency (USDC)"},
                "network": {"type": "string", "description": "Target blockchain network (Base)"}
            },
            "required": ["pricing", "currency", "network"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "read_web_page",
        "description": "Extract clean, readable Markdown and metadata from any public webpage for LLM ingestion, stripping ads, popups, and navigational clutter. Returns clean markdown, title, description, character count, and estimated tokens. Requires x402 micropayment (0.005 USDC on Base).\n\nWhen to use: Ingesting articles, blog posts, documentation, or news pages into LLM context.\nWhen NOT to use: Do NOT use for raw binary files (PDF/images), authenticated pages behind a login, or single-page apps that require heavy JavaScript rendering.\n\nParameters:\n- `url` (string, required): Full target webpage URL (e.g. 'https://news.ycombinator.com').\n- `include_links` (boolean, optional, default true): Whether to preserve markdown hyperlinks.\n- `include_images` (boolean, optional, default false): Whether to preserve image markdown links.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 8,
                    "maxLength": 1000,
                    "description": "Full target webpage URL to extract markdown from.",
                    "examples": ["https://news.ycombinator.com", "https://en.wikipedia.org/wiki/Artificial_intelligence"]
                },
                "include_links": {
                    "type": "boolean",
                    "default": True,
                    "description": "Preserve markdown hyperlinks."
                },
                "include_images": {
                    "type": "boolean",
                    "default": False,
                    "description": "Preserve markdown image tags."
                }
            },
            "required": ["url"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Target webpage URL"},
                "title": {"type": "string", "description": "Extracted page title"},
                "description": {"type": "string", "description": "Extracted meta description"},
                "content": {"type": "string", "description": "Clean extracted Markdown content"},
                "length": {"type": "integer", "description": "Content length in characters"},
                "estimated_tokens": {"type": "integer", "description": "Estimated LLM tokens (~len/4)"},
                "elapsed_ms": {"type": "integer", "description": "Extraction time in milliseconds"}
            },
            "required": ["url", "content", "length"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "search_web",
        "description": "Execute live web searches using multi-engine chain (TinyFish, DuckDuckGo, Jina) without monthly API subscriptions. Returns fresh source URLs, titles, and snippets. Requires x402 micropayment (0.005 USDC on Base).\n\nWhen to use: Real-time web browsing and information retrieval for AI agents.\nWhen NOT to use: Do NOT use for deep recursive crawling of entire sites.\n\nParameters:\n- `query` (string, required): Search query (3-300 chars).\n- `limit` (integer, optional, default 5): Maximum number of search results to return (1-10).",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 300,
                    "description": "Web search query."
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                    "description": "Number of results to return."
                }
            },
            "required": ["query"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Original search query"},
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": "string"},
                            "snippet": {"type": "string"},
                            "type": {"type": "string"}
                        }
                    }
                },
                "count": {"type": "integer", "description": "Number of returned results"}
            },
            "required": ["query", "results"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "extract_json_from_web",
        "description": "Extract structured JSON data matching a custom schema directly from any webpage. Extracts clean content and processes schema mapping via fast LLM parsing. Requires x402 micropayment (0.015 USDC on Base).\n\nWhen to use: Scraping structured data (product specs, prices, jobs, articles) into clean JSON.\nWhen NOT to use: Do NOT use for general open-ended chat without a defined schema.\n\nParameters:\n- `url` (string, required): Target webpage URL.\n- `schema` (object, required): JSON object describing fields or schema to extract.\n- `instructions` (string, optional): Specific guidance for extraction.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 8,
                    "maxLength": 1000,
                    "description": "Target webpage URL."
                },
                "schema": {
                    "type": "object",
                    "description": "Schema definition or list of fields to extract."
                },
                "instructions": {
                    "type": "string",
                    "description": "Optional instructions for parsing or field formatting."
                }
            },
            "required": ["url", "schema"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "title": {"type": "string"},
                "data": {"type": "object", "description": "Extracted JSON fields"},
                "elapsed_ms": {"type": "integer"}
            },
            "required": ["url", "data"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    },
    {
        "name": "fetch_stealth_web",
        "description": "Fetch webpage HTML with modern browser TLS and header impersonation (Sec-Ch-Ua, realistic headers) to bypass bot protection and detect anti-bot challenges. Requires x402 micropayment (0.01 USDC on Base).\n\nWhen to use: Fetching websites that block standard cURL or basic HTTP libraries with 403 Forbidden.\nWhen NOT to use: Do NOT use for downloading giant binary files (videos, zip archives).\n\nParameters:\n- `url` (string, required): Target webpage URL.\n- `custom_headers` (object, optional): Additional HTTP headers to pass along.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 8,
                    "maxLength": 1000,
                    "description": "Target webpage URL."
                },
                "custom_headers": {
                    "type": "object",
                    "description": "Optional custom headers."
                }
            },
            "required": ["url"]
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "http_status": {"type": "integer"},
                "title": {"type": "string"},
                "challenge_detected": {"type": "boolean"},
                "challenge_type": {"type": "string"},
                "content_length": {"type": "integer"},
                "html": {"type": "string"},
                "elapsed_ms": {"type": "integer"}
            },
            "required": ["url", "http_status", "html"]
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True
        }
    }
]

# In-memory session message queues for MCP SSE clients
_sse_sessions: dict[str, asyncio.Queue] = {}

def _mcp_rpc_response(body: dict):
    if not isinstance(body, dict):
        return None
    method = body.get("method")
    has_id = "id" in body
    req_id = body.get("id")

    # Only process if this is actually a JSON-RPC request
    if "jsonrpc" not in body and not method:
        return None

    # JSON-RPC Notification (no id) -> signal 204 No Content
    if not has_id or (method and method.startswith("notifications/")):
        return {"_is_notification": True}

    if method == "initialize":
        client_version = body.get("params", {}).get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "io.github.drain54/verify-api",
                    "version": "1.0.0"
                }
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
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Tool '{tool_name}' verified active on server. Requires x402 payment header on Base USDC for live execution."
                    }
                ],
                "isError": False
            }
        }
    elif method:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }
    return None

@app.get("/")
@app.get("/mcp")
@app.get("/v1/verify")
async def root_mcp_info():
    return JSONResponse(content={
        "name": "io.github.drain54/verify-api",
        "status": "ok",
        "mcp_endpoint": "https://verify.drain54.my.id/v1/verify",
        "sse_endpoint": "https://verify.drain54.my.id/sse",
        "card": "https://verify.drain54.my.id/.well-known/mcp/server-card.json"
    })

@app.post("/")
@app.post("/mcp")
async def root_mcp_post(request: Request):
    try:
        body = await request.json()
        with open("/tmp/glama_last_body.json", "w") as f:
            f.write(json.dumps(body) + "\n")
        resp = _mcp_rpc_response(body)
        if resp is not None:
            if resp.get("_is_notification"):
                return Response(status_code=204)
            return JSONResponse(content=resp)
    except Exception as e:
        _log({"path": "mcp_post_err", "err": str(e)})
        pass
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "error": {"code": -32700, "message": "Parse error / Invalid JSON-RPC request"}
        },
        status_code=400
    )

@app.get("/sse")
async def mcp_sse_endpoint(request: Request):
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sse_sessions[session_id] = queue

    async def event_generator():
        try:
            # MCP SSE handshake event: advertise message endpoint with session ID
            yield f"event: endpoint\ndata: /messages?sessionId={session_id}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _sse_sessions.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@app.post("/messages")
async def mcp_sse_messages(request: Request, sessionId: str = Query(...)):
    if sessionId not in _sse_sessions:
        return JSONResponse(content={"error": "Session not found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    resp = _mcp_rpc_response(body)
    if resp is not None and not resp.get("_is_notification"):
        await _sse_sessions[sessionId].put(resp)
    return Response(status_code=202)

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
    desc = f"AI infrastructure claim verification — {depth} depth (1.00 USDC standard / 3.00 USDC deep)."
    payment_header = request.headers.get("X-PAYMENT")
    if not payment_header:
        _log({"path": "/v1/verify", "method": body.get("method") or "unknown", "remote": request.client.host if request.client else "unknown", "result": "402_no_payment"})
        return _402_response(resource, amt, desc)
    try:
        paid, reason = await _verify_payment(request, amt, desc)
        if not paid:
            return _402_response(resource, amt, desc, error="Payment verification failed", invalid_reason=reason)
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
    try:
        body = await request.json()
    except Exception:
        body = {}
    captcha_type = body.get("type", "turnstile")
    amount = str(CAPTCHA_PRICING.get(captcha_type, 1000))
    resource = _get_resource(request)
    
    # Check payment first (standard x402 probe response)
    payment_header = request.headers.get("X-PAYMENT")
    captcha_description = f"CAPTCHA solve — {captcha_type}."
    if not payment_header:
        _log({"path": "/v1/solve-captcha", "type": captcha_type, "remote": request.client.host if request.client else "unknown", "result": "402_no_payment"})
        return _402_response(resource, amount, captcha_description)
    
    if captcha_type not in CAPTCHA_PRICING:
        return JSONResponse(
            content={"error": f"unsupported CAPTCHA type: {captcha_type}", "supported_types": list(CAPTCHA_PRICING.keys())},
            status_code=400,
        )
    
    try:
        paid, reason = await _verify_payment(request, amount, captcha_description)
        if not paid:
            return _402_response(resource, amount, captcha_description, error="Payment verification failed", invalid_reason=reason)
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

@app.get("/v1/organism/state")
async def get_organism_state():
    """Returns the live economic metabolism of the Autonomous Revenue Organism."""
    state = await metabolism.refresh_balance()
    return JSONResponse(content=state)

@app.post("/v1/read")
async def read_page(request: Request):
    """Extract clean Markdown from a webpage for LLM ingestion with x402 payment."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    base_amount = 5000  # 0.005 USDC
    amount = str(metabolism.compute_dynamic_price(base_amount))
    resource = _get_resource(request)
    description = f"Web-to-Markdown LLM Reader — clean markdown extraction ({int(amount)/1e6:.4f} USDC)."
    
    payment_header = request.headers.get("X-PAYMENT")
    remote_ip = request.client.host if request.client else "unknown"
    if not payment_header:
        _log({"path": "/v1/read", "url": str(body.get("url", ""))[:60], "remote": remote_ip, "result": "402_no_payment"})
        return _402_response(resource, amount, description)
    
    url = body.get("url")
    if not url:
        return JSONResponse(content={"error": "missing 'url' field in request body"}, status_code=400)
    
    safe, reason = is_safe_url(url)
    if not safe:
        return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
    
    try:
        paid, reason = await _verify_payment(request, amount, description)
        if not paid:
            return _402_response(resource, amount, description, error="Payment verification failed", invalid_reason=reason)
    except Exception as e:
        _log({"path": "/v1/read", "url": url[:60], "remote": remote_ip, "result": "500_payment_error", "error": str(e)})
        return JSONResponse(content={"error": f"payment verification failed: {e}"}, status_code=500)
    
    include_links = body.get("include_links", True)
    include_images = body.get("include_images", False)
    result = await extract_url(url, include_links=include_links, include_images=include_images)
    _log({"path": "/v1/read", "url": url[:60], "remote": remote_ip, "result": "paid_ok"})
    return JSONResponse(content=result)

@app.post("/v1/search")
async def search_endpoint(request: Request):
    """Execute live web searches with x402 payment."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    base_amount = 5000  # 0.005 USDC
    amount = str(metabolism.compute_dynamic_price(base_amount))
    resource = _get_resource(request)
    description = f"Live Web Search API — real-time web evidence ({int(amount)/1e6:.4f} USDC)."
    payment_header = request.headers.get("X-PAYMENT")
    remote_ip = request.client.host if request.client else "unknown"
    if not payment_header:
        _log({"path": "/v1/search", "query": str(body.get("query", ""))[:60], "remote": remote_ip, "result": "402_no_payment"})
        return _402_response(resource, amount, description)
    
    query = body.get("query")
    if not query:
        return JSONResponse(content={"error": "missing 'query' field in request body"}, status_code=400)
    limit = min(int(body.get("limit", 5)), 10)
    try:
        paid, reason = await _verify_payment(request, amount, description)
        if not paid:
            return _402_response(resource, amount, description, error="Payment verification failed", invalid_reason=reason)
    except Exception as e:
        _log({"path": "/v1/search", "query": query[:60], "remote": remote_ip, "result": "500_payment_error", "error": str(e)})
        return JSONResponse(content={"error": f"payment verification failed: {e}"}, status_code=500)
    
    results = search(query, limit)
    _log({"path": "/v1/search", "query": query[:60], "remote": remote_ip, "result": "paid_ok"})
    return JSONResponse(content={"query": query, "results": results, "count": len(results)})

@app.post("/v1/extract-json")
async def extract_json_endpoint(request: Request):
    """Extract structured JSON from webpage with x402 payment."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    base_amount = 15000  # 0.015 USDC
    amount = str(metabolism.compute_dynamic_price(base_amount))
    resource = _get_resource(request)
    description = f"Web-to-JSON Structured Extraction API — schema-driven data extraction ({int(amount)/1e6:.4f} USDC)."
    payment_header = request.headers.get("X-PAYMENT")
    remote_ip = request.client.host if request.client else "unknown"
    if not payment_header:
        _log({"path": "/v1/extract-json", "url": str(body.get("url", ""))[:60], "remote": remote_ip, "result": "402_no_payment"})
        return _402_response(resource, amount, description)
    
    url = body.get("url")
    schema_def = body.get("schema")
    if not url or schema_def is None:
        return JSONResponse(content={"error": "missing 'url' or 'schema' field in request body"}, status_code=400)
    
    safe, reason = is_safe_url(url)
    if not safe:
        return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
    try:
        paid, reason = await _verify_payment(request, amount, description)
        if not paid:
            return _402_response(resource, amount, description, error="Payment verification failed", invalid_reason=reason)
    except Exception as e:
        _log({"path": "/v1/extract-json", "url": url[:60], "remote": remote_ip, "result": "500_payment_error", "error": str(e)})
        return JSONResponse(content={"error": f"payment verification failed: {e}"}, status_code=500)
    
    instructions = body.get("instructions", "")
    result = await extract_structured_json(url, schema_def, instructions)
    _log({"path": "/v1/extract-json", "url": url[:60], "remote": remote_ip, "result": "paid_ok"})
    return JSONResponse(content=result)

@app.post("/v1/fetch-stealth")
async def fetch_stealth_endpoint(request: Request):
    """Fetch webpage HTML with stealth headers & bot protection detection with x402 payment."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    base_amount = 10000  # 0.010 USDC
    amount = str(metabolism.compute_dynamic_price(base_amount))
    resource = _get_resource(request)
    description = f"Stealth Web Fetcher API — anti-bot bypass & header impersonation ({int(amount)/1e6:.4f} USDC)."
    payment_header = request.headers.get("X-PAYMENT")
    remote_ip = request.client.host if request.client else "unknown"
    if not payment_header:
        _log({"path": "/v1/fetch-stealth", "url": str(body.get("url", ""))[:60], "remote": remote_ip, "result": "402_no_payment"})
        return _402_response(resource, amount, description)
    
    url = body.get("url")
    if not url:
        return JSONResponse(content={"error": "missing 'url' field in request body"}, status_code=400)
    
    safe, reason = is_safe_url(url)
    if not safe:
        return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
    try:
        paid, reason = await _verify_payment(request, amount, description)
        if not paid:
            return _402_response(resource, amount, description, error="Payment verification failed", invalid_reason=reason)
    except Exception as e:
        _log({"path": "/v1/fetch-stealth", "url": url[:60], "remote": remote_ip, "result": "500_payment_error", "error": str(e)})
        return JSONResponse(content={"error": f"payment verification failed: {e}"}, status_code=500)
    
    custom_headers = body.get("custom_headers")
    result = await fetch_stealth(url, custom_headers=custom_headers)
    _log({"path": "/v1/fetch-stealth", "url": url[:60], "remote": remote_ip, "result": "paid_ok"})
    return JSONResponse(content=result)

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

@app.get("/.well-known/mpp.json")
async def mpp_discovery_manifest():
    manifest_path = Path(__file__).parent / "mpp_manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text())
    return JSONResponse(status_code=404, content={"error": "mpp manifest not found"})

@app.get("/.well-known/mcp/server-card.json")
async def server_card():
    return {
        "name": "io.github.drain54/verify-api",
        "title": "Verify API & Agent Tools Suite",
        "description": "Multi-utility AI Agent suite: Claim Verification + Web Reader + Live Search + JSON Extraction + Stealth Fetch + CAPTCHA Solving — pay-per-use via x402 on Base USDC.",
        "version": "1.0.0",
        "license": "MIT",
        "author": {
            "name": "drain54",
            "url": "https://github.com/drain54",
            "email": "indradarmawan87@gmail.com"
        },
        "publisher": {
            "name": "drain54",
            "url": "https://github.com/drain54"
        },
        "support": {
            "url": "https://github.com/drain54/verify-api/issues",
            "email": "indradarmawan87@gmail.com"
        },
        "homepage": "https://smithery.ai/servers/indradarmawan87/verify-api",
        "repository": {
            "type": "git",
            "url": "https://github.com/drain54/verify-api"
        },
        "icon": "https://verify.drain54.my.id/icon.svg",
        "remotes": [
            {"type": "streamable-http", "url": "https://verify.drain54.my.id"},
            {"type": "sse", "url": "https://verify.drain54.my.id/sse"}
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

@app.get("/v1/remediate/approve")
async def approve_remediation(action: str, token: str):
    """Execute a Poci remediation action after validating a signed one-time token."""
    import remediate as R
    v = R.verify_approval_token(action, token)
    if not v.get("ok"):
        R._log({"event": "approve_rejected", "action": action, "reason": v.get("reason")})
        return JSONResponse(content={"ok": False, "error": v.get("reason")}, status_code=403)
    if action not in R.ACTIONS:
        return JSONResponse(content={"ok": False, "error": "unknown_action"}, status_code=400)

    # restart_mw would kill the process serving this request — defer it.
    if action == "restart_mw":
        import threading
        def _deferred():
            import time as _t
            _t.sleep(2)
            R._log({"event": "deferred_restart_mw", "approved": True})
            R.action_restart_mw()
        threading.Thread(target=_deferred, daemon=True).start()
        result = {"ok": True, "detail": "restart_mw scheduled in 2s (current worker will be replaced)"}
    else:
        result = R.ACTIONS[action]()

    R._log({"event": "approve_executed", "action": action, "result": result})
    return JSONResponse(content={"ok": True, "action": action, "result": result})

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
