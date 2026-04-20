from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import UserUpdate, UserResponse
from app.services.ton_service import ton_service

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the authenticated user's profile."""
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update username or link/change TON wallet address."""
    if data.ton_address:
        # Ensure no other user has claimed this address
        result = await db.execute(
            select(User).where(
                User.ton_address == data.ton_address,
                User.id != current_user.id,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This TON address is already linked to another account",
            )
        current_user.ton_address = data.ton_address

    if data.username is not None:
        current_user.username = data.username

    db.add(current_user)
    return current_user


@router.get("/me/balance")
async def get_wallet_balance(
    current_user: User = Depends(get_current_user),
):
    """Fetch live TON balance for the user's linked wallet."""
    if not current_user.ton_address:
        raise HTTPException(status_code=400, detail="No TON wallet linked")

    balance_nano = await ton_service.get_wallet_balance(current_user.ton_address)
    from app.schemas import nano_to_ton
    return {
        "address": current_user.ton_address,
        "balance_ton": nano_to_ton(balance_nano),
        "balance_nano": balance_nano,
    }
