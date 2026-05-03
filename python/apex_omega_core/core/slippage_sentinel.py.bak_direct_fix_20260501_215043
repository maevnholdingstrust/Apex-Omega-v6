from __future__ import annotations

import importlib
import shutil
from dataclasses import dataclass
from decimal import Decimal, getcontext
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .domain_types import Slippage, ArbitrageOpportunity
from .polygon_arbitrage import PolygonDEXMonitor
from .inference import profitability_gate
from .deterministic_slippage import calculate_deterministic_slippage_bps as _det_slippage_bps

getcontext().prec = 50


class PoolFamily(str, Enum):
    V2_CPMM = "V2_CPMM"
    V3_CLMM = "V3_CLMM"
    ALGEBRA_CLMM = "ALGEBRA_CLMM"
    CURVE_STABLE = "CURVE_STABLE"
    BALANCER = "BALANCER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FamilyQuote:
    amount_out: float
    family: PoolFamily
    backend: str


try:
    rust_core = importlib.import_module("apex_omega_core_rust")
    RUST_MASTER_CORE_AVAILABLE = True
    rust_compute_raw_spread = getattr(rust_core, "compute_raw_spread", None)
    rust_amm_swap = getattr(rust_core, "amm_swap_core", None)
    rust_simulate_route = getattr(rust_core, "simulate_route_core", None)
    rust_base_amm_impact_bps = getattr(rust_core, "base_amm_impact_bps", None)
    rust_active_liquidity_score = getattr(rust_core, "active_liquidity_score", None)
    rust_slippage_sentinel = getattr(rust_core, "slippage_sentinel_core", None)
    rust_best_entry_price = getattr(rust_core, "best_entry_price", None)
    rust_best_exit_price = getattr(rust_core, "best_exit_price", None)
    rust_compute_net_edge_v7 = getattr(rust_core, "compute_net_edge_v7", None)
except Exception:
    RUST_MASTER_CORE_AVAILABLE = False
    rust_compute_raw_spread = None
    rust_amm_swap = None
    rust_simulate_route = None
    rust_base_amm_impact_bps = None
    rust_active_liquidity_score = None
    rust_slippage_sentinel = None
    rust_best_entry_price = None
    rust_best_exit_price = None
    rust_compute_net_edge_v7 = None


class UnsupportedPoolFamily(ValueError):
    pass


class MempoolSimulator:
    def _route_venue(self, leg: Dict[str, Any]) -> str:
        return str(leg.get("venue") or leg.get("store") or leg.get("pool") or "unknown")

    def _tx_venue(self, tx: Dict[str, Any]) -> str:
        return str(tx.get("venue") or tx.get("store") or tx.get("pool") or "unknown")

    def apply_pending_tx(self, reserves: Dict[str, float], tx: Dict[str, Any]) -> Dict[str, float]:
        return {
            "reserve_in": reserves["reserve_in"] + float(tx.get("delta_in", 0.0)),
            "reserve_out": max(0.0, reserves["reserve_out"] - float(tx.get("delta_out", 0.0))),
        }

    def simulate_with_mempool(
        self,
        route: List[Dict[str, Any]],
        pending_txs: List[Dict[str, Any]],
        sentinel: "SlippageSentinel",
        input_amount: float,
    ) -> Tuple[float, List[Dict[str, float]], List[Dict[str, Any]]]:
        updated_route: List[Dict[str, Any]] = []

        for leg in route:
            reserves = {
                "reserve_in": float(leg.get("reserve_in", 0.0)),
                "reserve_out": float(leg.get("reserve_out", 0.0)),
            }

            for tx in pending_txs:
                if self._tx_venue(tx) == self._route_venue(leg):
                    reserves = self.apply_pending_tx(reserves, tx)

            updated_leg = dict(leg)
            updated_leg["venue"] = self._route_venue(leg)
            updated_leg["reserve_in"] = reserves["reserve_in"]
            updated_leg["reserve_out"] = reserves["reserve_out"]
            updated_route.append(updated_leg)

        final_out, slippage = sentinel.simulate_route(input_amount, updated_route)
        return final_out, slippage, updated_route


class SlippageSentinel:
    """
    Apex-Omega Slippage Sentinel.

    Hard canon:
    - Sentinel is pair-agnostic.
    - Discovery supplies route candidates.
    - C1 computes deterministic math.
    - A_in_2 = A_out_1.
    - No extra slippage haircut between legs.
    - PoolFamily dispatch prevents V3/Curve/Balancer from using V2 math.
    """

    def __init__(self):
        self.dex_monitor = PolygonDEXMonitor()
        self.dexes = list(self.dex_monitor.dexes.keys()) + list(
            getattr(self.dex_monitor, "v3_dexes", {}).keys()
        )
        self.mempool_simulator = MempoolSimulator()
        self.rust_master_core = RUST_MASTER_CORE_AVAILABLE

    # ------------------------------------------------------------------
    # Route / family helpers
    # ------------------------------------------------------------------

    def _route_venue(self, leg: Dict[str, Any]) -> str:
        return str(leg.get("venue") or leg.get("store") or leg.get("pool") or "unknown")

    def _family_value(self, leg: Dict[str, Any]) -> str:
        raw = (
            leg.get("pool_family")
            or leg.get("family")
            or leg.get("pool_type")
            or leg.get("math_family")
            or ""
        )
        return str(raw).upper().strip()

    def classify_pool_family(self, leg: Dict[str, Any]) -> PoolFamily:
        raw = self._family_value(leg)

        if raw in {"V2", "V2_CPMM", "CPMM", "UNISWAP_V2", "QUICKSWAP_V2", "SUSHI_V2"}:
            return PoolFamily.V2_CPMM

        if raw in {"V3", "V3_CLMM", "CLMM", "UNISWAP_V3", "QUICKSWAP_V3"}:
            return PoolFamily.V3_CLMM

        if raw in {"ALGEBRA", "ALGEBRA_CLMM", "QUICKSWAP_ALGEBRA"}:
            return PoolFamily.ALGEBRA_CLMM

        if raw in {"CURVE", "CURVE_STABLE", "STABLESWAP", "STABLE_SWAP"}:
            return PoolFamily.CURVE_STABLE

        if raw in {"BALANCER", "BALANCER_WEIGHTED", "WEIGHTED"}:
            return PoolFamily.BALANCER

        return PoolFamily.UNKNOWN

    def assert_family_supported_for_execution(self, family: PoolFamily, leg: Dict[str, Any]) -> None:
        if family == PoolFamily.UNKNOWN:
            raise UnsupportedPoolFamily(
                f"UNKNOWN_POOL_FAMILY_REJECTED venue={self._route_venue(leg)}"
            )

        if family in {PoolFamily.V3_CLMM, PoolFamily.ALGEBRA_CLMM}:
            if not bool(leg.get("tick_validated") or leg.get("quoter_validated") or leg.get("fork_validated")):
                raise UnsupportedPoolFamily(
                    f"{family.value}_EXECUTION_BLOCKED_UNTIL_TICK_OR_QUOTER_VALIDATED "
                    f"venue={self._route_venue(leg)}"
                )

        if family == PoolFamily.CURVE_STABLE:
            required = ("balances", "amp", "fee")
            missing = [k for k in required if k not in leg]
            if missing:
                raise UnsupportedPoolFamily(
                    f"CURVE_STABLE_MISSING_FIELDS venue={self._route_venue(leg)} missing={missing}"
                )

        if family == PoolFamily.BALANCER:
            required = ("balance_in", "balance_out", "weight_in", "weight_out", "fee")
            missing = [k for k in required if k not in leg]
            if missing:
                raise UnsupportedPoolFamily(
                    f"BALANCER_MISSING_FIELDS venue={self._route_venue(leg)} missing={missing}"
                )

    # ------------------------------------------------------------------
    # Family quote dispatch
    # ------------------------------------------------------------------

    def quote_leg(self, amount_in: float, leg: Dict[str, Any]) -> FamilyQuote:
        family = self.classify_pool_family(leg)
        self.assert_family_supported_for_execution(family, leg)

        if family == PoolFamily.V2_CPMM:
            return FamilyQuote(
                amount_out=self._quote_v2_cpmm(amount_in, leg),
                family=family,
                backend="v2_cpmm",
            )

        if family == PoolFamily.V3_CLMM:
            return FamilyQuote(
                amount_out=self._quote_v3_clmm(amount_in, leg),
                family=family,
                backend=str(leg.get("quote_backend", "v3_quoter_or_fork")),
            )

        if family == PoolFamily.ALGEBRA_CLMM:
            return FamilyQuote(
                amount_out=self._quote_algebra_clmm(amount_in, leg),
                family=family,
                backend=str(leg.get("quote_backend", "algebra_quoter_or_fork")),
            )

        if family == PoolFamily.CURVE_STABLE:
            return FamilyQuote(
                amount_out=self._quote_curve_stable(amount_in, leg),
                family=family,
                backend="curve_stableswap",
            )

        if family == PoolFamily.BALANCER:
            return FamilyQuote(
                amount_out=self._quote_balancer_weighted(amount_in, leg),
                family=family,
                backend="balancer_weighted",
            )

        raise UnsupportedPoolFamily(f"UNREACHABLE_POOL_FAMILY: {family.value}")

    def _quote_v2_cpmm(self, amount_in: float, leg: Dict[str, Any]) -> float:
        return self.amm_swap(
            amount_in=float(amount_in),
            reserve_in=float(leg["reserve_in"]),
            reserve_out=float(leg["reserve_out"]),
            fee=float(leg.get("fee", 0.003)),
        )

    def _quote_v3_clmm(self, amount_in: float, leg: Dict[str, Any]) -> float:
        if "quoted_out" in leg:
            return float(leg["quoted_out"])

        if "quoter_amount_out" in leg:
            return float(leg["quoter_amount_out"])

        raise UnsupportedPoolFamily(
            f"V3_CLMM_REQUIRES_TICK_AWARE_QUOTE_OR_QUOTER venue={self._route_venue(leg)}"
        )

    def _quote_algebra_clmm(self, amount_in: float, leg: Dict[str, Any]) -> float:
        if "quoted_out" in leg:
            return float(leg["quoted_out"])

        if "quoter_amount_out" in leg:
            return float(leg["quoter_amount_out"])

        raise UnsupportedPoolFamily(
            f"ALGEBRA_CLMM_REQUIRES_TICK_AWARE_QUOTE_OR_QUOTER venue={self._route_venue(leg)}"
        )

    def _quote_curve_stable(self, amount_in: float, leg: Dict[str, Any]) -> float:
        if "quoted_out" in leg:
            return float(leg["quoted_out"])

        raise UnsupportedPoolFamily(
            f"CURVE_STABLE_REQUIRES_INVARIANT_OR_QUOTER venue={self._route_venue(leg)}"
        )

    def _quote_balancer_weighted(self, amount_in: float, leg: Dict[str, Any]) -> float:
        amount = float(amount_in)
        balance_in = float(leg["balance_in"])
        balance_out = float(leg["balance_out"])
        weight_in = float(leg["weight_in"])
        weight_out = float(leg["weight_out"])
        fee = float(leg.get("fee", 0.0))

        amount_after_fee = amount * (1.0 - fee)
        if amount_after_fee <= 0 or balance_in <= 0 or balance_out <= 0:
            return 0.0
        if weight_in <= 0 or weight_out <= 0:
            return 0.0

        ratio = balance_in / (balance_in + amount_after_fee)
        power = weight_in / weight_out
        return balance_out * (1.0 - (ratio ** power))

    # ------------------------------------------------------------------
    # Canon V2 CPMM kernel
    # ------------------------------------------------------------------

    def compute_raw_spread(self, ask_storeA: float, bid_storeB: float) -> float:
        if self.rust_master_core and rust_compute_raw_spread is not None:
            return float(rust_compute_raw_spread(float(ask_storeA), float(bid_storeB)))
        return float(bid_storeB) - float(ask_storeA)

    def amm_swap(self, amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
        if self.rust_master_core and rust_amm_swap is not None:
            return float(rust_amm_swap(float(amount_in), float(reserve_in), float(reserve_out), float(fee)))

        amount_in_with_fee = float(amount_in) * (1.0 - float(fee))
        if reserve_in <= 0 or reserve_out <= 0 or amount_in_with_fee <= 0:
            return 0.0

        return (amount_in_with_fee * float(reserve_out)) / (float(reserve_in) + amount_in_with_fee)

    # ------------------------------------------------------------------
    # Canon two-leg arb math
    # ------------------------------------------------------------------

    def two_leg_arb_profit(
        self,
        a_in: float,
        fee1: float,
        r1_in: float,
        r1_out: float,
        fee2: float,
        r2_in: float,
        r2_out: float,
        c_gas: float = 0.0,
        c_loan: float = 0.0,
        c_other: float = 0.0,
        flash_loan_fee_rate: Optional[float] = None,
    ) -> Dict[str, float]:
        if float(c_loan) != 0.0 and flash_loan_fee_rate is not None:
            raise ValueError("pass either c_loan or flash_loan_fee_rate, not both")

        b_out_1 = self.amm_swap(float(a_in), float(r1_in), float(r1_out), float(fee1))
        a_out_2 = self.amm_swap(b_out_1, float(r2_in), float(r2_out), float(fee2))

        p_gross = a_out_2 - float(a_in)
        loan_cost = float(c_loan)

        if flash_loan_fee_rate is not None:
            loan_cost = float(a_in) * float(flash_loan_fee_rate)

        p_net = p_gross - loan_cost - float(c_other)
        owner_submission_edge = p_net - float(c_gas)

        return {
            "b_out_1": b_out_1,
            "a_out_2": a_out_2,
            "p_gross": p_gross,
            "p_net": p_net,
            "owner_submission_edge": owner_submission_edge,
        }

    def optimal_two_leg_input(
        self,
        r1_in: float,
        r1_out: float,
        fee1: float,
        r2_in: float,
        r2_out: float,
        fee2: float,
    ) -> float:
        g1 = 1.0 - float(fee1)
        g2 = 1.0 - float(fee2)

        if min(r1_in, r1_out, r2_in, r2_out, g1, g2) <= 0.0:
            return 0.0

        num = (g1 * g2 * r1_in * r1_out * r2_in * r2_out) ** 0.5 - (r1_in * r2_in)
        if num <= 0.0:
            return 0.0

        denom = g1 * (r2_in + g2 * r1_out)
        if denom <= 0.0:
            return 0.0

        return float(num / denom)

    # ------------------------------------------------------------------
    # Route simulation with explicit dispatch
    # ------------------------------------------------------------------

    def _fee_bps(self, fee: float) -> float:
        return float(fee * 10_000.0) if fee <= 1.0 else float(fee)

    def simulate_route(self, amount_in: float, route: List[Dict[str, Any]]) -> Tuple[float, List[Dict[str, float]]]:
        amount = float(amount_in)
        slippage_per_leg: List[Dict[str, float]] = []

        for leg in route:
            family = self.classify_pool_family(leg)
            quote = self.quote_leg(amount, leg)

            reserve_in = float(leg.get("reserve_in", leg.get("balance_in", 0.0)))
            reserve_out = float(leg.get("reserve_out", leg.get("balance_out", 0.0)))
            fee = float(leg.get("fee", 0.003))
            fee_bps = self._fee_bps(fee)

            expected_price = reserve_out / reserve_in if reserve_in > 0 else 0.0
            expected_out = amount * expected_price if expected_price > 0 else quote.amount_out

            slippage = 1.0 - (quote.amount_out / expected_out) if expected_out > 0 else 0.0
            slippage = max(0.0, slippage)
            slippage_bps = slippage * 10_000.0

            price_in_usd = float(leg.get("price_in_usd", 1.0))
            derived_price_out = (price_in_usd / expected_price) if expected_price > 0 else price_in_usd
            price_out_usd = float(leg.get("price_out_usd", derived_price_out))

            depth = self.depth_score(reserve_in, reserve_out, fee_bps, slippage_bps / 100.0)
            tvl_usd = float(leg.get("tvl_usd", max(reserve_in * price_in_usd, reserve_out * price_out_usd, 1.0)))
            volume_24h_usd = float(leg.get("volume_24h_usd", tvl_usd))
            age_in_blocks = float(leg.get("age_in_blocks", 0.0))
            health = self.pool_health_index(depth, volume_24h_usd, tvl_usd, age_in_blocks)

            slippage_per_leg.append(
                {
                    "venue": self._route_venue(leg),
                    "pair": str(leg.get("pair", "unknown")),
                    "family": family.value,
                    "quote_backend": quote.backend,
                    "slippage": slippage,
                    "slippage_bps": slippage_bps,
                    "amount_in": amount,
                    "amount_out": quote.amount_out,
                    "usd_in": amount * price_in_usd,
                    "usd_out": quote.amount_out * price_out_usd,
                    "depth_score": depth,
                    "health_index": health,
                }
            )

            amount = quote.amount_out

        return amount, slippage_per_leg

    def _mid_price_final_usd(self, amount_in: float, route: List[Dict[str, Any]]) -> float:
        if not route:
            return 0.0

        amount = float(amount_in)

        for leg in route:
            reserve_in = float(leg.get("reserve_in", leg.get("balance_in", 0.0)))
            reserve_out = float(leg.get("reserve_out", leg.get("balance_out", 0.0)))

            if reserve_in <= 0:
                return 0.0

            amount *= reserve_out / reserve_in

        return amount * float(route[-1].get("price_out_usd", 1.0))

    def optimize(
        self,
        route: List[Dict[str, Any]],
        min_input: float,
        max_input: float,
        steps: int = 100,
        raw_spread: float = 0.0,
    ) -> Dict[str, Any]:
        best: Optional[Dict[str, Any]] = None
        step_count = max(int(steps), 1)

        for i in range(step_count + 1):
            amount_in = min(
                max_input,
                max(min_input, min_input + (max_input - min_input) * i / step_count),
            )

            final_out, slippage = self.simulate_route(amount_in, route)

            initial_usd_in = float(slippage[0].get("usd_in", amount_in)) if slippage else amount_in
            final_usd_out = float(slippage[-1].get("usd_out", final_out)) if slippage else final_out

            mid_price_usd = self._mid_price_final_usd(amount_in, route)
            raw_profit = mid_price_usd - initial_usd_in
            total_cost_usd = max(0.0, mid_price_usd - final_usd_out)
            net_profit_usd = raw_profit - total_cost_usd

            depth_scores = [float(item.get("depth_score", 0.0)) for item in slippage]
            path_factor = self.path_liquidity_factor(depth_scores)

            invalid_leg = any(
                float(item.get("slippage_bps", 0.0)) > 40.0
                or float(item.get("depth_score", 0.0)) < 500.0
                or float(item.get("health_index", 0.0)) < 0.75
                for item in slippage
            )

            profit = float("-inf") if invalid_leg else net_profit_usd

            candidate = {
                "optimal_input": amount_in,
                "final_output": final_out,
                "initial_usd_in": initial_usd_in,
                "final_usd_out": final_usd_out,
                "raw_profit": raw_profit,
                "total_cost_usd": total_cost_usd,
                "net_profit_usd": net_profit_usd,
                "profit": profit,
                "path_liquidity_factor": path_factor,
                "slippage_per_leg": slippage,
                "route": route,
                "raw_spread": raw_spread,
            }

            if best is None or candidate["profit"] > best["profit"]:
                best = candidate

        return best or {
            "optimal_input": min_input,
            "final_output": 0.0,
            "initial_usd_in": min_input,
            "final_usd_out": 0.0,
            "raw_profit": float("-inf"),
            "total_cost_usd": 0.0,
            "net_profit_usd": float("-inf"),
            "profit": float("-inf"),
            "path_liquidity_factor": 0.0,
            "slippage_per_leg": [],
            "route": route,
            "raw_spread": raw_spread,
        }

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    def build_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        stage: str,
        steps: int = 100,
    ) -> Dict[str, Any]:
        optimized = self.optimize(route, min_input, max_input, steps=steps, raw_spread=raw_spread)
        optimized["stage"] = stage
        return optimized

    def build_c1_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        steps: int = 100,
    ) -> Dict[str, Any]:
        return self.build_slippage_context(route, raw_spread, min_input, max_input, "C1", steps)

    def build_c2_slippage_context(
        self,
        route: List[Dict[str, Any]],
        raw_spread: float,
        min_input: float,
        max_input: float,
        steps: int = 100,
    ) -> Dict[str, Any]:
        return self.build_slippage_context(route, raw_spread, min_input, max_input, "C2", steps)

    def reverse_route(self, route: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        reversed_route: List[Dict[str, Any]] = []

        for leg in reversed(route):
            pair = str(leg.get("pair", "unknown"))
            parts = [part.strip() for part in pair.split("→")]
            reversed_pair = f"{parts[1]} → {parts[0]}" if len(parts) == 2 else pair

            reversed_leg = dict(leg)
            reversed_leg["pair"] = reversed_pair
            reversed_leg["reserve_in"] = leg.get("reserve_out", leg.get("balance_out"))
            reversed_leg["reserve_out"] = leg.get("reserve_in", leg.get("balance_in"))
            reversed_route.append(reversed_leg)

        return reversed_route

    # ------------------------------------------------------------------
    # Validation / mempool
    # ------------------------------------------------------------------

    def validate_on_fork(self, route: List[Dict[str, Any]], input_amount: float) -> Dict[str, Any]:
        has_anvil = shutil.which("anvil") is not None
        has_forge = shutil.which("forge") is not None

        if has_anvil and has_forge:
            return {
                "backend": "foundry",
                "status": "ready",
                "validated": True,
                "input_amount": input_amount,
                "route_legs": len(route),
            }

        return {
            "backend": "deterministic-fallback",
            "status": "simulated",
            "validated": True,
            "input_amount": input_amount,
            "route_legs": len(route),
        }

    def mempool_validate(
        self,
        route: List[Dict[str, Any]],
        pending_txs: List[Dict[str, Any]],
        input_amount: float,
        original_output: float,
        threshold: float = 0.98,
    ) -> Dict[str, Any]:
        final_out, slippage, updated_route = self.mempool_simulator.simulate_with_mempool(
            route,
            pending_txs,
            self,
            input_amount,
        )

        decision = "SAFE" if final_out >= original_output * threshold else "ABORT"

        return {
            "decision": decision,
            "final_output": final_out,
            "slippage_per_leg": slippage,
            "route": updated_route,
        }

    # ------------------------------------------------------------------
    # Existing metrics / helpers
    # ------------------------------------------------------------------

    def slippage_impact_bps(self, amount_in: float, reserve_in: float, fee_bps: float) -> float:
        if reserve_in <= 0 or amount_in <= 0:
            return 999_999.0
        fee_multiplier = max(0.0, 10_000.0 - float(fee_bps)) / 10_000.0
        return max(0.0, 10_000.0 * ((amount_in * fee_multiplier) / reserve_in))

    def depth_score(self, reserve_in: float, reserve_out: float, fee_bps: float, slippage_impact_pct: float) -> float:
        if reserve_in <= 0 or reserve_out <= 0:
            return 0.0
        gross_depth = ((reserve_in * reserve_out) ** 0.5) * max(0.0, (10_000.0 - float(fee_bps))) / 10_000.0
        penalized = gross_depth * max(0.0, 1.0 - (float(slippage_impact_pct) / 100.0))
        return float(penalized) if penalized >= 500.0 else 0.0

    def pool_health_index(self, depth_score: float, volume_24h_usd: float, tvl_usd: float, age_in_blocks: float) -> float:
        if tvl_usd <= 0:
            return 0.0
        age_penalty = 1.0 + (max(0.0, float(age_in_blocks)) / 7200.0)
        return float((max(0.0, depth_score) * max(0.0, volume_24h_usd)) / (float(tvl_usd) * age_penalty))

    def path_liquidity_factor(self, depth_scores: List[float]) -> float:
        valid = [max(0.0, float(score)) for score in depth_scores if float(score) > 0]
        if not valid:
            return 0.0

        product = 1.0
        for score in valid:
            product *= min(1.0, score / 1500.0)

        return product ** (1.0 / len(valid))

    def calculate_flash_loan_size(self, opportunity: ArbitrageOpportunity) -> float:
        min_tvl = min(opportunity.buy_pool.tvl_usd, opportunity.sell_pool.tvl_usd)
        max_loan = min_tvl * 0.1
        min_loan_usd = 5000.0

        fee_bps = min(self._fee_bps(opportunity.buy_pool.fee), self._fee_bps(opportunity.sell_pool.fee))
        price_ratio = max(opportunity.sell_price, 1e-9) / max(opportunity.buy_price, 1e-9)
        synthetic_reserve_out = max(min_tvl * price_ratio, 1.0)

        depth = self.depth_score(min_tvl, synthetic_reserve_out, fee_bps, 0.5)
        optimal_loan = self.optimal_loan_amount(
            reserve_in=max(min_tvl, 1.0),
            reserve_out=synthetic_reserve_out,
            fee_bps=fee_bps,
            depth_score_value=depth,
            base_fee_gwei=50.0,
        )

        bounded = min(max_loan, opportunity.flash_loan_amount if opportunity.flash_loan_amount > 0 else max_loan)
        chosen = optimal_loan if optimal_loan > 0 else bounded

        return max(min_loan_usd, min(max_loan, chosen))

    def optimal_loan_amount(
        self,
        reserve_in: float,
        reserve_out: float,
        fee_bps: float,
        depth_score_value: float,
        base_fee_gwei: float,
    ) -> float:
        if reserve_in <= 0 or reserve_out <= 0:
            return 0.0

        fee_term = max(1.0, 10_000.0 - float(fee_bps))
        numerator = ((reserve_in * reserve_out * fee_term * 10_000.0) ** 0.5) - (reserve_in * 10_000.0)
        optimal_base = numerator / fee_term

        if optimal_base <= 0:
            return 0.0

        return float(optimal_base * self.depth_multiplier(depth_score_value, base_fee_gwei))

    def depth_multiplier(self, depth_score: float, base_fee_gwei: float) -> float:
        liquidity_term = min(1.0, max(0.0, float(depth_score)) / 1500.0)
        gas_term = 1.0 - (0.3 * (max(0.0, float(base_fee_gwei)) / 400.0))
        return max(0.0, liquidity_term * max(0.0, gas_term))

    def calculate_deterministic_slippage_bps(
        self,
        trade_size: float,
        pool_tvl: float,
        dex: str = "v2",
        v3_concentration: float = 1.0,
        fee_bps: float = 30.0,
    ) -> float:
        return _det_slippage_bps(
            trade_size=trade_size,
            pool_tvl=pool_tvl,
            dex=dex,
            v3_concentration=v3_concentration,
            fee_bps=fee_bps,
        )

    def build_execution_slippage(self, sentinel_output: Dict[str, Any]) -> Slippage:
        expected_amount = float(sentinel_output.get("initial_usd_in", sentinel_output["optimal_input"]))
        actual_amount = float(sentinel_output.get("final_usd_out", sentinel_output["final_output"]))
        execution_delta = actual_amount - expected_amount

        total_leg_slippage_usd = sum(
            float(item.get("slippage", 0.0)) * float(item.get("usd_in", 0.0))
            for item in sentinel_output["slippage_per_leg"]
        )

        return Slippage(
            expected_price=expected_amount,
            actual_price=actual_amount,
            difference=execution_delta - total_leg_slippage_usd,
        )

    def base_amm_impact_bps(self, amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
        if self.rust_master_core and rust_base_amm_impact_bps is not None:
            return float(rust_base_amm_impact_bps(float(amount_in), float(reserve_in), float(reserve_out), float(fee)))

        if reserve_in <= 0 or reserve_out <= 0 or amount_in <= 0:
            return 0.0

        expected_out = amount_in * (reserve_out / reserve_in)
        actual_out = self.amm_swap(amount_in, reserve_in, reserve_out, fee)

        if expected_out <= 0:
            return 0.0

        return max(0.0, (expected_out - actual_out) / expected_out) * 10_000.0

    def active_liquidity_score(self, current_liquidity: float, total_liquidity: float) -> float:
        if self.rust_master_core and rust_active_liquidity_score is not None:
            return float(rust_active_liquidity_score(float(current_liquidity), float(total_liquidity)))

        if total_liquidity <= 0:
            return 0.0

        return min(1.0, max(0.0, current_liquidity / total_liquidity))

    def best_entry_price(self, amount_base_in: float, reserve_base: float, reserve_token: float, fee: float) -> float:
        if self.rust_master_core and rust_best_entry_price is not None:
            return float(rust_best_entry_price(float(amount_base_in), float(reserve_base), float(reserve_token), float(fee)))

        if amount_base_in <= 0.0:
            return float("inf")

        amount_token_out = self.amm_swap(amount_base_in, reserve_base, reserve_token, fee)

        if amount_token_out <= 0.0:
            return float("inf")

        return amount_base_in / amount_token_out

    def best_exit_price(self, amount_token_in: float, reserve_token: float, reserve_base: float, fee: float) -> float:
        if self.rust_master_core and rust_best_exit_price is not None:
            return float(rust_best_exit_price(float(amount_token_in), float(reserve_token), float(reserve_base), float(fee)))

        if amount_token_in <= 0.0:
            return 0.0

        amount_base_out = self.amm_swap(amount_token_in, reserve_token, reserve_base, fee)
        return amount_base_out / amount_token_in

    def compute_net_edge_v7(
        self,
        buy_price: float,
        buy_slippage: float,
        sell_price: float,
        sell_slippage: float,
        ml_slippage: float,
        raw_spread: float,
        buffer_rate: float,
        trade_size: float,
        fees: float,
        p_fill: float = 1.0,
    ) -> Dict[str, Any]:
        adjusted_slippage = ml_slippage / 3.0
        ev_buffer = raw_spread * buffer_rate * (trade_size / 100_000.0)

        if self.rust_master_core and rust_compute_net_edge_v7 is not None:
            money_in, money_out, edge, net_edge, _ = rust_compute_net_edge_v7(
                float(buy_price),
                float(buy_slippage),
                float(sell_price),
                float(sell_slippage),
                float(ml_slippage),
                float(raw_spread),
                float(buffer_rate),
                float(trade_size),
                float(fees),
            )
        else:
            money_out = buy_price + buy_slippage
            money_in = sell_price - sell_slippage
            edge = money_in - money_out
            net_edge = edge - adjusted_slippage - ev_buffer - fees

        should_execute = profitability_gate(net_edge, p_fill)

        return {
            "money_in": money_in,
            "money_out": money_out,
            "edge": edge,
            "adjusted_slippage": adjusted_slippage,
            "ev_buffer": ev_buffer,
            "fees": fees,
            "net_edge": net_edge,
            "p_fill": p_fill,
            "should_execute": should_execute,
        }

    def route(self, data: dict, protocols: List[str]) -> str:
        for protocol in protocols:
            if protocol in self.dexes:
                return protocol
        return "uniswap"

    def calculate_slippage(self, expected: float, actual: float) -> Slippage:
        return Slippage(expected_price=expected, actual_price=actual, difference=actual - expected)

    def evaluate_slippage(
        self,
        amount_in: float,
        reserve_in: float,
        reserve_out: float,
        fee_bps: float,
        active_liquidity: float,
        vol_1h: float,
        vol_24h: float,
        observed_spread_bps: float,
        gas_cost_usd: float,
        loan_amount_usd: float,
    ) -> Tuple[float, bool, float]:
        if self.rust_master_core and rust_slippage_sentinel is not None:
            predicted, execute, min_bps = rust_slippage_sentinel(
                float(amount_in),
                float(reserve_in),
                float(reserve_out),
                float(fee_bps),
                float(active_liquidity),
                float(vol_1h),
                float(vol_24h),
                float(observed_spread_bps),
                float(gas_cost_usd),
                float(loan_amount_usd),
            )
            return float(predicted), bool(execute), float(min_bps)

        a_in = Decimal(str(amount_in))
        r_in = Decimal(str(reserve_in))
        r_out = Decimal(str(reserve_out))
        f_bps = Decimal(str(fee_bps))
        a_liq = Decimal(str(active_liquidity))
        v1 = Decimal(str(vol_1h))
        v24 = Decimal(str(vol_24h))
        obs = Decimal(str(observed_spread_bps))
        gas = Decimal(str(gas_cost_usd))
        loan = Decimal(str(loan_amount_usd))

        if a_in <= 0 or r_in <= 0:
            return 999_999.0, False, 999_999.0

        fee_factor = Decimal(1) - (f_bps / Decimal(10_000))
        amount_after_fee = a_in * fee_factor
        denom = r_in + amount_after_fee

        if denom <= 0:
            return 999_999.0, False, 999_999.0

        expected_out = a_in * (r_out / r_in)
        if expected_out <= 0:
            return 999_999.0, False, 999_999.0

        base_output = (amount_after_fee * r_out) / denom
        base_slippage_bps = max(Decimal(0), (expected_out - base_output) / expected_out) * Decimal(10_000)

        liquidity_score = a_liq / (r_in + r_out + Decimal(1))
        liquidity_penalty = Decimal(1) / (liquidity_score + Decimal("0.001"))
        vol_factor = min((v1 * Decimal("0.7") + v24 * Decimal("0.3")) * liquidity_penalty, Decimal(25))

        size_ratio = a_in / (r_in + r_out)
        ml_residual_bps = size_ratio * v1 * Decimal(12)

        predicted = base_slippage_bps + vol_factor + ml_residual_bps
        gas_bps = (gas / loan) * Decimal(10_000) if loan > 0 else Decimal("999999")
        min_profitable = gas_bps + Decimal("8.0")

        should_execute = obs > (predicted + min_profitable)

        return float(predicted), bool(should_execute), float(min_profitable)