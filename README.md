# Verify API

AI infrastructure claim verification via x402 pay-per-query.
- Endpoint: `https://verify.drain54.my.id/v1/verify`
- Protocol: MCP streamable-http + x402 V1
- Price: 0.01 USDC (`standard`), 0.03 USDC (`deep`)
- Network: Base mainnet (`eip155:8453`)
- Facilitator: `https://facilitator.payai.network`
- Wallet: `0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98`
- Asset: USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`

## Run locally

```bash
cd /home/aether/verify-api
.venv/bin/python x402_mw.py
```

Health: `http://127.0.0.1:8012/health`

## Usage

```bash
curl -X POST https://verify.drain54.my.id/v1/verify \
  -H 'content-type: application/json' \
  -d '{"query":"your claim","type":"claim_verify","depth":"standard"}'
```

Without payment → `402` with `x402` payment requirements.

## Monitoring

```bash
# Live service and tunnel status
systemctl --user status verify-api.service cloudflared-aether-verify.service

# Public health and Glama verification
curl -sS https://verify.drain54.my.id/health
curl -sS https://verify.drain54.my.id/.well-known/glama.json
```

Log: `/home/aether/verify-api/usage.jsonl`

## Marketplace listings

- **PayAI Bazaar** — listed (`https://bazaar.payai.network`)
- **Glama** — submitted & active (`https://glama.ai/mcp/servers` — `verify-api`)
- **MCP Registry** — published (`io.github.drain54/verify-api` v0.3.0)
- **Smithery** — connected (`https://smithery.ai/servers/indradarmawan87/verify-api`)

## Config

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

Key env vars in `.env`:
- `X402_FACILITATOR_URL` — PayAI facilitator
- `X402_WALLET` — payTo address
- `X402_USDC` — USDC contract address
- `X402_USDC_NAME` — token name for EIP-712 (`USD Coin`)
- `X402_NETWORK` — CAIP-2 network (`eip155:8453` mainnet, `eip155:84532` sepolia)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `402 No config schema provided` | Add `/.well-known/mcp/server-card.json` with `configSchema` |
| `Connection error: Initialization failed with status 402` | Smithery cannot scan paywalled `/v1/verify`; use `server-card.json` for discovery |
| `Invalid payment` | Verify `X-PAYMENT` payload shape; facilitator expects V1 |
| `Settle failed` | Check facilitator status / network gas |
| Ngrok tunnel drops | Restart tmux session `ngrok` or reboot; crontab `@reboot` handles auto-start |

## Lessons learned

- x402 V1 requires exact payment requirements match; normalize `x402Version: 1`
- `USD Coin` domain name must match on-chain token name exactly
- Free MCP initialize (`initialize`/`tools/list`/`ping`) must be exempt from payment for scanner compatibility
- Settle does not require wallet ETH when using PayAI facilitator with sponsored gas
- Ngrok free URLs are ephemeral; use `@reboot` crontab + tmux wrapper for resilience

