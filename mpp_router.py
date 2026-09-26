# mpp_router.py — MPP (Machine Payments Protocol / RFC 9110 HTTP Auth) APIRouter
import os, json, uuid, base64
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

# Load .env
_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=", 1))

from main import verify, Req
from reader_backend import extract_url
from json_extractor import extract_structured_json
from stealth_fetcher import fetch_stealth
from ssrf_guard import is_safe_url

import mpp
from mpp import Challenge, Credential, Receipt

router = APIRouter(prefix="/mpp", tags=["mpp"])

LOG_PATH = Path(__file__).parent / "usage.jsonl"
WALLET = os.getenv("X402_WALLET", "")
USDC = os.getenv("X402_USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
MPP_SECRET = os.getenv("MPP_SECRET_KEY", "mpp-server-secret-drain54-verify")

PRICES_USD = {
    "standard": "0.01",
    "deep": "0.03",
    "reader": "0.005",
    "extract_json": "0.01",
    "stealth": "0.01",
}

def _log_mpp(event: dict):
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "protocol": "mpp", **event}) + "\n")
    except Exception:
        pass

def _get_realm(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "verify.drain54.my.id"
    return host.split(":")[0]

def _mpp_402_challenge(request: Request, amount: str, description: str, currency: str = USDC, recipient: str = WALLET) -> Response:
    """Create IETF compliant HTTP 402 with WWW-Authenticate: Payment header."""
    realm = _get_realm(request)
    req_payload = {
        "amount": amount,
        "currency": currency,
        "recipient": recipient,
    }
    challenge = Challenge.create(
        secret_key=MPP_SECRET,
        realm=realm,
        method="tempo",
        intent="charge",
        request=req_payload,
        description=description,
    )
    www_auth_value = challenge.to_www_authenticate(realm=realm)
    
    return Response(
        status_code=402,
        content=json.dumps({
            "error": "Payment Required",
            "protocol": "mpp",
            "method": "tempo",
            "intent": "charge",
            "amount": amount,
            "description": description,
        }),
        media_type="application/json",
        headers={
            "WWW-Authenticate": www_auth_value,
            "Paywall": "mpp",
        }
    )

async def _verify_mpp_auth(request: Request, expected_amount: str, description: str) -> tuple[bool, str | None, Response | None]:
    """Parse and verify Payment authorization header. Returns (paid, error_reason, response_if_not_paid)."""
    auth_header = request.headers.get("Authorization") or request.headers.get("Payment-Authorization")
    if not auth_header or not auth_header.startswith("Payment "):
        # Return 402 challenge
        return False, None, _mpp_402_challenge(request, expected_amount, description)
    
    try:
        credential = Credential.from_authorization(auth_header)
        realm = _get_realm(request)
        
        # Verify request amount
        req_b64 = credential.challenge.request
        pad = len(req_b64) % 4
        req_b64_padded = req_b64 + ("=" * (4 - pad)) if pad else req_b64
        decoded_req = json.loads(base64.urlsafe_b64decode(req_b64_padded).decode())
        
        if str(decoded_req.get("amount")) != str(expected_amount):
            return False, "Amount mismatch", JSONResponse(status_code=400, content={"error": "Amount mismatch in credential"})
            
        receipt = Receipt.success(
            reference=f"mpp_ref_{uuid.uuid4().hex[:16]}",
            method=credential.challenge.method or "tempo"
        )
        return True, receipt.to_payment_receipt(), None
    except Exception as e:
        return False, str(e), JSONResponse(status_code=400, content={"error": f"Invalid payment credential: {e}"})

@router.get("/v1/info")
async def mpp_info(request: Request):
    return {
        "status": "online",
        "protocol": "mpp (Machine Payments Protocol)",
        "specification": "draft-ryan-httpauth-payment",
        "supported_methods": ["tempo"],
        "supported_intents": ["charge"],
        "endpoints": [
            {"path": "/mpp/v1/verify", "price_usd": PRICES_USD["standard"]},
            {"path": "/mpp/v1/read", "price_usd": PRICES_USD["reader"]},
            {"path": "/mpp/v1/extract-json", "price_usd": PRICES_USD["extract_json"]},
            {"path": "/mpp/v1/fetch-stealth", "price_usd": PRICES_USD["stealth"]},
        ]
    }

@router.post("/v1/verify")
async def mpp_verify_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    depth = (body.get("depth") or "standard").lower()
    amt = PRICES_USD.get(depth, PRICES_USD["standard"])
    desc = f"AI infrastructure claim verification via MPP - {depth} depth (${amt} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt, desc)
    if not paid:
        _log_mpp({"path": "/mpp/v1/verify", "result": "402_challenge", "remote": request.client.host if request.client else "unknown"})
        return fail_response
    
    try:
        req = Req(**body)
        result = await verify(req)
        _log_mpp({"path": "/mpp/v1/verify", "result": "paid_ok", "remote": request.client.host if request.client else "unknown"})
        headers = {}
        if receipt_header:
            headers["Payment-Receipt"] = receipt_header
        return JSONResponse(content=result, headers=headers)
    except Exception as e:
        _log_mpp({"path": "/mpp/v1/verify", "result": "crash", "error": str(e)})
        return JSONResponse(status_code=500, content={"error": f"Internal error: {e}"})

@router.post("/v1/read")
async def mpp_read_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    amt = PRICES_USD["reader"]
    desc = f"Read and extract clean markdown content from URL via MPP (${amt} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt, desc)
    if not paid:
        _log_mpp({"path": "/mpp/v1/read", "result": "402_challenge", "remote": request.client.host if request.client else "unknown"})
        return fail_response
    
    try:
        url = body.get("url")
        if not url:
            return JSONResponse(status_code=400, content={"error": "Missing 'url' parameter"})
        safe, reason = is_safe_url(url)
        if not safe:
            return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
        include_links = body.get("include_links", True)
        include_images = body.get("include_images", False)
        result = await extract_url(url, include_links=include_links, include_images=include_images)
        headers = {}
        if receipt_header:
            headers["Payment-Receipt"] = receipt_header
        return JSONResponse(content=result, headers=headers)
    except Exception as e:
        _log_mpp({"path": "/mpp/v1/read", "result": "crash", "error": str(e)})
        return JSONResponse(status_code=500, content={"error": f"Internal error: {e}"})

@router.post("/v1/extract-json")
async def mpp_extract_json_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    amt = PRICES_USD["extract_json"]
    desc = f"Extract structured JSON data from webpage via MPP (${amt} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt, desc)
    if not paid:
        _log_mpp({"path": "/mpp/v1/extract-json", "result": "402_challenge", "remote": request.client.host if request.client else "unknown"})
        return fail_response
        
    try:
        url = body.get("url")
        schema_def = body.get("schema")
        if not url or not schema_def:
            return JSONResponse(status_code=400, content={"error": "Missing 'url' or 'schema' parameter"})
        safe, reason = is_safe_url(url)
        if not safe:
            return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
        instructions = body.get("instructions", "")
        result = await extract_structured_json(url, schema_def, instructions)
        headers = {}
        if receipt_header:
            headers["Payment-Receipt"] = receipt_header
        return JSONResponse(content=result, headers=headers)
    except Exception as e:
        _log_mpp({"path": "/mpp/v1/extract-json", "result": "crash", "error": str(e)})
        return JSONResponse(status_code=500, content={"error": f"Internal error: {e}"})

@router.post("/v1/fetch-stealth")
async def mpp_fetch_stealth_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
        
    amt = PRICES_USD["stealth"]
    desc = f"Stealth web fetcher via MPP (${amt} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt, desc)
    if not paid:
        _log_mpp({"path": "/mpp/v1/fetch-stealth", "result": "402_challenge", "remote": request.client.host if request.client else "unknown"})
        return fail_response
        
    try:
        url = body.get("url")
        if not url:
            return JSONResponse(status_code=400, content={"error": "Missing 'url' parameter"})
        safe, reason = is_safe_url(url)
        if not safe:
            return JSONResponse(content={"error": f"SSRF security policy violation: {reason}"}, status_code=403)
        result = await fetch_stealth(url=url, custom_headers=body.get("custom_headers"))
        headers = {}
        if receipt_header:
            headers["Payment-Receipt"] = receipt_header
        return JSONResponse(content=result, headers=headers)
    except Exception as e:
        _log_mpp({"path": "/mpp/v1/fetch-stealth", "result": "crash", "error": str(e)})
        return JSONResponse(status_code=500, content={"error": f"Internal error: {e}"})
