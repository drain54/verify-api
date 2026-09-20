import time, httpx
from datetime import datetime, timezone
import trafilatura

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

async def extract_url(url: str, include_links: bool = True, include_images: bool = False, max_chars: int = 50000) -> dict:
    t0 = time.time()
    html = None
    status_code = None

    # Step 1: Fetch via httpx with modern browser User-Agent
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"})
            status_code = resp.status_code
            if resp.status_code == 200:
                html = resp.text
    except Exception:
        pass

    # Step 2: Fallback to trafilatura native fetch if httpx failed or non-200
    if not html:
        try:
            html = trafilatura.fetch_url(url)
        except Exception as e:
            return {
                "error": f"Failed to fetch URL: {str(e)[:120]}",
                "url": url,
                "elapsed_ms": int((time.time() - t0) * 1000)
            }

    if not html:
        return {
            "error": "Failed to fetch webpage content (empty or unreachable)",
            "url": url,
            "http_status": status_code,
            "elapsed_ms": int((time.time() - t0) * 1000)
        }

    # Step 3: Extract clean markdown
    try:
        content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=include_links,
            include_images=include_images,
            favor_precision=True
        )
        metadata = trafilatura.extract_metadata(html)
        title = metadata.title if metadata and metadata.title else ""
        description = metadata.description if metadata and metadata.description else ""

        # Fallback if trafilatura stripped too much
        if not content or len(content.strip()) < 20:
            content = trafilatura.extract(html, output_format="txt") or ""

        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + "\n\n...[truncated]"

        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "url": url,
            "title": title,
            "description": description,
            "content": content,
            "length": len(content),
            "estimated_tokens": len(content) // 4,
            "elapsed_ms": elapsed_ms,
            "extracted_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {
            "error": f"Extraction error: {str(e)[:120]}",
            "url": url,
            "elapsed_ms": int((time.time() - t0) * 1000)
        }
