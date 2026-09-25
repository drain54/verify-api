# Verify API & Agent Tools Suite

Suite perkakas agen AI cerdas dan verifikasi klaim infrastruktur AI berbasis micropayment **x402 (USDC on Base)** dan **MCP (Model Context Protocol)**.

- **Primary Gateway:** `https://verify.drain54.my.id`
- **Protocol:** MCP streamable-http, SSE, and x402 V1 Pay-Per-Query
- **Network:** Base Mainnet (`eip155:8453`)
- **Payment Facilitator:** `https://facilitator.payai.network`
- **Settlement Wallet (payTo):** `0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98`
- **Settlement Asset:** USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)

---

## 🛠️ Katalog Produk: MCP Tools vs Apify Actors

Arsitektur kami membedakan secara tegas antara **MCP Tools** (layanan RPC interaktif real-time untuk model agen AI) dan **Apify Actors** (layanan komputasi serverless untuk scraping & ekstraksi web skala besar).

### 1. Model Context Protocol (MCP) — 8 Tools Aktif
Tersedia secara terpusat melalui endpoint `https://verify.drain54.my.id` dan terdaftar di **Glama**, **Smithery**, serta **Official MCP Registry**.

| No | Nama Tool | Tipe / Endpoint | Estimasi Tarif (x402 USDC) | Fungsi Utama |
|---|---|---|---|---|
| 1 | `verify_ai_claim` | POST `/v1/verify` | $1.00 (std) / $3.00 (deep) | Verifikasi mendalam klaim akurasi, status provider LLM, pricing, kuota, dan infrastruktur AI via multi-search engine & reasoning. |
| 2 | `check_endpoint_health` | POST `/v1/health-check` | $0.002 | Pemeriksaan latensi, header, dan status ketersediaan endpoint API/web. |
| 3 | `solve_captcha` | POST `/v1/solve-captcha` | $0.001 – $0.0025 | Pemecah tantangan CAPTCHA (Turnstile, hCaptcha, reCAPTCHA v2, Arkose, Cloudflare). |
| 4 | `get_captcha_pricing` | GET `/v1/captcha-pricing` | Gratis (Free) | Informasi struktur harga solver per tipe CAPTCHA. |
| 5 | `read_web_page` | POST `/v1/read` | $0.005 | Konversi URL web langsung menjadi teks bersih / Markdown ramah LLM. |
| 6 | `search_web` | POST `/v1/search` | $0.005 | Pencarian web cepat berbasis integrasi multi-search engine. |
| 7 | `extract_json_from_web` | POST `/v1/extract-json` | $0.015 | Scraping web otomatis yang langsung diparsing menjadi JSON terstruktur oleh LLM. |
| 8 | `fetch_stealth_web` | POST `/v1/fetch-stealth` | $0.010 | Pengambilan raw HTML dengan kemampuan bypass proteksi bot / WAF lanjutan. |

---

### 2. Apify Store Portfolio — 10 Actors Publik
Daftar aktor cloud serverless aktif di akun Apify (`drain54`) untuk automasi data & lead generation:

| No | Nama Actor | Actor ID | Model Tarif Apify | Fokus Ekstraksi |
|---|---|---|---|---|
| 1 | `captcha-solver` | `fOarZc7qPutsdrOqt` | $0.008 / solve | Pemecah otomatis CAPTCHA Turnstile, hCaptcha, & reCAPTCHA. |
| 2 | `verify-api` | `v9A8xu0c60mW6TPEO` | $0.008 (std) / $0.025 (deep) | Actor verifikator fakta teknis dan status model AI. |
| 3 | `web-to-markdown` | `5Z13wZtsgbRfUvasT` | $0.005 / item | Ekstraktor artikel web ke format Markdown bersih. |
| 4 | `google-maps-leads-scraper` | `S2fwpE51Oq40vU57W` | $0.004 / lead | Pengumpul prospek bisnis lokal, telepon, alamat, dan rating Maps. |
| 5 | `youtube-transcript-scraper` | `3WgM9WDER6JqekJsU` | $0.002 / video | Ekstraksi transkrip, subtitle, dan metadata video YouTube. |
| 6 | `website-contact-extractor` | `sfYQuH46SbddSna8e` | $0.005 / domain | Pemetik email, telepon, dan tautan sosial media dari website. |
| 7 | `tiktok-trends-scraper` | `HE8mawidR2naLVcEm` | $0.003 / video | Pemantau hashtag viral, metrik engagement, dan video tren TikTok. |
| 8 | `linkedin-jobs-scraper` | `anvDGnzWIAvhTODjo` | $0.003 / listing | Pengikis lowongan kerja, kualifikasi, dan perusahaan di LinkedIn. |
| 9 | `reddit-discussions-scraper` | `LB03ZuyXFXTFo1saI` | $0.002 / post | Pengeruk diskusi komunitas, thread sentimen, dan komentar Reddit. |
| 10 | `google-search-serp-scraper` | `Zl4vERvfPVmba1OUi` | $0.002 / result | Pengumpul hasil pencarian organik & posisi ranking Google SERP. |

---

## 🌐 Direktori & Marketplace Listings

| Marketplace / Direktori | Identifikasi / Listing Key | Status | Kelengkapan Tools | Keterangan |
|---|---|---|---|---|
| **Official MCP Registry** | `io.github.drain54/verify-api` | ✅ **Live (v1.0.0)** | 8/8 Tools | Source of truth resmi. Menunjuk ke `https://verify.drain54.my.id`. |
| **Glama** | `id.my.drain54.verify/verify-api` | ✅ **Live & Verified** | 8/8 Tools | Terverifikasi via `/.well-known/glama.json`. Status sehat. |
| **Smithery** | `indradarmawan87/verify-api` | ✅ **Live & Re-synced** | 8/8 Tools | Release terbaru tersinkronisasi via Smithery API Token. |
| **Apify Store** | `drain54/*` | ✅ **10 Public Actors** | 10 Actors | Portofolio cloud aktor publik di Apify. |
| **PayAI Bazaar** | `/discovery/resources` | ✅ **Listed** | x402 Endpoints | Terdaftar di katalog facilitator PayAI on-chain Base. |
| **MCP Queen** | `io.github.drain54/verify-api` | ⏳ *Auto-Syncing* | - | Scanner membaca v1.0.0 dari Official Registry (siklus ~2.7 hari). |
| **VerifyMCP** | `drain54-verify-api` | ⏳ *Auto-Syncing* | - | Scanner independen menyerap update dari Official Registry. |
| **Canopii** | `io.github.drain54/verify-api` | ✅ **Score 85/100 (B)** | Metadata | Index audit keamanan & transparansi MCP. |

---

## 🚀 Panduan Operasional & Local Run

### Menjalankan Server
```bash
cd /home/aether/verify-api
.venv/bin/python x402_mw.py
```

### Healthcheck & Monitoring
```bash
# Cek service systemd persisten
systemctl --user status verify-api.service cloudflared-aether-verify.service

# Uji healthcheck internal & publik
curl -sS https://verify.drain54.my.id/health
curl -sS https://verify.drain54.my.id/.well-known/mcp/server-card.json
```

### Konfigurasi Lingkungan (`.env`)
Salin template dari `.env.example`:
```bash
X402_FACILITATOR_URL=https://facilitator.payai.network
X402_WALLET=0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98
X402_USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
X402_NETWORK=eip155:8453
CAPZY_API_KEY=capzy_...
SMITHERY_API_KEY=76b6...
```
