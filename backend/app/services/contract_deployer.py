"""
contract_deployer.py
Деплой нового контракта PredictionMarket из Python-бэкенда.

Запускает Blueprint CLI в подпроцессе, читает адрес из stdout,
сохраняет его в БД через market_service.

Требования:
  - Node.js 18+ в PATH
  - prediction-market/ Blueprint-проект рядом с бэкендом
  - В Blueprint-проекте скрипт scripts/deployPredictionMarket.ts
    принимает параметры через переменные окружения (см. ниже)

Использование:
  from app.services.contract_deployer import deploy_contract
  address = await deploy_contract(market_id, bet_closes_at, db)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.market_service import market_service

log = logging.getLogger(__name__)

# Путь к Blueprint-проекту относительно корня репозитория
BLUEPRINT_DIR = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'contract'
)

# Regex для извлечения адреса из вывода Blueprint
_ADDRESS_RE = re.compile(r'Адрес:\s*(EQ[A-Za-z0-9_\-]{46}|UQ[A-Za-z0-9_\-]{46})')


async def deploy_contract(
    market_id: str,
    bet_closes_at: datetime,
    db: AsyncSession,
    network: str = 'testnet',
) -> Optional[str]:
    """
    Деплоит контракт через Blueprint и обновляет market.contract_address в БД.
    Возвращает адрес контракта или None при ошибке.
    """
    closes_ts = int(bet_closes_at.replace(tzinfo=timezone.utc).timestamp())

    env = {
        **os.environ,
        'MARKET_ID':            market_id,
        'BET_CLOSES_AT':        str(closes_ts),
        'PLATFORM_ADDRESS':     settings.PLATFORM_TON_ADDRESS,
        'FEE_BPS':              str(settings.PLATFORM_FEE_BPS),
        'WALLET_MNEMONIC':      settings.DEPLOYER_MNEMONIC,
        # Blueprint читает эту переменную для выбора сети
        'TON_NETWORK':          network,
    }

    blueprint_dir = os.path.abspath(BLUEPRINT_DIR)
    if not os.path.isdir(blueprint_dir):
        log.error(f'Blueprint directory not found: {blueprint_dir}')
        return None

    cmd = [
        'npx', 'blueprint', 'run', 'deployPredictionMarket',
        f'--{network}',
        '--mnemonic',           # auth mode: mnemonic from env
    ]

    log.info(f'Deploying contract for market {market_id} (closes {closes_ts})')

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=blueprint_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )
        stdout = stdout_bytes.decode()
        stderr = stderr_bytes.decode()

        if proc.returncode != 0:
            log.error(f'Blueprint deploy failed (rc={proc.returncode}): {stderr}')
            return None

        # Парсим адрес из вывода скрипта
        match = _ADDRESS_RE.search(stdout)
        if not match:
            log.error(f'Could not find contract address in blueprint output:\n{stdout}')
            return None

        contract_address = match.group(1)
        log.info(f'Contract deployed: {contract_address}')

        # Сохраняем адрес в БД
        market = await market_service.get_by_id(db, market_id)
        if market:
            await market_service.set_contract_address(db, market, contract_address)

        return contract_address

    except asyncio.TimeoutError:
        log.error(f'Blueprint deploy timed out for market {market_id}')
        return None
    except Exception as e:
        log.exception(f'Unexpected deploy error: {e}')
        return None


async def deploy_contract_background(
    market_id: str,
    bet_closes_at: datetime,
    network: str = 'testnet',
) -> None:
    """
    Fire-and-forget wrapper для использования с asyncio.create_task().
    Открывает собственную DB-сессию.
    """
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        addr = await deploy_contract(market_id, bet_closes_at, db, network)
        if addr:
            log.info(f'Auto-deployed contract {addr} for market {market_id}')
        else:
            log.warning(f'Auto-deploy failed for market {market_id}')
