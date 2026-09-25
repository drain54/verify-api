# metabolism.py — Autonomous Revenue Organism (ARO) Metabolism & Dynamic Pricing Engine
import os
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path
from forager import forager, DAILY_COMPUTE_BURN_USDC

# Load .env if present
_env = Path(__file__).parent / ".env"
if _env.is_file():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            os.environ.setdefault(*line.split("=", 1))

class MetabolismManager:
    """
    Manages the economic life-cycle of the Autonomous Revenue Organism on Base.
    States:
      - STARVING (< $1.00 USDC): emergency mode, 30% discount to stimulate inflow.
      - NOMINAL ($1.00 - $10.00 USDC): standard operations.
      - ABUNDANT (> $10.00 USDC): surplus mode, 20% margin expansion.
    """
    def __init__(self, rpc_url: str = "https://mainnet.base.org", cache_ttl: int = 60):
        self.rpc_url = rpc_url
        self.cache_ttl = cache_ttl
        self.usdc_contract = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        self.wallet = os.getenv("X402_WALLET", "0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98")
        
        self.last_updated = 0
        self.cached_usdc = 0.0
        self.cached_eth = 0.0
        self.state = "NOMINAL"
        self.multiplier = 1.0

    async def refresh_balance(self, force: bool = False) -> dict:
        now = time.time()
        if not force and (now - self.last_updated < self.cache_ttl) and self.last_updated > 0:
            return self.get_state()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # 1. Base ETH (gas)
                r_eth = await client.post(
                    self.rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [self.wallet, "latest"]}
                )
                eth_hex = r_eth.json().get("result", "0x0")
                self.cached_eth = int(eth_hex, 16) / 1e18

                # 2. Base USDC (ERC20 balanceOf)
                data = "0x70a08231" + self.wallet[2:].lower().zfill(64)
                r_usdc = await client.post(
                    self.rpc_url,
                    json={"jsonrpc": "2.0", "id": 2, "method": "eth_call", "params": [{"to": self.usdc_contract, "data": data}, "latest"]}
                )
                usdc_hex = r_usdc.json().get("result", "0x0")
                self.cached_usdc = int(usdc_hex, 16) / 1e6

            self.last_updated = now

            # Determine State & Multiplier
            if self.cached_usdc < 1.0:
                self.state = "STARVING"
                self.multiplier = 0.7  # 30% discount
            elif self.cached_usdc <= 10.0:
                self.state = "NOMINAL"
                self.multiplier = 1.0  # standard
            else:
                self.state = "ABUNDANT"
                self.multiplier = 1.2  # 20% premium
        except Exception as e:
            # On network/RPC error, retain existing cache or conservative state
            if self.last_updated == 0:
                self.state = "NOMINAL"
                self.multiplier = 1.0

        return self.get_state()

    def get_state(self) -> dict:
        return {
            "state": self.state,
            "wallet": self.wallet,
            "usdc": round(self.cached_usdc, 4),
            "eth": round(self.cached_eth, 6),
            "multiplier": self.multiplier,
            "daily_burn_usdc": DAILY_COMPUTE_BURN_USDC,
            "foraging": forager.get_summary(),
            "last_updated": datetime.fromtimestamp(self.last_updated, timezone.utc).isoformat() if self.last_updated else None
        }

    def compute_dynamic_price(self, base_micro_usdc: int) -> int:
        """Calculate dynamic price in micro-USDC based on organism state."""
        dynamic = int(round(base_micro_usdc * self.multiplier))
        return max(1000, dynamic)  # Minimum 0.0010 USDC (1000 micro)

    def format_telemetry(self) -> str:
        s = self.get_state()
        state_emoji = "🔴" if s["state"] == "STARVING" else "🟢" if s["state"] == "NOMINAL" else "💎"
        
        lines = [
            f"{state_emoji} ORGANISM STATE: {s['state']}",
            f"WALLET: {s['wallet']}",
            f"USDC BALANCE: ${s['usdc']:.4f} USDC",
            f"GAS RESERVE: {s['eth']:.6f} ETH",
            f"DYNAMIC MULTIPLIER: {s['multiplier']}x",
            f"LAST UPDATED: {s['last_updated'] or 'Pending initial sync'}"
        ]
        return "\n".join(lines)

# Singleton instance
metabolism = MetabolismManager()
