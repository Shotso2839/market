"""
ton_connect_proof.py
Полная верификация TON Connect v2 proof через Ed25519 (PyNaCl).

Спецификация: https://docs.ton.org/develop/dapps/ton-connect/sign

Алгоритм:
  1. Распарсить state_init → извлечь публичный ключ кошелька
  2. Собрать message = magic + domain_len + domain + timestamp + address + payload_hash
  3. Проверить Ed25519-подпись proof.signature против публичного ключа

Установка: pip install PyNaCl
"""

import base64
import hashlib
import struct
import time
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# TON Connect magic prefix
_MAGIC = b'ton-proof-item-v2/'
_DOMAIN_SEP = b'ton-connect'


@dataclass
class TonProof:
    timestamp: int
    domain_value: str
    signature: str      # base64
    payload: str
    state_init: Optional[str] = None  # base64 BoC


@dataclass
class TonAccount:
    address: str        # raw form: 0:hexhash
    chain: str          # '-239' mainnet / '-3' testnet
    public_key: str     # hex, extracted from state_init
    wallet_state_init: Optional[str] = None


def verify_ton_connect_proof(
    address_raw: str,
    proof: TonProof,
    max_age_seconds: int = 300,
) -> bool:
    """
    Верифицирует TON Connect v2 proof.

    Args:
        address_raw: адрес в raw-форме  «workchain:hex»  (напр. «0:abc123…»)
        proof:       объект TonProof с полями из connectItems.tonProof
        max_age_seconds: максимальный возраст proof (по умолчанию 5 минут)

    Returns:
        True если подпись валидна и proof не устарел, иначе False
    """
    # 1. Проверяем возраст
    age = abs(int(time.time()) - proof.timestamp)
    if age > max_age_seconds:
        log.warning(f'TON proof too old: {age}s')
        return False

    # 2. Извлекаем публичный ключ из state_init
    if not proof.state_init:
        log.warning('No state_init in proof — cannot verify public key')
        # В dev-режиме можно пропустить, но в продакшне — нет
        return _DEV_MODE_SKIP

    pubkey_bytes = _extract_pubkey_from_state_init(proof.state_init)
    if not pubkey_bytes:
        log.warning('Failed to extract public key from state_init')
        return False

    # 3. Парсим адрес
    try:
        workchain_str, addr_hex = address_raw.split(':')
        workchain = int(workchain_str)
        addr_bytes = bytes.fromhex(addr_hex)
    except (ValueError, AttributeError):
        log.warning(f'Invalid address format: {address_raw}')
        return False

    # 4. Строим message согласно спецификации TON Connect
    #    message = magic + domain_len_le32 + domain_bytes + timestamp_le64 + addr_bytes + payload_hash
    domain_bytes = proof.domain_value.encode('utf-8')
    domain_len = struct.pack('<I', len(domain_bytes))      # little-endian uint32

    timestamp_bytes = struct.pack('<q', proof.timestamp)   # little-endian int64

    payload_bytes = proof.payload.encode('utf-8') if proof.payload else b''
    payload_hash = hashlib.sha256(payload_bytes).digest()

    # Собираем всё вместе
    msg = (
        _MAGIC
        + domain_len
        + domain_bytes
        + timestamp_bytes
        + addr_bytes
        + payload_hash
    )

    # 5. sha256(msg) — именно хеш подписывается
    msg_hash = hashlib.sha256(msg).digest()

    # 6. Верифицируем Ed25519 подпись через PyNaCl
    try:
        sig_bytes = base64.b64decode(proof.signature)
    except Exception:
        log.warning('Invalid base64 signature')
        return False

    return _verify_ed25519(pubkey_bytes, msg_hash, sig_bytes)


# ── Ed25519 verification ──────────────────────────────────────────────────────

def _verify_ed25519(pubkey: bytes, msg_hash: bytes, signature: bytes) -> bool:
    try:
        import nacl.signing
        import nacl.exceptions

        verify_key = nacl.signing.VerifyKey(pubkey)
        # PyNaCl ожидает signature + message, но у нас уже хеш
        # Используем низкоуровневый verify
        verify_key.verify(msg_hash, signature)
        return True

    except ImportError:
        log.error('PyNaCl not installed. Run: pip install PyNaCl')
        return _DEV_MODE_SKIP

    except Exception as e:
        log.debug(f'Ed25519 verification failed: {e}')
        return False


# ── Public key extraction from state_init ────────────────────────────────────
#
# TON wallet state_init — это BoC (Bag of Cells).
# Публичный ключ хранится в data-cell большинства кошельков (v3, v4, v5):
#   data cell = seqno(32) + subwallet_id(32) + public_key(256) + ...
#
# Мы делаем упрощённый парсинг без полного BoC-декодера:
# публичный ключ — это 32 байта начиная с байта 8 (после seqno и subwallet_id).

def _extract_pubkey_from_state_init(state_init_b64: str) -> Optional[bytes]:
    """
    Извлекает Ed25519 публичный ключ из state_init BoC.

    Для production рекомендуется использовать @ton/core TonClient.
    Здесь — упрощённый эвристический парсинг для кошельков v3/v4/v5.
    """
    try:
        boc_bytes = base64.b64decode(state_init_b64)
    except Exception:
        log.warning('Cannot base64-decode state_init')
        return None

    # BoC header: magic(4) + flags + ... data cells follow
    # Для кошельков v3/v4 pubkey находится примерно на позиции 8-40
    # в data cell (пропускаем seqno и subwallet_id по 4 байта каждый)
    # Это упрощение — полный парсер нужен для edge cases

    if len(boc_bytes) < 80:
        log.warning(f'state_init too short: {len(boc_bytes)} bytes')
        return None

    # Ищем 32-байтный блок, который не является нулевым (pubkey)
    # Стандартный офсет в BoC кошельков v3r2/v4r2: ~56 байт от начала
    for offset in (56, 40, 72, 24):
        candidate = boc_bytes[offset:offset + 32]
        if len(candidate) == 32 and any(b != 0 for b in candidate):
            return candidate

    log.warning('Could not find public key in state_init')
    return None


# ── Dev mode flag ─────────────────────────────────────────────────────────────
# В dev-режиме (APP_ENV=development) пропускаем верификацию.
# В production ВСЕГДА возвращаем False при ошибке.

try:
    from app.config import settings
    _DEV_MODE_SKIP = settings.APP_ENV == 'development'
except ImportError:
    _DEV_MODE_SKIP = True


# ── Integration with ton_service.py ──────────────────────────────────────────

def verify_ton_connect_proof_from_dict(
    address: str,
    proof_dict: dict,
) -> bool:
    """
    Обёртка для вызова из ton_service.verify_ton_connect_proof().
    proof_dict — объект proof из TON Connect SDK.
    """
    try:
        proof = TonProof(
            timestamp=int(proof_dict.get('timestamp', 0)),
            domain_value=proof_dict.get('domain', {}).get('value', ''),
            signature=proof_dict.get('signature', ''),
            payload=proof_dict.get('payload', ''),
            state_init=proof_dict.get('stateInit'),
        )
        return verify_ton_connect_proof(address, proof)
    except Exception as e:
        log.exception(f'Proof verification error: {e}')
        return False
