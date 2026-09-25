# Security Policy

## Reporting a Vulnerability

Please report security issues privately via GitHub Security Advisories:
https://github.com/drain54/verify-api/security/advisories/new

Do NOT open a public issue for security reports.

## Scope

Verify API is a stateless HTTP service. It holds no user accounts, no database,
and no long-lived credentials. All payment verification is delegated to the
x402 facilitator (`https://facilitator.payai.network`), which validates EIP-3009
`TransferWithAuthorization` signatures on-chain before any service is delivered.

## Known Characteristics

- `GET /health` — unauthenticated liveness probe (intentional).
- MCP `initialize` / `tools/list` — served without payment so that registry
  scanners can validate the server. `tools/call` and all `/v1/*` POST endpoints
  require a valid x402 payment.
- No outbound network calls are made to loopback, link-local, or metadata
  service addresses by the pricing or payment layers.
