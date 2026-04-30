from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from eth_abi import encode
from web3 import Web3

from .polygon_market_registry import VENUES, VenueSpec
from .route_step_encoder import EncodedStep, PROTOCOL_UNISWAP_V2, PROTOCOL_UNISWAP_V3

PROTOCOL_CURVE = 3
PROTOCOL_BALANCER = 4
PROTOCOL_ALGEBRA = 5


@dataclass(frozen=True)
class SwapRequest:
    venue_name: str
    token_in: str
    token_out: str
    amount_in: int
    min_amount_out: int
    recipient: str
    deadline: int
    fee: int | None = None
    pool: str | None = None
    pool_id: bytes | None = None
    extra: dict[str, Any] | None = None


class SwapAdapter(Protocol):
    def build_step(self, request: SwapRequest) -> dict[str, Any]: ...


def _selector(signature: str) -> bytes:
    return Web3.keccak(text=signature)[:4]


def _venue(request: SwapRequest) -> VenueSpec:
    if request.venue_name not in VENUES:
        raise ValueError(f"unknown venue {request.venue_name}")
    return VENUES[request.venue_name]


class V2SwapAdapter:
    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        if venue.kind != "v2" or not venue.router:
            raise ValueError(f"venue {request.venue_name} is not executable as V2")
        data = _selector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)") + encode(
            ["uint256", "uint256", "address[]", "address", "uint256"],
            [
                int(request.amount_in),
                int(request.min_amount_out),
                [Web3.to_checksum_address(request.token_in), Web3.to_checksum_address(request.token_out)],
                Web3.to_checksum_address(request.recipient),
                int(request.deadline),
            ],
        )
        return EncodedStep(
            protocol=PROTOCOL_UNISWAP_V2,
            target=venue.router,
            approve_token=request.token_in,
            output_token=request.token_out,
            call_value=0,
            min_amount_in=request.amount_in,
            min_amount_out=request.min_amount_out,
            fee_bps=venue.default_fee_bps,
            data=data,
        ).as_institutional_step()


class UniV3SwapAdapter:
    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        if venue.kind != "v3" or not venue.router:
            raise ValueError(f"venue {request.venue_name} is not executable as UniV3")
        fee = int(request.fee if request.fee is not None else 500)
        data = _selector("exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))") + encode(
            ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
            [(
                Web3.to_checksum_address(request.token_in),
                Web3.to_checksum_address(request.token_out),
                fee,
                Web3.to_checksum_address(request.recipient),
                int(request.deadline),
                int(request.amount_in),
                int(request.min_amount_out),
                0,
            )],
        )
        return EncodedStep(
            protocol=PROTOCOL_UNISWAP_V3,
            target=venue.router,
            approve_token=request.token_in,
            output_token=request.token_out,
            call_value=0,
            min_amount_in=request.amount_in,
            min_amount_out=request.min_amount_out,
            fee_bps=max(1, int(fee / 100)),
            data=data,
        ).as_institutional_step()


class AlgebraSwapAdapter:
    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        if venue.kind != "algebra" or not venue.router:
            raise ValueError(f"venue {request.venue_name} missing Algebra router")
        data = _selector("exactInputSingle((address,address,address,uint256,uint256,uint256,uint160))") + encode(
            ["(address,address,address,uint256,uint256,uint256,uint160)"],
            [(
                Web3.to_checksum_address(request.token_in),
                Web3.to_checksum_address(request.token_out),
                Web3.to_checksum_address(request.recipient),
                int(request.deadline),
                int(request.amount_in),
                int(request.min_amount_out),
                0,
            )],
        )
        return EncodedStep(PROTOCOL_ALGEBRA, venue.router, request.token_in, request.token_out, 0, request.amount_in, request.min_amount_out, venue.default_fee_bps, data).as_institutional_step()


class CurveSwapAdapter:
    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        extra = request.extra or {}
        pool = request.pool or extra.get("pool")
        i = extra.get("i")
        j = extra.get("j")
        if venue.kind != "curve" or not pool or i is None or j is None:
            raise ValueError("Curve adapter requires pool, i, and j; failing closed")
        data = _selector("exchange(int128,int128,uint256,uint256)") + encode(
            ["int128", "int128", "uint256", "uint256"],
            [int(i), int(j), int(request.amount_in), int(request.min_amount_out)],
        )
        return EncodedStep(PROTOCOL_CURVE, pool, request.token_in, request.token_out, 0, request.amount_in, request.min_amount_out, venue.default_fee_bps, data).as_institutional_step()


class BalancerSwapAdapter:
    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        extra = request.extra or {}
        if venue.kind != "balancer" or not venue.router or not request.pool_id:
            raise ValueError("Balancer adapter requires vault router and pool_id; failing closed")
        # SingleSwap: (bytes32 poolId,uint8 kind,address assetIn,address assetOut,uint256 amount,bytes userData)
        # FundManagement: (address sender,bool fromInternalBalance,address recipient,bool toInternalBalance)
        data = _selector("swap((bytes32,uint8,address,address,uint256,bytes),(address,bool,address,bool),uint256,uint256)") + encode(
            ["(bytes32,uint8,address,address,uint256,bytes)", "(address,bool,address,bool)", "uint256", "uint256"],
            [
                (request.pool_id, 0, Web3.to_checksum_address(request.token_in), Web3.to_checksum_address(request.token_out), int(request.amount_in), b""),
                (Web3.to_checksum_address(request.recipient), False, Web3.to_checksum_address(request.recipient), False),
                int(request.min_amount_out),
                int(request.deadline),
            ],
        )
        return EncodedStep(PROTOCOL_BALANCER, venue.router, request.token_in, request.token_out, 0, request.amount_in, request.min_amount_out, 0, data).as_institutional_step()


class UniversalSwapAdapter:
    def __init__(self) -> None:
        self.adapters: dict[str, SwapAdapter] = {
            "v2": V2SwapAdapter(),
            "v3": UniV3SwapAdapter(),
            "algebra": AlgebraSwapAdapter(),
            "curve": CurveSwapAdapter(),
            "balancer": BalancerSwapAdapter(),
        }

    def build_step(self, request: SwapRequest) -> dict[str, Any]:
        venue = _venue(request)
        adapter = self.adapters.get(venue.kind)
        if adapter is None:
            raise ValueError(f"no adapter for venue kind {venue.kind}")
        return adapter.build_step(request)
