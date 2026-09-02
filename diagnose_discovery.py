# diagnose_discovery.py — test 2 hypotheses about PayAI Bazaar indexing
import httpx, json, datetime

SEPOLIA = []
TFC = []

for offset in range(0, 30000, 1000):
    r = httpx.get(f"https://facilitator.payai.network/discovery/resources?limit=1000&offset={offset}", timeout=30)
    items = r.json().get("items", [])
    if not items:
        break
    for i in items:
        accepts = i.get("accepts", [])
        net = accepts[0].get("network") if accepts else "?"
        if net == "eip155:84532" or net == "base-sepolia":
            SEPOLIA.append(i)
        if "trycloudflare" in i.get("resource", ""):
            TFC.append(i)

print(f"=== base-sepolia items: {len(SEPOLIA)} ===")
if SEPOLIA:
    # newest lastUpdated
    def parse(ts):
        try: return datetime.datetime.fromisoformat(str(ts).replace("Z","+00:00"))
        except: return None
    dated = [(parse(i.get("lastUpdated")), i) for i in SEPOLIA if parse(i.get("lastUpdated"))]
    dated.sort(key=lambda x: x[0])
    if dated:
        newest = dated[-1]
        print(f"newest lastUpdated: {newest[0]} | {newest[1].get('resource')}")
        oldest = dated[0]
        print(f"oldest lastUpdated: {oldest[0]} | {oldest[1].get('resource')}")
    else:
        print("no parseable lastUpdated")
    print("sample resources:")
    for i in SEPOLIA[:3]:
        print(" -", i.get("resource"))

print(f"\n=== trycloudflare items: {len(TFC)} ===")
if TFC:
    for i in TFC[:5]:
        print(" -", i.get("resource"), "| lastUpdated:", i.get("lastUpdated"))
    # test liveness of up to 3
    import time
    for i in TFC[:3]:
        url = i.get("resource")
        try:
            t0 = time.time()
            resp = httpx.get(url, timeout=8, follow_redirects=True)
            print(f"  LIVE {resp.status_code} ({time.time()-t0:.1f}s) {url}")
        except Exception as e:
            print(f"  DEAD {url}: {type(e).__name__}")