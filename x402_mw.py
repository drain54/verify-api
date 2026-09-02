# x402_mw.py — payment middleware (Step 3)
# Wraps main.py pipeline behind x402 402 flow. NOT a framework — minimal adapter.
import os, json, uuid, httpx, asyncio
from fastapi import FastAPI, Request, Response
from pathlib import Path
_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line=line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=",1))

# import verification pipeline
from main import verify, Req  # noqa: E402

FACILITATOR = os.getenv("X402_FACILITATOR_URL", "https://facilitator.payai.network")
WALLET = os.getenv("X402_WALLET", "")        # payTo address (Base)
USDC = os.getenv("X402_USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")  # Base mainnet USDC
USDC_NAME = os.getenv("X402_USDC_NAME", "USD Coin")  # must match token on-chain name (EIP-712 domain)
NET = os.getenv("X402_NETWORK", "eip155:84532")  # CAIP-2 for reference only
# PayAI V1 network string (hardcoded: "base" or "base-sepolia")
V1_NET = "base-sepolia" if "84532" in NET else "base"
PRICES = {"standard": "1000000", "deep": "3000000"}  # 0.01 / 0.03 USDC (6 decimals)

app = FastAPI()

def _payment_requirements(resource: str, amount: str) -> dict:
    # V1 (PayAI /supported: only x402Version 1, network string "base")
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": V1_NET,
        "maxAmountRequired": amount,
        "resource": resource,  # V1: string URL (objek resource = V2)
        "description": "AI infra claim verification — pay-per-query.",
        "mimeType": "application/json",
        "payTo": WALLET,
        "maxTimeoutSeconds": 60,
        "asset": USDC,
        "extra": {"name": USDC_NAME, "version": "2"},
        "outputSchema": {"input": {
            "type": "http", "method": "POST", "bodyType": "json",
            "body": {"query": "string",
                     "type": "enum(claim_verify|endpoint_check|pricing_check|region_check|error_diagnosis)",
                     "depth": "enum(standard|deep)", "target_url": "string(optional)"}}},
        "extensions": {"bazaar": {
            "serviceName": "Verify API",
            "tags": ["ai", "verification", "claim", "api", "infrastructure", "x402"],
            "iconUrl": "",
            "info": {
                "input": {"type":"http","method":"POST","bodyType":"json",
                          "body":{"query":"string","type":"enum(claim_verify|endpoint_check|pricing_check|region_check|error_diagnosis)","depth":"enum(standard|deep)","target_url":"string(optional)"}},
                "output": {"type":"json","example":{"verdict":"TRUE|FALSE|PARTIALLY_TRUE|CHANGED|UNREACHABLE|BLOCKED|UNVERIFIED","answer":"evidence-backed answer","confidence":0.91,"sources":[{"url":"https://...","type":"official","supports":"..."}],"caveats":[]}}},
            "schema": {}
        }}
    }

@app.post("/v1/verify")
async def paid_verify(request: Request):
    body = await request.json()
    depth = (body.get("depth") or "standard").lower()
    amt = PRICES.get(depth, PRICES["standard"])
    resource = f"{request.url.scheme}://{request.url.netloc}/v1/verify"
    payment_header = request.headers.get("X-PAYMENT")

    # no payment -> 402
    if not payment_header:
        return Response(
            content=json.dumps(_payment_requirements(resource, amt)),
            status_code=402,
            headers={"Content-Type":"application/json",
                     "Accept":"application/x402-payment-v2+json",
                     "Paywall":"x402"},
        )

    # verify via facilitator
    payload = json.loads(payment_header)  # raw JSON default
    if payment_header.startswith("0x"):
        payload = json.loads(bytes.fromhex(payment_header[2:]).decode())
    # normalize: enforce V1 shape for PayAI (facilitator is V1-only)
    if payload.get("accepted") and "x402Version" in payload and payload.get("x402Version", 1) != 1:
        payload = {"x402Version": 1, "scheme": payload.get("scheme", "exact"),
                   "network": payload["accepted"].get("network", NET),
                   "payload": payload.get("payload", {})}
    if "x402Version" in payload:
        payload["x402Version"] = 1
    # ponytail: real signature hex decode handled by x402 client lib; accept raw JSON for dev/test
    # 500 di paid path — ini bug yang perlu difix
    # tapi untuk sekarang: wrap biar response bisa dibaca
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            vr = await client.post(f"{FACILITATOR}/verify", json={
                "paymentPayload": payload,
                "paymentRequirements": {
                    "scheme":"exact","network":V1_NET,"maxAmountRequired":amt,
                    "resource":resource,"description":"verify","mimeType":"application/json",
                    "payTo":WALLET,"maxTimeoutSeconds":60,"asset":USDC,
                    "extra":{"name":USDC_NAME,"version":"2"}}},
                headers={"Content-Type":"application/json"})
            ver = vr.json()
            if not ver.get("isValid"):
                return Response(content=json.dumps({"error":"invalid payment",
                    "invalidReason":ver.get("invalidReason","")}),
                    status_code=402,
                    headers={"Content-Type":"application/json"})
            # settle
            sr = await client.post(f"{FACILITATOR}/settle",
                json={"paymentPayload":payload,
                      "paymentRequirements":{"scheme":"exact","network":V1_NET,"amount":amt,
                        "asset":USDC,"payTo":WALLET,"maxTimeoutSeconds":60,
                        "extra":{"name":USDC_NAME,"version":"2"}}},
                headers={"Content-Type":"application/json"})
            settled = sr.json()
            if not settled.get("success"):
                return Response(content=json.dumps({"error":"settle failed",
                    "errorReason":settled.get("errorReason","")}),
                    status_code=502, headers={"Content-Type":"application/json"})
        # payment ok -> run pipeline
        req = Req(**body)
        result = await verify(req)
        return result
    except Exception as e:
        return Response(content=json.dumps({"error":f"paid path crashed: {type(e).__name__}: {e}"}),
            status_code=500, headers={"Content-Type":"application/json"})

@app.get("/health")
async def health():
    return {"ok": True, "facilitator": FACILITATOR, "network": NET, "paid": bool(WALLET)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8012)
