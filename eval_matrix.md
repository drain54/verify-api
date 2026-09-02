# eval_matrix.md — 10 benchmark queries (Step 1b)

Expected verdict/type/tier/determinism. Used to gate MVP: pipeline must hit
expected_verdict on >=8/10 before x402 is added.

Columns:
- determinism: DETERMINISTIC (no LLM needed for verdict) | LLM-ASSISTED (LLM interprets evidence)
- tier: highest source tier the verdict should rest on (official=3, primary/API=2, third=1, social=0)

| # | query | type | expected_verdict | expected_tier | determinism | notes |
|---|-------|------|------------------|---------------|-------------|-------|
| 1 | GLM-5.3 Flash masih gratis di ZenMux hari ini? | pricing_check | TRUE/PARTIALLY_TRUE | 3 | LLM-ASSISTED | live status bisa berubah; bandingkan pricing page |
| 2 | CodeBuddy masih kasih API key gratis atau wajib paid? | pricing_check | FALSE/CHANGED | 3 | LLM-ASSISTED | per experience: signup butuh paid |
| 3 | TinyFish API key X valid atau backend tolak INVALID? | error_diagnosis | TRUE | 2 | DETERMINISTIC | MCP URL 200 = hidup; 402 arti payment bukan down |
| 4 | Firecrawl keyless masih jalan dari IP server ini atau 402? | endpoint_check | FALSE | 2 | DETERMINISTIC | 402 = payment required, endpoint hidup; bukan block/down |
| 5 | Model B.AI mana yang masih free hari ini? | pricing_check | PARTIALLY_TRUE | 3 | LLM-ASSISTED | list + filter free_only |
| 6 | x402-facilitator Y support Base mainnet atau testnet only? | claim_verify | FALSE/CHANGED | 3 | LLM-ASSISTED | docs: x402.org testnet only, bukan mainnet |
| 7 | Provider W masih region-lock untuk akun Indonesia? | region_check | BLOCKED/TRUE/PARTIALLY_TRUE | 3 | LLM-ASSISTED | CDP region-lock US/SG = contoh nyata; bisa ambiguous |
| 8 | Endpoint https://.../v1/chat/completions provider Z masih 200 atau mati? | endpoint_check | TRUE/UNREACHABLE | 2 | DETERMINISTIC | pure HTTP probe: status+latency+TLS/DNS. BENCHMARK PERTAMA (no LLM) |
| 9 | README repo R: "100rb free req/bulan" masih valid di pricing page? | claim_verify | PARTIALLY_TRUE/FALSE | 3 | LLM-ASSISTED | cross-source docs vs README |
| 10 | Model A di OpenRouter masih $0 (free) atau sudah paid? | pricing_check | CHANGED/TRUE/PARTIALLY_TRUE | 3 | LLM-ASSISTED | mixed free/paid = PARTIALLY_TRUE wajar |

## Run format (Step 2 — appended per run)
| # | actual_verdict | confidence | latency_ms | search_count | source_tier | pass | failure_reason |
|---|---------------|-----------|-----------|--------------|-------------|------|---------------|
| (filled by eval harness) |

## Gate
- 8/10 expected_verdict match -> pipeline acceptable, proceed to x402.
- <8 -> fix classifier/tier/verdict rules, not LLM prompt.
