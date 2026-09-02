# spec.md — AI Infrastructure Claim Verification API (v0)

## Product
Pay-per-query verification API: is a claim about AI infra **still true right now**?
Not general research. Unit = one bounded verification.

## Scope (hard)
IN: AI models, API providers, free tiers, pricing, rate limits, endpoint availability, region eligibility, dev-doc claims, x402 infra.
OUT: any non-AI-infra topic, open-ended "research", subjective/advice queries. Reject at edge with `verdict: UNVERIFIED` + `caveats:["out of scope"]`.

## Endpoint
`POST /v1/verify`
```json
{ "query": "...", "type": "endpoint_check", "depth": "standard",
  "target_url": "https://...", "freshness_hours": 24 }
```
- `type` (required, explicit — NO auto classifier in MVP):
  `claim_verify | endpoint_check | pricing_check | region_check | error_diagnosis`
- `depth`: `standard` (default) | `deep`
- `target_url`: optional, used by `endpoint_check` / `error_diagnosis`
- `freshness_hours`: optional, default 24

## Verdict enum (7, deterministic — assigned by rule, not free LLM guess)
`TRUE | FALSE | PARTIALLY_TRUE | CHANGED | UNREACHABLE | BLOCKED | UNVERIFIED`
- `CHANGED` = was true, now different (e.g. model dulu free, now paid)
- `UNREACHABLE` = endpoint timeout / DNS fail
- `BLOCKED` = region/IP blocked (403/429 from block, not 404)
- `UNVERIFIED` = no usable evidence (incl. out-of-scope)

## Response
```json
{ "id": "vrf_abc123", "verdict": "TRUE", "answer": "...",
  "confidence": 0.91, "verified_at": "2026-08-30T13:00:00Z",
  "freshness": { "checked_at": "...", "max_age_hours": 24 },
  "sources": [ { "url": "...", "type": "official", "supports": "..." } ],
  "caveats": [ "..." ] }
```

## Source tier (ordinal — NOT weighted score in MVP)
`official=3 | primary/API=2 | third_party=1 | social=0`
Official = vendor docs/pricing/GitHub/announcement. Primary = direct API/HTTP response.
Rule: verdict must rest on highest tier available; LLM may interpret, not rank.
# ponytail: weighted scoring (100/90/...) added only if eval shows tier collisions misrank.

## Pipeline
```
REQUEST
  -> scope gate (reject out-of-scope)
  -> route by type
       endpoint_check/error_diagnosis -> HTTP probe (deterministic: status, latency, TLS/DNS)
       claim_verify/pricing_check/region_check -> search plan -> fetch -> evidence
  -> source tier assignment (deterministic)
  -> conflict detection
  -> verdict by rule (deterministic)
  -> LLM: answer synthesis + conflict explanation ONLY
  -> structured JSON
```
LLM does NOT decide verdict, tier, or HTTP truth. Those are deterministic.

## Compute bounds (HARD, not targets)
| | standard | deep |
|---|---|---|
| search queries | 3 | 10 |
| sources | 5 | 12 |
| retries | 1 | 2 |
| HTTP probe | yes | yes |
| historical compare | no | yes |
| evidence detail | brief | full |
Timeout bounded per call (default 60s standard / 120s deep).

## Env vars (provider-agnostic = 1 var each, no abstraction layer)
```
LLM_BASE_URL=https://api.vikey.ai/v1
LLM_API_KEY=...
LLM_MODEL=tencent/hy3:free
SEARCH_BACKEND=tinyfish            # swap to firecrawl/web_search by changing this
X402_NETWORK=base
X402_ASSET=USDC
X402_FACILITATOR_URL=https://api.x402.celo.org   # or PayAI; Crossmint later
VERIFY_STANDARD_PRICE=0.01
VERIFY_DEEP_PRICE=0.03
```

## Pricing
standard $0.01 / deep $0.03, exact scheme, USDC on Base. No subscription.

## Build order
1. `/v1/verify` WITHOUT x402 — pass 10 benchmark queries
2. eval dataset (expected vs actual verdict/type/tier)
3. x402 middleware (402 + facilitator adapter via env)
4. MCP wrapper (`verify_ai_claim`)
```
