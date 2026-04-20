from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import TonConnectPayload, TonTxVerify, TonTxResponse
from app.services.ton_service import ton_service, TonApiError

router = APIRouter()


@router.post("/connect")
async def connect_wallet(
    payload: TonConnectPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify TON Connect proof and link wallet address to the user.

    The frontend (TON Connect SDK) sends:
    - address: raw TON wallet address
    - proof: {timestamp, domain, signature, payload, state_init}
    """
    valid = ton_service.verify_ton_connect_proof(
        address=payload.address,
        domain=payload.proof.get("domain", {}).get("value", ""),
        timestamp=payload.proof.get("timestamp", 0),
        signature=payload.proof.get("signature", ""),
        payload=payload.proof.get("payload", ""),
        state_init=payload.proof.get("state_init"),
    )

    if not valid:
        raise HTTPException(status_code=401, detail="Invalid TON Connect proof")

    # Check address not taken by another user
    from sqlalchemy import select
    result = await db.execute(
        select(User).where(
            User.ton_address == payload.address,
            User.id != current_user.id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Address already linked to another account")

    current_user.ton_address = payload.address
    db.add(current_user)

    return {
        "status": "connected",
        "address": payload.address,
        "network": payload.network,
    }


@router.post("/verify-tx", response_model=dict)
async def verify_transaction(
    data: TonTxVerify,
    current_user: User = Depends(get_current_user),
):
    """
    Verify a TON transaction exists on-chain.
    Used by the frontend to confirm a bet or contract deployment.
    """
    try:
        result = await ton_service.verify_transaction(
            tx_hash=data.tx_hash,
            expected_destination=data.expected_destination,
            expected_amount_nano=data.expected_amount_nano,
        )
    except TonApiError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


@router.get("/contract/{address}/state")
async def get_contract_state(
    address: str,
    current_user: User = Depends(get_current_user),
):
    """
    Read the on-chain state of a prediction market smart contract.
    Returns pool sizes, status, and winning outcome.
    """
    try:
        state = await ton_service.get_contract_state(address)
    except TonApiError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not state:
        raise HTTPException(status_code=404, detail="Contract not found or empty state")

    from app.schemas import nano_to_ton

    status_map = {0: "open", 1: "closed", 2: "resolved", 3: "cancelled"}
    outcome_map = {0: None, 1: "yes", 2: "no"}

    return {
        "address": address,
        "status": status_map.get(state["status"], "unknown"),
        "yes_pool_ton": nano_to_ton(state["yes_pool"]),
        "no_pool_ton": nano_to_ton(state["no_pool"]),
        "total_pool_ton": nano_to_ton(state["yes_pool"] + state["no_pool"]),
        "winning_outcome": outcome_map.get(state["winning_outcome"]),
        "bet_closes_at": state["bet_closes_at"],
    }


@router.get("/balance/{address}")
async def get_address_balance(address: str):
    """Get TON balance for any address (public endpoint)."""
    from app.schemas import nano_to_ton
    try:
        balance_nano = await ton_service.get_wallet_balance(address)
        return {
            "address": address,
            "balance_ton": nano_to_ton(balance_nano),
            "balance_nano": balance_nano,
        }
    except TonApiError as e:
        raise HTTPException(status_code=502, detail=str(e))
