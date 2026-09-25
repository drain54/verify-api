import os, json, time, httpx, pathlib
from eth_account import Account
from eth_utils import to_bytes

env = {}
for l in pathlib.Path(".env").read_text().splitlines():
    if "=" in l and not l.startswith("#"):
        env.setdefault(*l.split("=", 1))

acct = Account.from_key(env["EVM_PRIVATE_KEY"])
FACILITATOR = os.environ.get("X402_FACILITATOR_URL") or env.get("X402_FACILITATOR_URL", "https://facilitator.payai.network")
API = "https://verify.drain54.my.id"

def chain_id(net: str) -> int:
    return 84532 if "84532" in net or net == "base-sepolia" else 8453

def sign_payment(reqs: dict, nonce=None):
    nonce = nonce or ("0x" + os.urandom(32).hex())
    now = int(time.time())
    extra = reqs.get("extra", {})
    domain = {
        "name": extra.get("name", "USD Coin"),
        "version": extra.get("version", "2"),
        "chainId": chain_id(reqs.get("network", "base")),
        "verifyingContract": reqs["asset"]
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"}
        ]
    }
    message = {
        "from": acct.address,
        "to": reqs["payTo"],
        "value": int(reqs["maxAmountRequired"]),
        "validAfter": now - 60,
        "validBefore": now + 3600,
        "nonce": to_bytes(hexstr=nonce)
    }
    sig = Account.sign_typed_data(acct.key, domain, types, message).signature.hex()
    return {
        "x402Version": reqs.get("x402Version", 1),
        "scheme": reqs.get("scheme", "exact"),
        "network": reqs.get("network", "base"),
        "payload": {
            "signature": f"0x{sig}",
            "authorization": {
                "from": acct.address,
                "to": reqs["payTo"],
                "value": reqs["maxAmountRequired"],
                "validAfter": str(now - 60),
                "validBefore": str(now + 3600),
                "nonce": nonce
            }
        },
        "extensions": reqs.get("extensions", {})
    }

def main():
    url = f"{API}/v1/read"
    body = {"url": "https://example.com"}

    print("1) Calling endpoint without payment to get 402 requirements...")
    r = httpx.post(url, json=body, timeout=30)
    print("   Status:", r.status_code)
    if r.status_code != 402:
        print("   Unexpected response:", r.text[:200])
        return

    reqs = r.json()
    print(f"2) Requirements: resource={reqs.get('resource')} amount={reqs.get('maxAmountRequired')} ({int(reqs.get('maxAmountRequired'))/1e6} USDC)")

    print("3) Signing EIP-3009 transfer authorization...")
    payload = sign_payment(reqs)
    print(f"   Signed by: {acct.address}")

    payreqs = {k: reqs[k] for k in reqs if k in (
        "scheme", "network", "maxAmountRequired", "resource", "description",
        "mimeType", "payTo", "maxTimeoutSeconds", "asset", "extra", "outputSchema"
    )}

    print("4) Facilitator /verify...")
    vr = httpx.post(
        f"{FACILITATOR}/verify",
        json={"paymentPayload": payload, "paymentRequirements": payreqs},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    vj = vr.json()
    print("   isValid:", vj.get("isValid"), "| invalidReason:", vj.get("invalidReason", ""))
    print("   EXTENSION-RESPONSES:", vr.headers.get("extension-responses") or "(none)")

    if not vj.get("isValid"):
        print("   Verification failed!")
        return

    print("5) Facilitator /settle (broadcasting on-chain Base transaction)...")
    sr = httpx.post(
        f"{FACILITATOR}/settle",
        json={"paymentPayload": payload, "paymentRequirements": payreqs},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    sj = sr.json()
    print("   success:", sj.get("success"), "| errorReason:", sj.get("errorReason", ""), "| tx:", sj.get("transaction") or "")

    if not sj.get("success"):
        print("   Settlement failed!")
        return

    print("6) Retrying /v1/read with X-PAYMENT header...")
    rr = httpx.post(
        url,
        json=body,
        headers={"X-PAYMENT": json.dumps(payload), "Content-Type": "application/json"},
        timeout=60
    )
    print("   Response Status:", rr.status_code)
    res_data = rr.json()
    print("   Title:", res_data.get("title"))
    print("   Length:", res_data.get("length"))
    print("   Tokens:", res_data.get("estimated_tokens"))
    print("   Elapsed:", res_data.get("elapsed_ms"), "ms")
    print("   Preview:", repr(res_data.get("content", "")[:120]))

if __name__ == "__main__":
    main()
