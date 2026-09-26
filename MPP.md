# Machine Payments Protocol (MPP) Integration Guide

`verify-api` mendukung penuh standar **Machine Payments Protocol (MPP)** berbasis IETF HTTP Authentication Scheme (`Payment`), memungkinkan autonomous AI agents untuk menemukan, menegosiasikan harga, dan membayar per panggilan API via micropayments di jaringan **Tempo (USDC)** atau **Stripe**.

---

## 🚀 Quickstart for Agents

### 1. Install via AgentCash / Poncho
Agen AI yang menggunakan ekosistem MPP/AgentCash dapat langsung mengimpor tools ini:
```bash
npx agentcash add https://verify.drain54.my.id
```

### 2. Auto-Discovery Endpoints
* **OpenAPI 3.1 Spec (Canonical Contract):** `https://verify.drain54.my.id/openapi.json`
* **MPP Well-Known Manifest:** `https://verify.drain54.my.id/.well-known/mpp.json`
* **MPPscan Explorer:** [https://mppscan.com/server/4abfa95cf527d4717accc0d81b03a9b7458bf2dbfc1a2977d0d68a63cd2a0d02](https://mppscan.com/server/4abfa95cf527d4717accc0d81b03a9b7458bf2dbfc1a2977d0d68a63cd2a0d02)

---

## 💳 Katalog Endpoint MPP & Tarif

| Method | Endpoint | Tarif (USD) | Settlement Rail | Fungsi Utama |
|---|---|---|---|---|
| `POST` | `/mpp/v1/verify` | $0.01 (std) / $0.03 (deep) | Tempo USDC | Verifikasi kebenaran klaim infrastruktur AI secara deterministik berbasis web evidence. |
| `POST` | `/mpp/v1/read` | $0.005 | Tempo USDC | Ekstraksi konten webpage bersih ke format Markdown ramah LLM. |
| `POST` | `/mpp/v1/extract-json` | $0.01 | Tempo USDC | Scraping dan ekstraksi data JSON terstruktur sesuai skema kustom. |
| `POST` | `/mpp/v1/fetch-stealth` | $0.01 | Tempo USDC | Pengambilan HTML dengan bypass WAF/anti-bot dan impersonasi header. |
| `GET` | `/mpp/v1/info` | Gratis | - | Metadata protokol, status server, dan direktori harga. |

---

## 🔄 Alur Transaksi HTTP 402 (RFC 9110)

1. **Client Probe (Unauthenticated):**
   ```bash
   curl -i -X POST https://verify.drain54.my.id/mpp/v1/verify \
     -H "Content-Type: application/json" \
     -d '{"query": "Is Llama-3.3 free on OpenRouter?"}'
   ```
2. **Server Challenge (HTTP 402):**
   ```http
   HTTP/2 402 Payment Required
   Paywall: mpp
   WWW-Authenticate: Payment id="...", realm="verify.drain54.my.id", method="tempo", intent="charge", expires="...", request="<base64url>"
   ```
3. **Client Signing & Payment:**
   Client menandatangani voucher transfer USDC di Tempo sesuai payload request challenge.
4. **Client Retry with Authorization:**
   ```bash
   curl -i -X POST https://verify.drain54.my.id/mpp/v1/verify \
     -H "Content-Type: application/json" \
     -H "Authorization: Payment id=\"...\", request=\"...\", credential=\"<base64url>\"" \
     -d '{"query": "Is Llama-3.3 free on OpenRouter?"}'
   ```
5. **Server Verification & Response (HTTP 200):**
   ```http
   HTTP/2 200 OK
   Payment-Receipt: Payment status="success", reference="mpp_ref_...", method="tempo"
   Content-Type: application/json
   ```

---

## 🐍 Client Integration via Python (`pympp`)

```python
import asyncio, httpx
from mpp.client import MppClient
from mpp.methods.tempo import TempoWallet

async def run():
    # Setup client dengan wallet Tempo
    wallet = TempoWallet(private_key="0x...")
    client = MppClient(methods=[wallet])
    
    async with httpx.AsyncClient() as http:
        # Panggil endpoint - MppClient otomatis menangani handshake 402 -> sign -> retry
        response = await client.post(
            "https://verify.drain54.my.id/mpp/v1/verify",
            json={"query": "Is GLM-4-Flash free on ZenMux?"}
        )
        print("Verdict:", response.json())

asyncio.run(run())
```
