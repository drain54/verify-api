import time, json, os, httpx, pathlib
from reader_backend import extract_url

def get_env_val(key: str) -> str:
    # check os.environ
    if os.getenv(key):
        return os.environ[key]
    # check local .env
    local_env = pathlib.Path(__file__).parent / ".env"
    if local_env.is_file():
        for line in local_env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    # check hermes .env
    hermes_env = pathlib.Path.home() / ".hermes" / ".env"
    if hermes_env.is_file():
        for line in hermes_env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""

GEMINI_KEY = get_env_val("GEMINI_API_KEY")
OPENROUTER_KEY = get_env_val("OPENROUTER_API_KEY")

async def extract_structured_json(url: str, schema_def: dict | list, instructions: str = "") -> dict:
    t0 = time.time()
    read_res = await extract_url(url, include_links=False, max_chars=12000)
    if "error" in read_res:
        return {
            "error": read_res["error"],
            "url": url,
            "elapsed_ms": int((time.time() - t0) * 1000)
        }

    markdown_content = read_res.get("content", "")
    if not markdown_content:
        return {
            "error": "Webpage content is empty",
            "url": url,
            "elapsed_ms": int((time.time() - t0) * 1000)
        }

    prompt = f"""Extract data from the following webpage content according to the requested schema.

Target URL: {url}
Page Title: {read_res.get('title', '')}

Webpage Markdown:
{markdown_content[:10000]}

Requested Schema / Fields:
{json.dumps(schema_def, indent=2)}

Additional Instructions: {instructions or 'None'}

Return ONLY a valid JSON object matching the requested schema.
"""

    extracted_data = {}
    # Strategy 1: Google Gemini Flash Lite via Generative Language API
    if GEMINI_KEY:
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
            try:
                async with httpx.AsyncClient(timeout=25) as client:
                    resp = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_KEY}",
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"response_mime_type": "application/json"}
                        }
                    )
                    if resp.status_code == 200:
                        cand = resp.json().get("candidates", [])
                        if cand:
                            text = cand[0]["content"]["parts"][0]["text"].strip()
                            extracted_data = json.loads(text)
                            break
            except Exception:
                continue

    # Strategy 2: OpenRouter fallback if Gemini did not produce result
    if not extracted_data and OPENROUTER_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": "meta-llama/llama-3.3-70b-instruct:free",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1
                    }
                )
                data = resp.json()
                if "choices" in data and data["choices"]:
                    raw_text = data["choices"][0]["message"]["content"].strip()
                    if raw_text.startswith("```"):
                        raw_text = raw_text.split("\n", 1)[-1]
                    if raw_text.endswith("```"):
                        raw_text = raw_text.rsplit("\n", 1)[0]
                    extracted_data = json.loads(raw_text.strip())
        except Exception:
            pass

    if not extracted_data:
        extracted_data = {"error": "Failed to extract structured data from models"}

    elapsed_ms = int((time.time() - t0) * 1000)
    return {
        "url": url,
        "title": read_res.get("title", ""),
        "data": extracted_data,
        "elapsed_ms": elapsed_ms
    }
