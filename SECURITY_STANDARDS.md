# Standard Hardening MCP & Agent API (Canopii Grade-A / OWASP MCP)

Dokumen ini adalah **Standar Keamanan Baku** untuk seluruh MCP tools, Agent APIs, dan mikroservis yang dikembangkan di lingkungan Aether. Standar ini memastikan kepatuhan penuh terhadap **Canopii Trust Index (Target: 95-100 / Grade A)** dan panduan keamanan OWASP untuk AI Agents.

---

## 🛡️ Checklist 7 Pilar Keamanan MCP

Setiap repositori dan layanan MCP yang dipublikasikan wajib memenuhi 7 poin berikut:

### 1. Strict JSON Schema (`additionalProperties: false`)
- **Aturan:** Setiap `inputSchema` pada definisi tool wajib mencantumkan `"additionalProperties": false`.
- **Tujuan:** Mencegah *parameter smuggling* dan manipulasi payload oleh prompt injection liar yang disuntikkan ke dalam agent runner.

### 2. Pertahanan Anti-SSRF (Server-Side Request Forgery)
- **Aturan:** Setiap fungsi yang menerima parameter URL dari pengguna (misal: scraper, fetcher, webhook) wajib divalidasi dengan `ssrf_guard.py` sebelum request dieksekusi.
- **Dilarang:** Mengizinkan request ke:
  - Loopback (`127.0.0.0/8`, `localhost`, `::1`)
  - Private networks RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
  - Cloud metadata link-local (`169.254.169.254`, `metadata.google.internal`)
  - Non-HTTP(S) schemes (`file://`, `gopher://`, `ftp://`).

### 3. Execution Sandboxing (Containerization)
- **Aturan:** Setiap project wajib menyertakan `Dockerfile` (multi-stage build dengan user non-root) dan `docker-compose.yml`.
- **Tujuan:** Menunjukkan bahwa service dapat dijalankan terisolasi tanpa memerlukan hak akses root host.

### 4. Reproducible Dependencies (Lockfile)
- **Aturan:** Selalu commit lockfile dependensi (`requirements.lock`, `poetry.lock`, atau `package-lock.json`).
- **Tujuan:** Menjamin integritas supply-chain dan memudahkan audit kerentanan CVE via scanner OSV.dev / Dependabot.

### 5. Legal & Open Source Hygiene (`LICENSE`)
- **Aturan:** Wajib menyertakan file `LICENSE` resmi (SPDX-compliant, misal MIT atau Apache-2.0).

### 6. Vulnerability Disclosure Policy (`SECURITY.md`)
- **Aturan:** Wajib menyediakan `SECURITY.md` yang mencantumkan:
  - Versi yang didukung
  - Alur pelaporan privat (email security / GitHub Advisory)
  - SLA acknowledgement respon (maksimal 48 jam).

### 7. x402 Challenge Conformance (`accepts[]`)
- **Aturan:** Response HTTP 402 Payment Required wajib mengikuti struktur resmi x402 V1:
  - Header: `Paywall: x402`, `Accept: application/x402-payment-v2+json`
  - Body: dibungkus dalam array `accepts: [{ scheme, network, maxAmountRequired, resource, payTo, asset }]`.
