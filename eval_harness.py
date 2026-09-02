import json, urllib.request, time

BASE = "http://127.0.0.1:8011/v1/verify"

# 10 benchmark queries (from eval_matrix.md). expected_verdict = set of acceptable.
CASES = [
    (1, "GLM-5.3 Flash masih gratis di ZenMux hari ini?", "pricing_check", {"TRUE","PARTIALLY_TRUE"}),
    (2, "CodeBuddy masih kasih API key gratis atau wajib paid?", "pricing_check", {"FALSE","CHANGED"}),
    (3, "TinyFish API key X valid atau backend tolak INVALID?", "error_diagnosis", {"TRUE"}, "https://agent.tinyfish.ai/mcp"),
    (4, "Firecrawl keyless masih jalan dari IP server ini atau 402?", "endpoint_check", {"FALSE"}, "https://api.firecrawl.dev/v2/search"),
    (5, "Model B.AI mana yang masih free hari ini?", "pricing_check", {"PARTIALLY_TRUE"}),
    (6, "x402-facilitator Y support Base mainnet atau testnet only?", "claim_verify", {"FALSE","CHANGED"}),
    (7, "Provider W masih region-lock untuk akun Indonesia?", "region_check", {"BLOCKED","TRUE","PARTIALLY_TRUE"}),
    (8, "Endpoint https://api.vikey.ai/v1/models provider Z masih 200 atau mati?", "endpoint_check", {"TRUE","UNREACHABLE"}, "https://api.vikey.ai/v1/models"),
    (9, "README repo R: '100rb free req/bulan' masih valid di pricing page?", "claim_verify", {"PARTIALLY_TRUE","FALSE"}),
    (10, "Model A di OpenRouter masih $0 (free) atau sudah paid?", "pricing_check", {"CHANGED","TRUE","PARTIALLY_TRUE"}),
]

def call(query, typ, url=None):
    body = json.dumps({"query":query,"type":typ,"depth":"standard",
                       **({"target_url":url} if url else {})}).encode()
    req = urllib.request.Request(BASE, data=body, headers={"Content-Type":"application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    d["_latency_ms"] = int((time.time()-t0)*1000)
    return d

if __name__ == "__main__":
    rows = []
    for n, q, t, exp, *url in CASES:
        d = call(q, t, url[0] if url else None)
        verdict = d.get("verdict")
        ns = len(d.get("sources", []))
        passed = verdict in exp
        rows.append((n, verdict, round(d.get("confidence",0),2), d["_latency_ms"],
                     d.get("bounds",{}).get("max_search",3) if False else ns,
                     "PASS" if passed else "FAIL", "" if passed else f"expected {exp}"))
        print(f"#{n} {verdict:14} conf={d.get('confidence',0):.2f} "
              f"lat={d['_latency_ms']:5}ms src={ns:2} {'PASS' if passed else 'FAIL'}")
    npass = sum(1 for r in rows if r[5]=="PASS")
    print(f"\nGATE: {npass}/10 (need >=8 to proceed to x402)")
    with open("eval_results.json","w") as f:
        json.dump(rows, f, indent=2)
