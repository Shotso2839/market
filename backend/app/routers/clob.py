"""
app/routers/clob.py  —  CLOB API, proxies to Go operator service.

Mirrors Polymarket's CLOB API (https://docs.polymarket.com):
  POST /clob/price_impact  → Go /price_impact  (buy preview)
  POST /clob/sell_quote    → Go /sell_quote     (signed settlement)
  GET  /clob/price/{id}    → Go /price/{id}
  GET  /clob/book/{id}     → Go /book/{id}
  POST /clob/order         → Go /order
"""
from __future__ import annotations
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.fees import dynamic_taker_fee, MarketCategory

router = APIRouter(prefix="/clob", tags=["clob"])
OPERATOR_URL = os.getenv("OPERATOR_URL", "http://localhost:8081")


class PriceImpactReq(BaseModel):
    market_id: int
    outcome: int
    amount_nano: str

class SellReq(BaseModel):
    market_id: int
    outcome: int
    shares_nano: str
    user_addr: str
    addr_hash: str
    slippage_bps: int = 50


@router.get("/price/{market_id}")
async def get_price(market_id: int):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{OPERATOR_URL}/price/{market_id}")
    if r.status_code != 200:
        raise HTTPException(502, "operator unavailable")
    return r.json()


@router.post("/price_impact")
async def price_impact(req: PriceImpactReq):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{OPERATOR_URL}/price_impact", json=req.dict())
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    data = r.json()
    p = data.get("price_before", 0.5)
    fee = dynamic_taker_fee(int(req.amount_nano),
                            p if req.outcome == 1 else 1 - p,
                            MarketCategory.OTHER, 1)
    data["fee_nano"] = fee.fee_nano
    data["fee_pct"]  = fee.effective_rate_pct
    return data


@router.post("/sell_quote")
async def sell_quote(req: SellReq):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{OPERATOR_URL}/sell_quote", json=req.dict())
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    data = r.json()
    proceeds = int(data.get("proceeds_nano", 0))
    fee = dynamic_taker_fee(proceeds, 0.5, MarketCategory.OTHER, 99)
    data["fee_breakdown"] = {
        "formula": "θ × p × (1-p)",
        "fee_nano": fee.fee_nano,
        "fee_pct":  fee.effective_rate_pct,
        "net_proceeds": fee.net_amount,
    }
    return data


@router.post("/order")
async def submit_order(order: dict):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{OPERATOR_URL}/order", json=order)
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    return r.json()


@router.delete("/order/{order_id}")
async def cancel_order(order_id: str):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.delete(f"{OPERATOR_URL}/order/{order_id}")
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    return r.json()


@router.get("/book/{market_id}")
async def get_book(market_id: int):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{OPERATOR_URL}/book/{market_id}")
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    return r.json()


@router.post("/market/register")
async def register_market(body: dict):
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{OPERATOR_URL}/market/register", json=body)
    if r.status_code != 200:
        raise HTTPException(502, r.text)
    return r.json()
