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
import mpp.methods.tempo as tempo
from mpp.methods.tempo.intents import ChargeIntent

router = APIRouter(prefix="/mpp", tags=["mpp"])

LOG_PATH = Path(__file__).parent / "usage.jsonl"
WALLET = os.getenv("X402_WALLET", "0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98")
# Official Tempo L1 USDC token contract
TEMPO_USDC = getattr(tempo, "USDC", "0x20C000000000000000000000b9537d11c60E8b50")
TEMPO_CHAIN_ID = getattr(tempo, "CHAIN_ID", 4217)
MPP_SECRET = os.getenv("MPP_SECRET_KEY", "mpp-server-secret-drain54-verify")

# Amounts in Tempo USDC units (micro-USDC: 1 USD = 1,000,000 units)
PRICES_UNITS = {
    "standard": 10000,    # $0.010 USD
    "deep": 30000,        # $0.030 USD
    "reader": 5000,       # $0.005 USD
    "extract_json": 10000,# $0.010 USD
    "stealth": 10000,     # $0.010 USD
}

# Live charge intent instance bound to Tempo Mainnet RPC
_tempo_charge_intent = ChargeIntent(chain_id=TEMPO_CHAIN_ID)

def _log_mpp(event: dict):
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "protocol": "mpp", **event}) + "\n")
    except Exception:
        pass

def _get_realm(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "verify.drain54.my.id"
    return host.split(":")[0]

def _mpp_402_challenge(request: Request, amount_units: int, description: str, currency: str = TEMPO_USDC, recipient: str = WALLET) -> Response:
    """Create IETF compliant HTTP 402 with WWW-Authenticate: Payment header."""
    realm = _get_realm(request)
    req_payload = {
        "amount": str(amount_units),
        "currency": currency,
        "recipient": recipient,
    }
    # RFC 3339 timestamp (expires in 15 minutes)
    expires_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + 900, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    
    challenge = Challenge.create(
        secret_key=MPP_SECRET,
        realm=realm,
        method="tempo",
        intent="charge",
        request=req_payload,
        expires=expires_at,
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
            "amount": str(amount_units / 1_000_000),
            "amount_units": str(amount_units),
            "currency": currency,
            "chain_id": TEMPO_CHAIN_ID,
            "description": description,
        }),
        media_type="application/json",
        headers={
            "WWW-Authenticate": www_auth_value,
            "Paywall": "mpp",
        }
    )

async def _verify_mpp_auth(request: Request, expected_amount_units: int, description: str) -> tuple[bool, str | None, Response | None]:
    """Parse and verify Payment authorization header via live Tempo RPC settlement.
    Returns (paid, receipt_header_if_paid, response_if_not_paid)."""
    auth_header = request.headers.get("Authorization") or request.headers.get("Payment-Authorization")
    if not auth_header or not auth_header.startswith("Payment "):
        # Return 402 challenge
        return False, None, _mpp_402_challenge(request, expected_amount_units, description)
    
    try:
        credential = Credential.from_authorization(auth_header)
        realm = _get_realm(request)
        
        # Verify challenge authenticity against server secret if available
        if hasattr(credential.challenge, "verify") and callable(getattr(credential.challenge, "verify")):
            valid_hmac = credential.challenge.verify(MPP_SECRET, realm)
            if not valid_hmac:
                return False, None, JSONResponse(status_code=400, content={"error": "Invalid challenge HMAC signature"})
        
        # Decode request payload
        req_b64 = credential.challenge.request
        pad = len(req_b64) % 4
        req_b64_padded = req_b64 + ("=" * (4 - pad)) if pad else req_b64
        decoded_req = json.loads(base64.urlsafe_b64decode(req_b64_padded).decode())
        
        # Verify amount & recipient
        req_amount = str(decoded_req.get("amount", ""))
        if req_amount != str(expected_amount_units):
            return False, None, JSONResponse(
                status_code=400,
                content={"error": f"Amount mismatch: expected {expected_amount_units} units, got {req_amount}"}
            )
            
        # Live Settlement Verification via Tempo RPC
        receipt = await _tempo_charge_intent.broadcast(credential, decoded_req)
        receipt_header = receipt.to_payment_receipt()
        
        return True, receipt_header, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _log_mpp({"event": "mpp_verification_failed", "error": error_msg})
        return False, None, JSONResponse(status_code=402, content={"error": f"Payment settlement failed: {error_msg}"})

@router.get("/v1/info")
async def mpp_info(request: Request):
    return {
        "status": "online",
        "protocol": "mpp (Machine Payments Protocol)",
        "specification": "draft-ryan-httpauth-payment",
        "supported_methods": ["tempo"],
        "supported_intents": ["charge"],
        "settlement": {
            "chain_id": TEMPO_CHAIN_ID,
            "currency": TEMPO_USDC,
            "recipient": WALLET,
            "rpc_url": "https://rpc.tempo.xyz"
        },
        "endpoints": [
            {"path": "/mpp/v1/verify", "price_usd": "0.01", "units": PRICES_UNITS["standard"]},
            {"path": "/mpp/v1/read", "price_usd": "0.005", "units": PRICES_UNITS["reader"]},
            {"path": "/mpp/v1/extract-json", "price_usd": "0.01", "units": PRICES_UNITS["extract_json"]},
            {"path": "/mpp/v1/fetch-stealth", "price_usd": "0.01", "units": PRICES_UNITS["stealth"]},
        ]
    }

@router.post("/v1/verify")
async def mpp_verify_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    depth = (body.get("depth") or "standard").lower()
    amt_units = PRICES_UNITS.get(depth, PRICES_UNITS["standard"])
    desc = f"AI infrastructure claim verification via MPP - {depth} depth (${amt_units/1e6:.4f} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt_units, desc)
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
    
    amt_units = PRICES_UNITS["reader"]
    desc = f"Read and extract clean markdown content from URL via MPP (${amt_units/1e6:.4f} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt_units, desc)
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
        
    amt_units = PRICES_UNITS["extract_json"]
    desc = f"Extract structured JSON data from webpage via MPP (${amt_units/1e6:.4f} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt_units, desc)
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
        
    amt_units = PRICES_UNITS["stealth"]
    desc = f"Stealth web fetcher via MPP (${amt_units/1e6:.4f} USD)."
    
    paid, receipt_header, fail_response = await _verify_mpp_auth(request, amt_units, desc)
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
