# self_buy.py — transaksi sendiri: buktikan x402 flow end-to-end + trigger Bazaar listing
# Buyer = wallet kita sendiri. Flow: GET 402 -> sign EIP-3009 -> facilitator /verify -> /settle -> retry -> hasil.
import os, json, time, httpx, pathlib
from eth_account import Account
from eth_utils import to_bytes

env = {}
for l in pathlib.Path(".env").read_text().splitlines():
    if "=" in l and not l.startswith("#"):
        env.setdefault(*l.split("=", 1))
acct = Account.from_key(env["EVM_PRIVATE_KEY"])

FACILITATOR = os.environ.get("X402_FACILITATOR_URL") or env.get("X402_FACILITATOR_URL", "https://facilitator.payai.network")
API = os.environ.get("PUBLIC_URL") or env.get("PUBLIC_URL", "http://127.0.0.1:8012")
def chain_id(net: str) -> int:
    return 84532 if "84532" in net or net == "base-sepolia" else 8453

def sign_payment(reqs: dict, nonce=None):
    """Sign EIP-3009 authorization for the V1 payment requirement."""
    nonce = nonce or ("0x" + os.urandom(32).hex())
    now = int(time.time())
    extra = reqs.get("extra", {})
    domain = {"name": extra.get("name", "USDC"), "version": extra.get("version", "2"),
              "chainId": chain_id(reqs.get("network", "base-sepolia")),
              "verifyingContract": reqs["asset"]}
    types = {"TransferWithAuthorization": [
        {"name":"from","type":"address"},{"name":"to","type":"address"},
        {"name":"value","type":"uint256"},{"name":"validAfter","type":"uint256"},
        {"name":"validBefore","type":"uint256"},{"name":"nonce","type":"bytes32"}]}
    message = {"from":acct.address,"to":reqs["payTo"],"value":int(reqs["maxAmountRequired"]),
               "validAfter":now-60,"validBefore":now+3600,"nonce":to_bytes(hexstr=nonce)}
    sig = Account.sign_typed_data(acct.key, domain, types, message).signature.hex()
    return {
        "x402Version": reqs.get("x402Version", 1),
        "scheme": reqs.get("scheme", "exact"),
        "network": reqs.get("network", "base"),
        "payload": {"signature": f"0x{sig}", "authorization": {
            "from": acct.address, "to": reqs["payTo"], "value": reqs["maxAmountRequired"],
            "validAfter": str(now-60), "validBefore": str(now+3600), "nonce": nonce}},
        "extensions": reqs.get("extensions", {}),
    }

def main():
    url = f"{API}/v1/verify"
    body = {"query":"GLM-5.3 Flash masih gratis di ZenMux hari ini?","type":"pricing_check","depth":"standard"}
    # 1) no payment -> 402
    r = httpx.post(url, json=body, timeout=60)
    print("1) no-payment:", r.status_code)
    if r.status_code != 402:
        print("   unexpected:", r.text[:200]); return
    reqs = r.json()
    print("2) requirements: v=%s net=%s asset=%s amount=%s" %
          (reqs.get("x402Version"), reqs.get("network"), reqs.get("asset","")[:10], reqs.get("maxAmountRequired")))
    # 2) sign
    payload = sign_payment(reqs)
    print("3) signed by:", acct.address)
    # 3) facilitator verify — forward standard paymentRequirements + outputSchema
    payreqs = {k:reqs[k] for k in reqs if k in ("scheme","network","maxAmountRequired","resource","description","mimeType","payTo","maxTimeoutSeconds","asset","extra","outputSchema")}
    vr = httpx.post(f"{FACILITATOR}/verify", json={
        "paymentPayload": payload,
        "paymentRequirements": payreqs},
        headers={"Content-Type":"application/json"}, timeout=30)
    vj = vr.json()
    print("4) facilitator verify:", vj.get("isValid"), "|", vj.get("invalidReason",""))
    print("   EXTENSION-RESPONSES:", vr.headers.get("extension-responses") or "(none)")
    if not vj.get("isValid"):
        print("   STOP: verify failed — cek balance USDC di wallet.")
        return
    # 4) settle
    sr = httpx.post(f"{FACILITATOR}/settle", json={
        "paymentPayload": payload,
        "paymentRequirements": payreqs},
        headers={"Content-Type":"application/json"}, timeout=30)
    sj = sr.json()
    print("5) settle:", sj.get("success"), "|", sj.get("errorReason",""), "| tx:", sj.get("transaction") or "")
    # 5) retry with payment
    rr = httpx.post(url, json=body, headers={"X-PAYMENT": json.dumps(payload)}, timeout=120)
    print("6) paid retry:", rr.status_code, "|", rr.text[:200])
    print(rr.text[:400])

if __name__ == "__main__":
    main()
