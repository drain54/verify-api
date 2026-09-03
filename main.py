import os, json, time, uuid, asyncio, subprocess, httpx
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# load .env if present (no dotenv dep)
_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)

app = FastAPI()

# --- config (provider-agnostic: 1 env var each, no abstraction layer) ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY  = os.getenv("LLM_API_KEY", os.getenv("OPENROUTER_API_KEY", ""))
LLM_MODEL    = os.getenv("LLM_MODEL", "minimax/minimax-m3:free")
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "tinyfish")  # tinyfish | firecrawl | web_search

VERDICTS = {"TRUE","FALSE","PARTIALLY_TRUE","CHANGED","UNREACHABLE","BLOCKED","UNVERIFIED"}
TYPES    = {"claim_verify","endpoint_check","pricing_check","region_check","error_diagnosis"}
# ordinal tier: official=3, primary/API=2, third_party=1, social=0
TIER = {"official":3,"primary":2,"third_party":1,"social":0}

class Req(BaseModel):
    query: str
    type: str = "claim_verify"
    depth: str = "standard"
    target_url: str | None = None
    freshness_hours: int = 24

# --- scope gate: AI-infra only ---
SCOPE_KW = ["model","api","provider","free","tier","pricing","rate limit","endpoint",
            "region","doc","readme","x402","glm","openrouter","base","usdc","agent",
            "token","key","subscription","quota","github","model","llm","chat"]

def in_scope(q: str) -> bool:
    ql = q.lower()
    return any(k in ql for k in SCOPE_KW)

# --- HTTP probe (deterministic, no LLM) ---
async def http_probe(url: str) -> dict:
    import httpx
    t0 = time.time()
    try:
        r = await httpx.AsyncClient(timeout=15, follow_redirects=True).get(url, headers={"User-Agent":"verify-api/0.1"})
        ms = int((time.time()-t0)*1000)
        status = r.status_code
        if status == 200:
            verdict = "TRUE"
        elif status >= 500:
            verdict = "UNREACHABLE"
        elif status in (401,403,429):
            # auth/rate-limit, NOT dead and NOT necessarily region-blocked
            verdict = "TRUE"
        else:
            verdict = "FALSE"
        return {"verdict":verdict,"http_status":status,"latency_ms":ms,
                "checked_at":datetime.now(timezone.utc).isoformat()}
    except httpx.ConnectError:
        return {"verdict":"UNREACHABLE","http_status":0,"latency_ms":int((time.time()-t0)*1000),
                "checked_at":datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"verdict":"UNREACHABLE","http_status":0,"latency_ms":int((time.time()-t0)*1000),
                "checked_at":datetime.now(timezone.utc).isoformat(),"error":str(e)[:120]}

# --- search via tinyfish CLI (already installed, free) ---
def search(query: str, n: int) -> list[dict]:
    if SEARCH_BACKEND == "tinyfish":
        out = subprocess.run(["tinyfish","search","query",query],
                             capture_output=True, text=True, timeout=60).stdout
        try:
            data = json.loads(out)
            return [{"url":r.get("url"),"title":r.get("title"),"type":"third_party"}
                    for r in data.get("results",[])][:n]
        except Exception:
            return []
    # ponytail: firecrawl/web_search backends added when env switched
    return []

# --- verdict from evidence (deterministic rule, NOT LLM guess) ---
NEG = ["tidak","bukan","tolak","reject","wajib","required","expired","hilang",
       "unavailable","payment required","402","not available","not supported",
       "no longer","was free but","no longer free"]
POS = ["masih","gratis","free","ya","yes","available","active","live","true","bertahan",
       "still","still available","still free"]
def verdict_from_evidence(typ: str, sources: list, answer: str) -> str:
    if not sources:
        return "UNVERIFIED"
    a = answer.lower()
    # deterministic: parse explicit VERDICT line first (when LLM emits it)
    for line in a.splitlines():
        line = line.strip()
        if line.startswith("verdict:") or line.startswith("verdict "):
            v = line.split(":",1)[-1].strip() if ":" in line else line.split(None,1)[-1].strip()
            v = v.upper().strip()
            if v in VERDICTS:
                return v
    # fallback: keyword signal (ponytail: flaky input -> prefer uncertainty over false FALSE)
    has_neg = any(w in a for w in NEG)
    has_pos = any(w in a for w in POS)
    tier = max((TIER.get(s.get("type","social"),0) for s in sources), default=0)
    if has_neg and not has_pos:
        return "FALSE"
    if has_pos and not has_neg:
        return "TRUE" if tier >= 2 else "PARTIALLY_TRUE"
    return "PARTIALLY_TRUE"

# --- LLM: answer synthesis ONLY (verdict decided by rule above) ---
def llm_answer(query: str, typ: str, sources: list[dict]) -> str:
    if not LLM_API_KEY:
        return "(llm disabled: no key)"
    src_block = "\n".join(f"- [{s.get('type','?')}] {s['url']}: {s.get('supports','')}"
                          for s in sources) or "(no sources)"
    sys = ("You verify AI-infrastructure claims using the sources. Output EXACTLY:\n"
           "VERDICT: <one of TRUE,FALSE,CHANGED,PARTIALLY_TRUE,UNREACHABLE,BLOCKED,UNVERIFIED>\n"
           "ANSWER: <1-2 sentences evidence-backed>\n"
           "Use the verdict enum only. Do not write 'unclear' — pick PARTIALLY_TRUE if uncertain. "
           "For region_check/error_diagnosis that are ambiguous and lack explicit confirmation, "
           "the claim cannot be verified=true; pick PARTIALLY_TRUE or FALSE only if sources "
           "explicitly say 'not available'/'not supported'.")
    body = {"model":LLM_MODEL,"messages":[
        {"role":"system","content":sys},
        {"role":"user","content":f"Claim: {query}\nType: {typ}\nSources:\n{src_block}"}],
        "temperature":0.2}
    for attempt in range(2):  # spec: standard retry=1
        try:
            r = httpx.post(f"{LLM_BASE_URL}/chat/completions",
                           headers={"Authorization":f"Bearer {LLM_API_KEY}","Content-Type":"application/json"},
                           json=body, timeout=60)
            res = r.json()["choices"][0]["message"]["content"].strip()
            # keep answer sans VERDICT line; strip **bold** markers around ANSWER
            out = "\n".join(l for l in res.splitlines()
                            if not l.strip().upper().lstrip("*").startswith("VERDICT"))
            out = out.replace("**ANSWER:**", "").replace("**ANSWER**", "")
            if out.strip().upper().startswith("ANSWER"):
                out = out.strip().split(":", 1)[-1].strip()
            return out.strip()
        except Exception as e:
            if attempt == 1:
                return f"(llm error: {str(e)[:120]})"
            time.sleep(2)  # backoff for 429
    return "(llm error: exhausted retries)"
# --- bounds by depth ---
def bounds(depth: str) -> dict:
    return {"standard":{"max_search":3,"max_src":5,"retry":1},
            "deep":   {"max_search":10,"max_src":12,"retry":2}}.get(
            depth, {"max_search":3,"max_src":5,"retry":1})

@app.post("/v1/verify")
async def verify(req: Req):
    if req.type not in TYPES:
        raise HTTPException(400, f"bad type; one of {sorted(TYPES)}")
    if req.depth not in ("standard","deep"):
        raise HTTPException(400, "depth must be standard|deep")
    verified_at = datetime.now(timezone.utc).isoformat()
    b = bounds(req.depth)

    # out of scope -> reject cleanly
    if not in_scope(req.query):
        return {"id":f"vrf_{uuid.uuid4().hex[:10]}","verdict":"UNVERIFIED",
                "answer":"Query out of scope (AI-infra claims only).",
                "confidence":1.0,"verified_at":verified_at,
                "freshness":{"checked_at":verified_at,"max_age_hours":req.freshness_hours},
                "sources":[],"caveats":["out of scope"]}

    # endpoint_check / error_diagnosis with target_url -> pure probe
    if req.type in ("endpoint_check","error_diagnosis") and req.target_url:
        p = await http_probe(req.target_url)
        return {"id":f"vrf_{uuid.uuid4().hex[:10]}","verdict":p["verdict"],
                "answer":f"HTTP probe: status={p['http_status']}, latency={p['latency_ms']}ms",
                "confidence":1.0,"verified_at":verified_at,
                "freshness":{"checked_at":verified_at,"max_age_hours":req.freshness_hours},
                "sources":[{"url":req.target_url,"type":"primary","supports":"direct HTTP response"}],
                "probe":p,"caveats":[]}

    # claim/pricing/region -> search + LLM synthesis
    results = search(req.query, b["max_search"])[:b["max_src"]]
    sources = [{"url":r["url"],"type":r.get("type","third_party"),"supports":r.get("title","")}
               for r in results]
    answer = llm_answer(req.query, req.type, sources)
    verdict = verdict_from_evidence(req.type, sources, answer)
    return {"id":f"vrf_{uuid.uuid4().hex[:10]}","verdict":verdict,
            "answer":answer,"confidence":0.7,"verified_at":verified_at,
            "freshness":{"checked_at":verified_at,"max_age_hours":req.freshness_hours},
            "sources":sources,"caveats":[]}

@app.get("/.well-known/mcp/server-card.json")
async def server_card():
    return {
        "name": "io.github.drain54/verify-api",
        "title": "Verify API",
        "description": "AI infrastructure claim verification — pay-per-query via x402.",
        "version": "0.1.0",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://slinging-chloride-chair.ngrok-free.dev/v1/verify"
            }
        ]
    }

@app.get("/health")
async def health():
    return {"ok":True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8011)
