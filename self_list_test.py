# self_list_test.py — trigger PayAI Bazaar listing via /verify (no funds moved)
import os, json, httpx, pathlib
from eth_account import Account
from eth_utils import to_bytes

env = {}
for l in pathlib.Path(".env").read_text().splitlines():
    if "=" in l and not l.startswith("#"):
        env.setdefault(*l.split("=", 1))
key = env.get("EVM_PRIVATE_KEY", "")
acct = Account.from_key(key)

USDC = "0xdC035D2dD77224696f5878861922287D84b9d608"
RECV = acct.address
AMOUNT = "1000000"
nonce = "0x" + os.urandom(32).hex()
VERIFY = "https://facilitator.payai.network/verify"
RESOURCE = "https://rand-cycling-strategies-reflect.trycloudflare.com/v1/verify"

domain = {"name": "USD Coin (PoS)", "version": "2", "chainId": 8453, "verifyingContract": USDC}
types = {"TransferWithAuthorization": [
    {"name": "from", "type": "address"},
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "validAfter", "type": "uint256"},
    {"name": "validBefore", "type": "uint256"},
    {"name": "nonce", "type": "bytes32"},
]}
message = {
    "from": acct.address, "to": RECV, "value": int(AMOUNT),
    "validAfter": 0, "validBefore": 9999999999,
    "nonce": to_bytes(hexstr=nonce),
}
sig = Account.sign_typed_data(acct.key, domain, types, message).signature.hex()

payload = {
    "x402Version": 2, "scheme": "exact", "network": "eip155:8453",
    "accepted": {"scheme": "exact", "network": "eip155:8453", "amount": AMOUNT,
                 "asset": USDC, "payTo": RECV, "maxTimeoutSeconds": 60},
    "payload": {"signature": f"0x{sig}", "authorization": {
        "from": acct.address, "to": RECV, "value": AMOUNT,
        "validAfter": "0", "validBefore": "9999999999", "nonce": nonce}},
    "extensions": {"bazaar": {
        "info": {
            "input": {"type": "http", "method": "POST", "bodyType": "json",
                      "body": {"query": "string", "type": "enum(...)", "depth": "enum(standard|deep)",
                               "target_url": "string(optional)"}},
            "output": {"type": "json", "example": {"verdict": "TRUE|FALSE|PARTIALLY_TRUE|CHANGED|UNREACHABLE|BLOCKED|UNVERIFIED", "answer": "...", "sources": []}}},
        "schema": {}}}
}

r = httpx.post(VERIFY, json={
    "paymentPayload": payload,
    "paymentRequirements": {
        "scheme": "exact", "network": "eip155:8453", "maxAmountRequired": AMOUNT,
        "resource": RESOURCE, "description": "verify", "mimeType": "application/json",
        "payTo": RECV, "maxTimeoutSeconds": 60, "asset": USDC,
        "extra": {"name": "USDC", "version": "2"}}},
    headers={"Content-Type": "application/json"})
print("status:", r.status_code)
print("body:", r.text[:400])
print("EXTENSION-RESPONSES:", r.headers.get("extension-responses"))
