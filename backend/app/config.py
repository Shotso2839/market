from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tonpred"

    # TON blockchain
    TON_API_KEY: str = ""
    TON_NETWORK: str = "testnet"  # "mainnet" | "testnet"
    TON_API_URL: str = "https://testnet.toncenter.com/api/v2"

    # Smart contract
    PREDICTION_CONTRACT_ADDRESS: str = ""  # Deploy and set here

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    MINI_APP_URL: str = "https://t.me/your_bot/app"

    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://t.me",
    ]

    # Fees
    PLATFORM_FEE_BPS: int = 200  # 2% in basis points (200 / 10000)

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
