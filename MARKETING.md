# Verify API — Marketing & Marketplace Asset Catalog

- **Service Name:** Verify API & Agent Tools Suite
- **Owner / Developer:** drain54
- **Official Gateway:** `https://verify.drain54.my.id`
- **Protocol:** x402 V1 Pay-Per-Query & Model Context Protocol (MCP)
- **Settlement Network:** Base Mainnet (`eip155:8453`)
- **Settlement Token:** USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`)
- **Facilitator:** `https://facilitator.payai.network`
- **Wallet (payTo):** `0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98`

---

## 📌 Elevator Pitch & Ringkasan

Verify API menyediakan infrastruktur terpadu untuk AI Agent otonom:
1. **Model Context Protocol (MCP):** 8 Tools real-time untuk mengecek kebenaran klaim teknis AI, status latensi endpoint, solver CAPTCHA otomatis, pembaca web markdown, dan ekstraktor JSON cerdas.
2. **Apify Actors Portfolio:** 10 Aktor ekstraksi web dan scraping data publik skala besar tanpa repot mengelola proxy.

Semua layanan MCP dilindungi protokol micropayment **x402**: Agen membayar secara instan per request menggunakan USDC di jaringan Base tanpa perlu membuat akun atau mendaftar API key konvensional.

---

## 🏛️ Marketplace Presence & Status Registry

| Platform / Marketplace | Link Publik / Identitas | Cakupan Produk | Status Terverifikasi |
|---|---|---|---|
| **Official MCP Registry** | `io.github.drain54/verify-api` | 8 MCP Tools | ✅ **v1.0.0 Active** |
| **Glama Marketplace** | `id.my.drain54.verify/verify-api` | 8 MCP Tools | ✅ **Active & Verified** |
| **Smithery Registry** | `indradarmawan87/verify-api` | 8 MCP Tools | ✅ **Active & Re-synced** |
| **Apify Store** | `https://apify.com/drain54` | 10 Scraper Actors | ✅ **10 Public Actors** |
| **PayAI Bazaar** | `facilitator.payai.network/discovery/resources` | x402 Endpoints | ✅ **Catalogued** |
| **Canopii Security** | `index.canopii.dev/server/io.github.drain54/verify-api` | Security Scoring | ✅ **Score 85/100 (B)** |
| **MCP Queen** | `mcpqueen.com/s/io.github.drain54/verify-api` | MCP Audit Index | ⏳ *Syncing from Official Registry* |
| **VerifyMCP** | `verifymcp.io/servers/drain54-verify-api` | Trust Score Index | ⏳ *Syncing from Official Registry* |

---

## 🧰 Rincian 8 Tools MCP (Agent Interoperability)

1. `verify_ai_claim` (`POST /v1/verify`)
   - Verifikasi mendalam klaim akurasi, ketersediaan model gratis, dan status penyedia AI berbasis multi-source search ($1.00 standard / $3.00 deep).
2. `check_endpoint_health` (`POST /v1/health-check`)
   - Pengujian ketersediaan, status HTTP, dan respon waktu server target.
3. `solve_captcha` (`POST /v1/solve-captcha`)
   - Bypass rintangan otomatis Cloudflare Turnstile, hCaptcha, reCAPTCHA v2, dan Arkose.
4. `get_captcha_pricing` (`GET /v1/captcha-pricing`)
   - Transparansi tarif solver berdasarkan tingkat kesulitan jenis CAPTCHA.
5. `read_web_page` (`POST /v1/read`)
   - Pembersihan DOM HTML menjadi Markdown teks murni siap konsumsi LLM.
6. `search_web` (`POST /v1/search`)
   - Pencarian informasi web instan dengan filter relevansi tinggi.
7. `extract_json_from_web` (`POST /v1/extract-json`)
   - Ekstraksi langsung dari halaman web ke skema JSON terstruktur via AI.
8. `fetch_stealth_web` (`POST /v1/fetch-stealth`)
   - Pengambilan konten mentah halaman terproteksi dengan header stealth anti-bot.

---

## 📦 Rincian 10 Actors Apify (Data Automation Suite)

1. **`captcha-solver`**: Layanan API Cloud serverless penyelesai CAPTCHA on-demand.
2. **`verify-api`**: Mesin audit klaim teknis dan verifikasi pernyataan kapabilitas AI.
3. **`web-to-markdown`**: Parser konten web bersih untuk pipeline RAG AI.
4. **`google-maps-leads-scraper`**: Mesin penggali kontak bisnis lokal dan lokasi Maps.
5. **`youtube-transcript-scraper`**: Pengekstrak teks narasi dan subtitle video YouTube.
6. **`website-contact-extractor`**: Detektor alamat email dan nomor WhatsApp/telepon pada domain.
7. **`tiktok-trends-scraper`**: Analis tagar tren dan audio populer video TikTok.
8. **`linkedin-jobs-scraper`**: Pengumpul lowongan pekerjaan dan profil requirement perusahaan.
9. **`reddit-discussions-scraper`**: Penganalisis percakapan sentimen dan komentar thread Reddit.
10. **`google-search-serp-scraper`**: Pengekstrak halaman pencarian Google SERP secara massal.
