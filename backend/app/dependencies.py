from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User
from app.services.ton_service import ton_service


async def get_current_user(
    x_init_data: str = Header(..., description="Telegram WebApp initData"),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency: validates Telegram initData header and returns User.
    Creates user on first login (upsert by telegram_id).
    """
    tg_user = ton_service.verify_telegram_init_data(x_init_data)
    if not tg_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData",
        )

    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user id in initData")

    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=tg_user.get("username") or tg_user.get("first_name"),
        )
        db.add(user)
        await db.flush()

    return user


async def get_current_user_optional(
    x_init_data: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if not x_init_data:
        return None
    try:
        return await get_current_user(x_init_data=x_init_data, db=db)
    except HTTPException:
        return None
