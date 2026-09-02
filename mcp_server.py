# mcp_server.py — Step 4: MCP wrapper for verify-api
# Tool: verify_ai_claim — proxy ke /v1/verify lokal (free backend 8011, paid 8012).
import os, json
from pathlib import Path
_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=", 1))

import httpx
from mcp.server.fastmcp import FastMCP

VERIFY_API = os.getenv("VERIFY_API_URL", "http://127.0.0.1:8011")
PAID_API   = os.getenv("PAID_API_URL", "http://127.0.0.1:8012")

mcp = FastMCP("verify-api")

@mcp.tool()
async def verify_ai_claim(query: str, type: str = "claim_verify",
                          depth: str = "standard", target_url: str = "") -> str:
    """Verify whether a current claim about AI models, APIs, pricing, free tiers,
    endpoints, regions, or provider availability is still true using fresh evidence.

    Args:
        query: the claim to verify (e.g. "GLM-5.3 Flash is free on ZenMux")
        type: one of claim_verify | endpoint_check | pricing_check | region_check | error_diagnosis
        depth: standard ($0.01 equivalent, 3 searches) | deep ($0.03, 10 searches)
        target_url: optional URL to probe directly (for endpoint_check/error_diagnosis)
    Returns JSON: {verdict, answer, confidence, verified_at, sources[], caveats[]}
    """
    body = {"query": query, "type": type, "depth": depth}
    if target_url:
        body["target_url"] = target_url
    # paid endpoint first; fall back to free local if payment path not configured
    for base in (PAID_API, VERIFY_API):
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{base}/v1/verify", json=body)
            if r.status_code == 200:
                return r.text
            if r.status_code == 402 and base == PAID_API:
                continue  # payment not configured in this session -> free backend
            return json.dumps({"error": f"HTTP {r.status_code}", "detail": r.text[:200]})
        except Exception as e:
            if base == VERIFY_API:
                return json.dumps({"error": str(e)[:200]})
    return json.dumps({"error": "no backend reachable"})

if __name__ == "__main__":
    mcp.run()
