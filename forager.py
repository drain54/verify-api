# forager.py — Active Foraging & Economic Burn Engine for Poci (ARO Organism)
import os
import json
import time
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
HERMES_ENV_PATH = Path("/home/aether/.hermes/.env")
FORAGING_LOG = BASE_DIR / "foraging.jsonl"

DAILY_COMPUTE_BURN_USDC = 0.2  # $0.20/day simulated server compute & bandwidth cost

# --- INCENTIVE SYSTEM: market-expansion multiplier -----------------------
# When Poci successfully expands into a NEW market (verified live listing),
# revenue attributable to that market is credited x2 in the metabolic ledger
# for NEW_MARKET_BONUS_DAYS. This is a VIRTUAL accounting credit only — it does
# NOT create or move real USDC. Real USDC inflow is always read from-chain.
NEW_MARKET_MULTIPLIER = 2.0
NEW_MARKET_BONUS_DAYS = 30
MARKETS_PATH = BASE_DIR / "markets.json"


def load_markets() -> list:
    if MARKETS_PATH.is_file():
        try:
            return json.loads(MARKETS_PATH.read_text())
        except Exception:
            return []
    return []


def save_markets(markets: list):
    try:
        MARKETS_PATH.write_text(json.dumps(markets, indent=2))
    except Exception:
        pass


def active_new_markets() -> list:
    """Return markets still inside their bonus window.

    Markets flagged `established` (listed before the incentive system existed)
    are NEVER eligible for the bonus, regardless of timestamp.
    """
    now = datetime.now(timezone.utc)
    out = []
    for m in load_markets():
        if not m.get("verified") or m.get("established"):
            continue
        # Only markets Poci submitted itself earn the bonus.
        if m.get("attributed_to", "poci") != "poci":
            continue
        since = datetime.fromisoformat(m["registered_at"])
        age_days = (now - since).days
        if age_days <= NEW_MARKET_BONUS_DAYS:
            out.append({**m, "age_days": age_days,
                        "days_left": NEW_MARKET_BONUS_DAYS - age_days})
    return out


def market_multiplier() -> tuple:
    """(multiplier, contributing_markets) — the highest active multiplier wins."""
    active = active_new_markets()
    if not active:
        return 1.0, []
    return NEW_MARKET_MULTIPLIER, active


def register_market(name: str, url: str, evidence: str = "",
                    established: bool = False,
                    registered_at: str | None = None,
                    attributed_to: str = "poci") -> dict:
    """Register a marketplace listing.

    established=True   -> pre-existing listing, no bonus ever.
    registered_at      -> pass the REAL listing date; defaults to now. Do not
                          backdate a genuinely new market to farm the bonus.
    attributed_to      -> "poci" (default) or "human". The x2 bonus is only
                          granted to markets Poci submitted itself; a market
                          the human registered manually earns no bonus.
    """
    markets = load_markets()
    for m in markets:
        if m["name"] == name:
            m.update({"url": url, "verified": True, "evidence": evidence,
                      "established": established,
                      "attributed_to": attributed_to,
                      "reverified_at": datetime.now(timezone.utc).isoformat()})
            save_markets(markets)
            return {"ok": False, "reason": "already registered", "market": m}
    entry = {"name": name, "url": url, "evidence": evidence, "verified": True,
             "established": established, "attributed_to": attributed_to,
             "registered_at": registered_at or datetime.now(timezone.utc).isoformat()}
    markets.append(entry)
    save_markets(markets)
    eligible = (not established) and attributed_to == "poci"
    return {"ok": True, "market": entry,
            "bonus_multiplier": NEW_MARKET_MULTIPLIER if eligible else 1.0,
            "bonus_days": NEW_MARKET_BONUS_DAYS if eligible else 0,
            "bonus_reason": None if eligible else
                            ("established market" if established
                             else f"attributed to {attributed_to}, not poci")}

def get_env_val(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val:
        return val
    for p in (ENV_PATH, HERMES_ENV_PATH):
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        return v.strip().strip("\"'")
    return default

class ForagerEngine:
    def __init__(self):
        self.wallet = get_env_val("X402_WALLET", "0xd477295C0Fe6Be96CaDd3d5B6B3eB82B16eADa98")
        self.apify_token = get_env_val("APIFY_API_TOKEN", "")
        self.public_url = "https://verify.drain54.my.id"
        self.last_run = 0
        self.cached_results = {
            "last_run": None,
            "burn_rate": DAILY_COMPUTE_BURN_USDC,
            "registries": {},
            "apify_demand": {},
            "urgency": "NORMAL",
            "action_taken": "IDLE"
        }

    def _log_forage(self, event: dict):
        try:
            with FORAGING_LOG.open("a") as f:
                f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
        except Exception:
            pass

    async def check_registries_liveness(self) -> dict:
        """Jalur A: Ping & verifikasi keterbacaan endpoint di marketplace."""
        results = {}
        async with httpx.AsyncClient(timeout=10) as client:
            # 1. Glama verification file
            try:
                r = await client.get(f"{self.public_url}/.well-known/glama.json")
                results["glama_domain_check"] = "HEALTHY" if r.status_code == 200 else f"HTTP_{r.status_code}"
            except Exception as e:
                results["glama_domain_check"] = f"FAIL_{type(e).__name__}"

            # 2. MCP Server Card
            try:
                r = await client.get(f"{self.public_url}/.well-known/mcp/server-card.json")
                results["mcp_server_card"] = "HEALTHY" if r.status_code == 200 else f"HTTP_{r.status_code}"
            except Exception as e:
                results["mcp_server_card"] = f"FAIL_{type(e).__name__}"

            # 3. MPP Manifest Discovery Check
            try:
                r = await client.get(f"{self.public_url}/.well-known/mpp.json")
                results["mpp_discovery"] = "HEALTHY" if r.status_code == 200 else f"HTTP_{r.status_code}"
            except Exception as e:
                results["mpp_discovery"] = f"FAIL_{type(e).__name__}"

            # 4. MPPscan Server Explorer Check
            try:
                r = await client.get("https://mppscan.com/server/4abfa95cf527d4717accc0d81b03a9b7458bf2dbfc1a2977d0d68a63cd2a0d02")
                results["mppscan_listing"] = "HEALTHY" if r.status_code == 200 else f"HTTP_{r.status_code}"
            except Exception as e:
                results["mppscan_listing"] = f"FAIL_{type(e).__name__}"

            # 5. PayAI Facilitator Discovery Reachability
            try:
                r = await client.get("https://facilitator.payai.network/discovery/resources?limit=1")
                results["payai_facilitator"] = "REACHABLE" if r.status_code == 200 else f"HTTP_{r.status_code}"
            except Exception as e:
                results["payai_facilitator"] = f"FAIL_{type(e).__name__}"

        return results

    async def scout_apify_demand(self) -> dict:
        """Jalur B: Scout demand dari portfolio actor Apify drain54."""
        if not self.apify_token:
            return {"status": "NO_TOKEN", "actors": {}}

        try:
            headers = {"Authorization": f"Bearer {self.apify_token}", "User-Agent": "Mozilla/5.0"}
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get("https://api.apify.com/v2/acts?my=1", headers=headers)
                if r.status_code != 200:
                    return {"status": f"HTTP_{r.status_code}", "actors": {}}

                items = r.json().get("data", {}).get("items", [])
                actors_summary = {}
                total_runs = 0
                for item in items:
                    name = item.get("name", "unknown")
                    stats = item.get("stats", {})
                    runs = stats.get("totalRuns", 0)
                    users = stats.get("totalUsers", 0)
                    total_runs += runs
                    actors_summary[name] = {"total_runs": runs, "total_users": users}

                return {
                    "status": "CONNECTED",
                    "total_portfolio_runs": total_runs,
                    "actors": actors_summary
                }
        except Exception as e:
            return {"status": f"ERROR_{type(e).__name__}", "actors": {}}

    async def execute_forage_cycle(self, current_usdc: float, net_24h_inflow: float) -> dict:
        """Siklus lengkap Active Foraging berdasarkan tekanan ekonomi."""
        now = time.time()

        # Incentive: new-market expansion multiplier (virtual accounting credit)
        mult, bonus_markets = market_multiplier()
        credited_inflow = net_24h_inflow * mult
        effective_net = credited_inflow - DAILY_COMPUTE_BURN_USDC

        if current_usdc < 1.0 or effective_net < 0:
            urgency = "HIGH (DEFICIT / STARVING)"
            action = "AGGRESSIVE_PROPAGATION"
        elif current_usdc <= 10.0:
            urgency = "MODERATE (NOMINAL)"
            action = "ROUTINE_KEEP_ALIVE"
        else:
            urgency = "LOW (ABUNDANT)"
            action = "SURPLUS_EXPANSION"

        # Jalankan scouting
        registries = await self.check_registries_liveness()
        apify = await self.scout_apify_demand()

        result = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "daily_burn_target": DAILY_COMPUTE_BURN_USDC,
            "real_inflow": round(net_24h_inflow, 4),
            "market_multiplier": mult,
            "credited_inflow": round(credited_inflow, 4),
            "bonus_markets": [{"name": m["name"], "days_left": m["days_left"]} for m in bonus_markets],
            "effective_net_daily": round(effective_net, 4),
            "urgency": urgency,
            "action_taken": action,
            "registries": registries,
            "apify_demand": apify
        }

        self.last_run = now
        self.cached_results = result
        self._log_forage(result)
        return result

    def get_summary(self) -> dict:
        return self.cached_results

forager = ForagerEngine()
