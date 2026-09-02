# Verify API — Marketing / Listing Assets

Service name: **Verify API**
Tagline: *AI infrastructure claim verification — pay-per-query.*
Owner: drain54
Wallet (payTo): `0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98`
Endpoint: `https://slinging-chloride-chair.ngrok-free.dev/v1/verify`
Protocol: x402 v1
Network: Base (eip155:8453)
Asset: USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
Price: `$0.01` per query (`1000000` atomic USDC)

---

## One-liner
Verify API is an HTTP x402 endpoint that checks AI model, API, pricing, free-tier, endpoint availability, and region claims with fresh evidence. Pay per query; no API key needed.

## Short description
Verify API answers whether an AI model, API, or infrastructure claim is still true today. It returns deterministic verdicts backed by fresh evidence, sources, and caveats.

## Value proposition
- **Trust claims with evidence**: every answer includes sources, confidence, and caveats
- **No API key, no account**: pay-per-request via x402 USDC micropayment
- **Agent-native**: works with HTTP clients, MCP tools, and x402-enabled agents
- **Deterministic verdicts**: TRUE / FALSE / PARTIALLY_TRUE / CHANGED / UNREACHABLE / BLOCKED / UNVERIFIED

## Input schema
```json
{
  "query": "string — the claim to verify",
  "type": "enum(claim_verify|endpoint_check|pricing_check|region_check|error_diagnosis)",
  "depth": "enum(standard|deep) — default: standard",
  "target_url": "string (optional)"
}
```

## Output schema
```json
{
  "verdict": "TRUE|FALSE|PARTIALLY_TRUE|CHANGED|UNREACHABLE|BLOCKED|UNVERIFIED",
  "answer": "evidence-backed answer",
  "confidence": 0.0-1.0,
  "sources": [{"url":"https://...","type":"official|community|news","supports":"..."}],
  "caveats": []
}
```

## What this does NOT cover
- General web search / open-ended research
- Long-form document analysis
- Training or model hosting
- Paid-source scraping

## Use cases
- **Model availability**: “Is GLM-5.3 Flash free on ZenMux?”
- **Endpoint health**: “Is https://api.example.com/health reachable?”
- **Pricing checks**: “Does provider X still have a free tier?”
- **Region eligibility**: “Is service Y available in Indonesia?”
- **Infra debugging**: x402 / payment / endpoint error diagnosis

## Compliance / buyer note
- USDC on Base mainnet
- Facilitator: `https://facilitator.payai.network`
- Discovery: listed on PayAI Bazaar `/discovery/resources`
