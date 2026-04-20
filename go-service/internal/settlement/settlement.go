// Package settlement — формирование TON-транзакций для CTFExchange.fc.
//
// Аналог того как Polymarket operator вызывает:
//   CTFExchange.fillOrder(order, fillAmount)
//   CTFExchange.matchOrders(takerOrder, makerOrders, ...)
//
// У нас Go-сервис формирует payload для каждого op-кода контракта.
package settlement

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"math/big"
)

const (
	OpFillOrder   = 0x10
	OpFillOrders  = 0x11
	OpMatchOrders = 0x12
	OpResolve     = 0x13
	OpClaim       = 0x14
	OpRegister    = 0x15
	OpCancel      = 0x16
)

// FillOrderPayload — payload для op 0x10 (buy_shares).
// Пользователь отправляет вместе с TON.
type FillOrderPayload struct {
	Outcome   uint8
	MinShares uint64 // slippage protection
}

// Bytes — сериализация в bytes для TON cell body.
func (p *FillOrderPayload) Bytes() []byte {
	buf := make([]byte, 4+1+8)
	binary.BigEndian.PutUint32(buf[0:4], OpFillOrder)
	buf[4] = p.Outcome
	binary.BigEndian.PutUint64(buf[5:13], p.MinShares)
	return buf
}

// MatchOrdersPayload — payload для op 0x12 (sell_shares).
// Содержит подпись оператора — контракт верифицирует.
type MatchOrdersPayload struct {
	Outcome    uint8
	Shares     uint64
	PriceNanos uint64
	ExpiresAt  uint32
	SigHex     string // 128 hex chars (64 bytes ed25519)
}

// Bytes — сериализация.
func (p *MatchOrdersPayload) Bytes() ([]byte, error) {
	sig, err := hex.DecodeString(p.SigHex)
	if err != nil || len(sig) != 64 {
		return nil, errors.New("invalid signature hex")
	}

	buf := make([]byte, 4+1+8+16+4+64)
	binary.BigEndian.PutUint32(buf[0:4], OpMatchOrders)
	buf[4] = p.Outcome
	binary.BigEndian.PutUint64(buf[5:13], p.Shares)

	// price_nanos как 128-bit big-endian (coins в TL-B)
	price := new(big.Int).SetUint64(p.PriceNanos)
	priceBuf := make([]byte, 16)
	priceBytes := price.Bytes()
	copy(priceBuf[16-len(priceBytes):], priceBytes)
	copy(buf[13:29], priceBuf)

	binary.BigEndian.PutUint32(buf[29:33], p.ExpiresAt)
	copy(buf[33:97], sig)
	return buf, nil
}

// ResolvePayload — payload для op 0x13.
type ResolvePayload struct {
	Outcome uint8
	SigHex  string
}

func (p *ResolvePayload) Bytes() ([]byte, error) {
	sig, err := hex.DecodeString(p.SigHex)
	if err != nil || len(sig) != 64 {
		return nil, errors.New("invalid signature hex")
	}
	buf := make([]byte, 4+1+64)
	binary.BigEndian.PutUint32(buf[0:4], OpResolve)
	buf[4] = p.Outcome
	copy(buf[5:69], sig)
	return buf, nil
}

// RegisterPayload — payload для op 0x15 (TokenRegistered).
type RegisterPayload struct {
	SigHex string
}

func (p *RegisterPayload) Bytes() ([]byte, error) {
	sig, err := hex.DecodeString(p.SigHex)
	if err != nil || len(sig) != 64 {
		return nil, errors.New("invalid signature hex")
	}
	buf := make([]byte, 4+64)
	binary.BigEndian.PutUint32(buf[0:4], OpRegister)
	copy(buf[4:68], sig)
	return buf, nil
}

// CancelPayload — payload для op 0x16.
func CancelPayload() []byte {
	buf := make([]byte, 4)
	binary.BigEndian.PutUint32(buf[0:4], OpCancel)
	return buf
}

// ClaimPayload — payload для op 0x14.
func ClaimPayload() []byte {
	buf := make([]byte, 4)
	binary.BigEndian.PutUint32(buf[0:4], OpClaim)
	return buf
}

// HexPayload — hex payload для отправки через TON API.
func HexPayload(b []byte) string {
	return hex.EncodeToString(b)
}
