// Package signing — ed25519 подпись ордеров оператором.
//
// Polymarket аналог: оператор подписывает Order struct (EIP-712)
// перед вызовом CTFExchange.fillOrder() / matchOrders().
//
// У нас: Go-сервис подписывает sell-ордера ed25519 перед тем как
// пользователь отправляет tx в CTFExchange.fc (op 0x12).
package signing

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"time"
)

// OperatorSigner — ключевая пара оператора.
// Приватный ключ хранится в Go-сервисе, публичный — в смарт-контракте.
type OperatorSigner struct {
	priv   ed25519.PrivateKey
	PubKey ed25519.PublicKey
}

// NewOperatorSigner — генерация новой ключевой пары.
func NewOperatorSigner() (*OperatorSigner, error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	return &OperatorSigner{priv: priv, PubKey: pub}, nil
}

// NewOperatorSignerFromHex — загрузка ключевой пары из hex.
func NewOperatorSignerFromHex(privHex string) (*OperatorSigner, error) {
	b, err := hex.DecodeString(privHex)
	if err != nil {
		return nil, err
	}
	if len(b) != ed25519.PrivateKeySize {
		return nil, errors.New("invalid ed25519 private key length")
	}
	priv := ed25519.PrivateKey(b)
	return &OperatorSigner{
		priv:   priv,
		PubKey: priv.Public().(ed25519.PublicKey),
	}, nil
}

// SellOrder — данные sell-ордера (аналог Polymarket Order struct).
// Содержит всё что нужно контракту для верификации и выплаты.
type SellOrder struct {
	SenderAddr []byte // 32-byte hash адреса пользователя
	Outcome    uint8  // 1=YES 2=NO
	Shares     uint64 // шеры × 10^6
	PriceNanos uint64 // выручка в наноTON
	ExpiresAt  uint32 // unix timestamp — подпись действительна до
}

// bytes — сериализация для подписи (детерминированная).
func (o *SellOrder) bytes() []byte {
	buf := make([]byte, 32+1+8+8+4)
	copy(buf[0:32], o.SenderAddr)
	buf[32] = o.Outcome
	binary.BigEndian.PutUint64(buf[33:41], o.Shares)
	binary.BigEndian.PutUint64(buf[41:49], o.PriceNanos)
	binary.BigEndian.PutUint32(buf[49:53], o.ExpiresAt)
	return buf
}

// ResolveOrder — данные resolve-ордера для подписи оператором.
type ResolveOrder struct {
	SenderAddr []byte
	Outcome    uint8
	ClosesAt   uint32
}

func (r *ResolveOrder) bytes() []byte {
	buf := make([]byte, 32+1+4)
	copy(buf[0:32], r.SenderAddr)
	buf[32] = r.Outcome
	binary.BigEndian.PutUint32(buf[33:37], r.ClosesAt)
	return buf
}

// SignedOrder — подписанный ордер, готовый к отправке в контракт.
type SignedOrder struct {
	Order  *SellOrder `json:"order"`
	SigHex string     `json:"sig_hex"` // 128 hex chars = 64 bytes ed25519
}

// SignSellOrder — подпись sell-ордера.
// Вызывается Go-сервисом перед тем как вернуть цену продажи фронтенду.
// Фронтенд отправляет sig в контракт (op 0x12 match_orders).
func (s *OperatorSigner) SignSellOrder(
	senderAddr []byte,
	outcome uint8,
	shares uint64,
	priceNanos uint64,
	ttl int,
) (*SignedOrder, error) {
	order := &SellOrder{
		SenderAddr: senderAddr,
		Outcome:    outcome,
		Shares:     shares,
		PriceNanos: priceNanos,
		ExpiresAt:  uint32(time.Now().Unix()) + uint32(ttl),
	}
	sig := ed25519.Sign(s.priv, order.bytes())
	return &SignedOrder{
		Order:  order,
		SigHex: hex.EncodeToString(sig),
	}, nil
}

// SignResolve — подпись разрешения рынка оператором (оракул).
func (s *OperatorSigner) SignResolve(
	senderAddr []byte,
	outcome uint8,
	closesAt uint32,
) (string, error) {
	r := &ResolveOrder{SenderAddr: senderAddr, Outcome: outcome, ClosesAt: closesAt}
	sig := ed25519.Sign(s.priv, r.bytes())
	return hex.EncodeToString(sig), nil
}

// SignRegister — подпись активации рынка (TokenRegistered аналог).
func (s *OperatorSigner) SignRegister(senderAddr []byte, closesAt uint32) (string, error) {
	msg := make([]byte, 32+4)
	copy(msg[0:32], senderAddr)
	binary.BigEndian.PutUint32(msg[32:36], closesAt)
	sig := ed25519.Sign(s.priv, msg)
	return hex.EncodeToString(sig), nil
}

// Verify — проверка подписи (для тестов).
func (s *OperatorSigner) Verify(order *SellOrder, sigHex string) bool {
	sig, err := hex.DecodeString(sigHex)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(s.PubKey, order.bytes(), sig)
}

// PubKeyHex — hex публичного ключа (кладётся в смарт-контракт при деплое).
func (s *OperatorSigner) PubKeyHex() string {
	return hex.EncodeToString(s.PubKey)
}
