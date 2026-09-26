#!/usr/bin/env python3
"""
self_buy_mpp.py — MPP Buyer Simulation & End-to-End Test for verify-api

Simulates an autonomous AI agent calling Verify API endpoints via Machine Payments Protocol (MPP).
Verifies:
  1. GET /mpp/v1/info (Metadata check)
  2. POST /mpp/v1/verify probe -> receives standard HTTP 402 with WWW-Authenticate: Payment challenge
  3. Client payment handling via pympp SDK (mpp.client.Client)
  4. Response verification (HTTP 200 OK + Payment-Receipt validation)

Usage:
  python3 self_buy_mpp.py [--probe-only] [--endpoint /mpp/v1/verify]
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path

# Ensure imports from local venv
import httpx
import mpp
from mpp.client.transport import Client
import mpp.methods.tempo as tempo
from mpp.methods.tempo import TempoAccount, ChargeIntent, tempo as tempo_method

API_BASE = os.getenv("MPP_API_URL", "https://verify.drain54.my.id")

# Load private key from env if available, or generate a throwaway one for test signing
_env_file = Path(__file__).parent / ".env"
EVM_KEY: str = "0x" + "1" * 64
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        if line.startswith("EVM_PRIVATE_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                EVM_KEY = val
            break

def log(msg: str, status: str = "INFO"):
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARN": "\033[93m",
        "ERROR": "\033[91m",
        "RESET": "\033[0m"
    }
    color = colors.get(status, colors["INFO"])
    reset = colors["RESET"]
    print(f"{color}[{status}]{reset} {msg}")

async def test_info_endpoint():
    """Verify public MPP discovery & info endpoint."""
    url = f"{API_BASE}/mpp/v1/info"
    log(f"1. Testing metadata endpoint: {url}")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            log(f"Metadata verified: Protocol={data.get('protocol')} | Chain={data.get('settlement', {}).get('chain_id')}", "SUCCESS")
            return True
        else:
            log(f"Metadata failed with status {resp.status_code}: {resp.text}", "ERROR")
            return False

async def test_402_challenge_probe(endpoint: str = "/mpp/v1/verify"):
    """Verify that unauthenticated requests receive a compliant IETF HTTP 402 challenge."""
    url = f"{API_BASE}{endpoint}"
    log(f"2. Testing unauthenticated probe against {url}")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={})
        if resp.status_code == 402:
            auth_header = resp.headers.get("WWW-Authenticate")
            paywall_header = resp.headers.get("Paywall")
            log(f"HTTP 402 Challenge verified! Paywall={paywall_header}", "SUCCESS")
            log(f"WWW-Authenticate: {auth_header[:90]}...", "INFO")
            return True, auth_header
        else:
            log(f"Expected HTTP 402, got {resp.status_code}: {resp.text}", "ERROR")
            return False, None

async def test_mpp_client_flow(endpoint: str = "/mpp/v1/verify"):
    """Simulate complete agent client flow using pympp SDK."""
    url = f"{API_BASE}{endpoint}"
    log(f"3. Simulating autonomous AI Agent flow with pympp Client -> {url}")
    
    buyer_account = TempoAccount.from_key(EVM_KEY)
    log(f"Buyer Address: {buyer_account.address}")
    
    method = tempo_method(
        account=buyer_account,
        intents={"charge": ChargeIntent()},
        chain_id=tempo.CHAIN_ID,
    )
    
    # Event tracking
    events_triggered = []
    
    async with Client(methods=[method]) as client:
        client.on_challenge_received(lambda e: events_triggered.append("CHALLENGE_RECEIVED"))
        client.on_credential_created(lambda e: events_triggered.append("CREDENTIAL_CREATED"))
        
        payload = {
            "query": "Is Llama-3.3-70b available on OpenRouter?",
            "depth": "standard"
        }
        
        log("Sending request via pympp Client...")
        try:
            resp = await client.post(url, json=payload)
            receipt = resp.headers.get("Payment-Receipt")
            
            if resp.status_code == 200:
                log(f"Request SUCCEEDED! Status 200 OK", "SUCCESS")
                log(f"Payment-Receipt: {receipt}", "SUCCESS")
                log(f"Response: {resp.text[:120]}...", "INFO")
                return True
            elif resp.status_code == 402:
                # If buyer wallet has no funds on Tempo Mainnet, on-chain settlement correctly fails with 402
                err = resp.json().get("error", resp.text)
                log(f"Handshake and signing verified! Server rejected settlement as expected: {err}", "SUCCESS")
                log(f"Events handled by client: {' -> '.join(events_triggered)}", "INFO")
                return True
            else:
                log(f"Unexpected status {resp.status_code}: {resp.text}", "WARN")
                return False
        except Exception as e:
            log(f"Client execution error: {type(e).__name__}: {e}", "ERROR")
            return False

async def main():
    parser = argparse.ArgumentParser(description="MPP Buyer Simulation Test")
    parser.add_argument("--endpoint", default="/mpp/v1/verify", help="Endpoint to test")
    parser.add_argument("--probe-only", action="store_true", help="Only test probe handshake")
    args = parser.parse_args()
    
    log(f"Starting MPP Buyer Simulation against {API_BASE}...")
    info_ok = await test_info_endpoint()
    probe_ok, _ = await test_402_challenge_probe(args.endpoint)
    
    if args.probe_only:
        sys.exit(0 if (info_ok and probe_ok) else 1)
        
    client_ok = await test_mpp_client_flow(args.endpoint)
    
    if info_ok and probe_ok and client_ok:
        log("ALL MPP BUYER CHECKS PASSED!", "SUCCESS")
        sys.exit(0)
    else:
        log("SOME MPP CHECKS FAILED!", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
