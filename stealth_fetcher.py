import time, re, httpx

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

CHALLENGE_PATTERNS = [
    r"challenges\.cloudflare\.com",
    r"Just a moment\.\.\.",
    r"cf-turnstile",
    r"cf-chl-bypass",
    r"Attention Required! \| Cloudflare",
    r"hcaptcha\.com/1/api\.js",
    r"www\.google\.com/recaptcha/api\.js"
]

async def fetch_stealth(url: str, custom_headers: dict | None = None, max_html_chars: int = 50000) -> dict:
    t0 = time.time()
    headers = {**DEFAULT_HEADERS, **(custom_headers or {})}

    html = ""
    status_code = 0
    challenge_detected = False
    challenge_type = None

    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            status_code = resp.status_code
            html = resp.text
    except Exception as e:
        return {
            "error": f"Connection error: {type(e).__name__}: {e}",
            "url": url,
            "elapsed_ms": int((time.time() - t0) * 1000)
        }

    # Detect challenges
    for pattern in CHALLENGE_PATTERNS:
        if re.search(pattern, html, re.IGNORECASE):
            challenge_detected = True
            if "turnstile" in pattern:
                challenge_type = "turnstile"
            elif "cloudflare" in pattern:
                challenge_type = "cloudflare"
            elif "hcaptcha" in pattern:
                challenge_type = "hcaptcha"
            elif "recaptcha" in pattern:
                challenge_type = "recaptcha"
            break

    # Extract title with regex
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html[:10000], re.IGNORECASE | re.DOTALL)
    if m:
        title = m.group(1).strip()

    truncated_html = html[:max_html_chars] if max_html_chars else html

    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "url": url,
        "http_status": status_code,
        "title": title,
        "challenge_detected": challenge_detected,
        "challenge_type": challenge_type,
        "content_length": len(html),
        "html": truncated_html,
        "elapsed_ms": elapsed_ms
    }
