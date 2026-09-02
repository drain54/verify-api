# search_discovery.py — find our resource in PayAI Bazaar discovery
import httpx, json, sys

WALLET = "0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98".lower()
OUT = "/home/server1/verify-api/discovery_scan.txt"
lines = []

def log(s):
    lines.append(s)

for offset in range(0, 30000, 1000):
    try:
        r = httpx.get(f"https://facilitator.payai.network/discovery/resources?limit=1000&offset={offset}", timeout=30)
        d = r.json()
        items = d.get("items", [])
        if not items:
            break
        log(f"offset={offset}: {len(items)} items")
        for i in items:
            meta = json.dumps(i).lower()
            if WALLET in meta or "salem-orders" in meta or "127.0.0.1:8012" in meta:
                log(f"FOUND: {i.get('resource')} | lastUpdated={i.get('lastUpdated')} | accepts={i.get('accepts')}")
                with open(OUT, "w") as f:
                    f.write("\n".join(lines))
                print("FOUND", flush=True)
                sys.exit(0)
    except Exception as e:
        log(f"ERR offset={offset}: {e}")

log("NOT FOUND")
with open(OUT, "w") as f:
    f.write("\n".join(lines))
print("DONE, see " + OUT, flush=True)