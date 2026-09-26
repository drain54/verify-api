#!/usr/bin/env python3
"""
Automated Resubmission to x402-list.com
Scheduled for: 2026-10-11
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import os
import re
from datetime import datetime, timezone

LOG_FILE = "/home/aether/verify-api/scripts/submit_x402_list.log"
ENV_FILE = "/home/aether/.hermes/.env"

def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass

def send_telegram(text: str):
    token = None
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for l in f:
                if l.startswith("TELEGRAM_BOT_TOKEN="):
                    token = l.strip().split("=", 1)[1].strip('"').strip("'")
                    break
    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    if not token:
        log("No Telegram bot token found, skipping alert.")
        return

    chat_id = "-1004473785949"
    thread_id = 13  # Topic x402
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "message_thread_id": thread_id,
        "text": text
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            log("Telegram alert sent successfully.")
    except Exception as e:
        log(f"Failed to send Telegram alert: {e}")

def main():
    log("Starting scheduled submission to x402-list.com...")

    data = {
        "submission_type": "service",
        "service_name": "Verify API & Agent Tools Suite",
        "service_url": "https://verify.drain54.my.id",
        "website_url": "https://github.com/drain54/verify-api",
        "email": "thekaioshin@gmail.com",
        "category": "Verification",
        "description": "Multi-utility AI Agent suite: Claim Verification + Web-to-Markdown Reader + Live Search + JSON Extraction + Stealth Fetch + CAPTCHA Solving — pay-per-use via x402 on Base USDC. Note: /v1/verify costs 1.00 USDC per call.",
        "endpoints": "/v1/verify\n/v1/read\n/v1/search\n/v1/extract-json\n/v1/fetch-stealth\n/v1/solve-captcha",
        "notes": "Compliant x402 v1 challenge wrapped in accepts[] array. Official v1.0.0 MCP & x402 server by drain54. Facilitator: PayAI, Settlement on Base USDC."
    }

    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "https://x402-list.com/api/v1/submit",
        data=encoded_data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://x402-list.com/submit"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status = r.status
            html = r.read().decode("utf-8", errors="ignore")
            log(f"HTTP Status: {status}")

            alerts = re.findall(r'class="[^"]*(alert|message|notice|success|error|banner)[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL | re.IGNORECASE)
            alert_text = alerts[0][1].strip() if alerts else "No explicit alert container found."
            clean_alert = re.sub(r'<[^>]+>', ' ', alert_text).strip()
            log(f"Alert feedback: {clean_alert[:300]}")

            msg = (
                "🚀 [Auto-Cron x402-list] Resubmission Report\n\n"
                f"Status Code: {status}\n"
                f"Feedback: {clean_alert[:300]}\n\n"
                "Endpoint: https://verify.drain54.my.id\n"
                "Target: x402-list.com/submit"
            )
            send_telegram(msg)

    except urllib.error.HTTPError as e:
        err_msg = f"HTTP Error {e.code}: {e.read().decode('utf-8', errors='ignore')[:300]}"
        log(err_msg)
        send_telegram(f"⚠️ [Auto-Cron x402-list] Resubmission Failed\n\n{err_msg}")
    except Exception as e:
        err_msg = f"Submission Exception: {e}"
        log(err_msg)
        send_telegram(f"❌ [Auto-Cron x402-list] Resubmission Failed\n\n{err_msg}")

if __name__ == "__main__":
    main()
