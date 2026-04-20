// Package exchange implements the TON port of Polymarket's CTF Exchange order protocol.
//
// Polymarket uses EIP-712 typed data + ECDSA for order signing.
// We use a similar structured approach but with ed25519 (native TON).
//
// Order lifecycle (mirrors Polymarket):
//   1. User creates and signs a buy/sell order off-chain
//   2. Go operator validates, matches, and signs settlement
//   3. User (or operator) submits signed settlement to FunC contract
//   4. Contract verifies ed25519 sig and settles atomically
package exchange

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"math/big"
	"time"
)

// Side mirrors Polymarket's Side enum (BUY=0, SELL=1)
type Side uint8

const (
	SideBuy  Side = 0
	SideSell Side = 1
)

// MatchType mirrors Polymarket's MatchType (MINT/MERGE/COMPLEMENTARY)
type MatchType uint8

const (
	MatchMint          MatchType = 0 // BUY + BUY  → mint new share pair
	MatchMerge         MatchType = 1 // SELL + SELL → merge, release collateral
	MatchComplementary MatchType = 2 // one BUY + one SELL (standard fill)
)

// Order mirrors Polymarket's Order struct from OrderStructs.sol
// Fields map 1:1 to Polymarket's EIP-712 typed data.
type Order struct {
	// Salt: unique identifier to prevent replay
	Salt uint64 `json:"salt"`

	// Maker: TON wallet address (hex) of the order creator
	Maker string `json:"maker"`

	// MarketID: 64-bit unique market identifier
	MarketID uint64 `json:"market_id"`

	// Outcome: 1=YES, 2=NO
	Outcome uint8 `json:"outcome"`

	// MakerAmount: collateral (TON nanotons) the maker provides (BUY)
	//              or shares the maker provides (SELL)
	MakerAmount *big.Int `json:"maker_amount"`

	// TakerAmount: shares the maker wants (BUY)
	//              or collateral (nanotons) the maker wants (SELL)
	TakerAmount *big.Int `json:"taker_amount"`

	// Expiration: unix timestamp after which order is invalid
	Expiration uint32 `json:"expiration"`

	// Nonce: for on-chain order cancellation
	Nonce uint64 `json:"nonce"`

	// FeeRateBps: fee in basis points (dynamic: θ×p×(1-p) × 10000)
	FeeRateBps uint16 `json:"fee_rate_bps"`

	// Side: BUY or SELL
	Side Side `json:"side"`

	// Signature: ed25519 signature of order hash (64 bytes hex)
	Signature string `json:"signature"`
}

// OrderStatus tracks fill state (mirrors Polymarket's OrderStatus)
type OrderStatus struct {
	IsFilledOrCancelled bool     `json:"is_filled_or_cancelled"`
	Remaining           *big.Int `json:"remaining"`
}

// SignedSettlement is what the Go operator sends to the FunC contract
// for sell_shares (op 0x11). Mirrors Polymarket's signed fill instruction.
type SignedSettlement struct {
	MarketID    uint64 `json:"market_id"`
	UserAddr    string `json:"user_addr"`    // TON address hex
	AddrHash    string `json:"addr_hash"`    // 256-bit hash
	Outcome     uint8  `json:"outcome"`
	Shares      *big.Int `json:"shares"`
	MinProceeds *big.Int `json:"min_proceeds"`
	Expiry      uint32   `json:"expiry"`
	Signature   string   `json:"signature"` // ed25519 hex 64 bytes
}

// OperatorSigner holds the ed25519 keypair for the Go operator service.
// Equivalent to Polymarket's operator address that calls fillOrder().
type OperatorSigner struct {
	privateKey ed25519.PrivateKey
	PublicKey  ed25519.PublicKey
	PubKeyHex  string
}

// NewOperatorSigner creates a signer from a hex-encoded private key seed (32 bytes).
func NewOperatorSigner(privKeyHex string) (*OperatorSigner, error) {
	seed, err := hex.DecodeString(privKeyHex)
	if err != nil || len(seed) != 32 {
		return nil, errors.New("invalid private key: must be 32-byte hex")
	}
	priv := ed25519.NewKeyFromSeed(seed)
	pub := priv.Public().(ed25519.PublicKey)
	return &OperatorSigner{
		privateKey: priv,
		PublicKey:  pub,
		PubKeyHex:  hex.EncodeToString(pub),
	}, nil
}

// HashOrder computes the canonical hash of an order.
// Equivalent to Polymarket's EIP-712 ORDER_TYPEHASH hashing.
// Layout: salt(8) | maker_len(1) | maker | market_id(8) | outcome(1) |
//         maker_amount(varint) | taker_amount(varint) |
//         expiration(4) | nonce(8) | fee_rate_bps(2) | side(1)
func HashOrder(o *Order) []byte {
	h := sha256.New()
	b8 := make([]byte, 8)
	b4 := make([]byte, 4)
	b2 := make([]byte, 2)

	binary.BigEndian.PutUint64(b8, o.Salt)
	h.Write(b8)

	makerBytes, _ := hex.DecodeString(o.Maker)
	h.Write([]byte{byte(len(makerBytes))})
	h.Write(makerBytes)

	binary.BigEndian.PutUint64(b8, o.MarketID)
	h.Write(b8)
	h.Write([]byte{o.Outcome})

	h.Write(bigIntToBytes32(o.MakerAmount))
	h.Write(bigIntToBytes32(o.TakerAmount))

	binary.BigEndian.PutUint32(b4, o.Expiration)
	h.Write(b4)
	binary.BigEndian.PutUint64(b8, o.Nonce)
	h.Write(b8)
	binary.BigEndian.PutUint16(b2, o.FeeRateBps)
	h.Write(b2)
	h.Write([]byte{byte(o.Side)})

	return h.Sum(nil)
}

// ValidateOrderSignature verifies the maker's ed25519 signature on an order.
// Equivalent to Polymarket's signature validation in Signatures.sol.
func ValidateOrderSignature(o *Order) error {
	if o.Expiration > 0 && uint32(time.Now().Unix()) > o.Expiration {
		return errors.New("order expired")
	}
	sigBytes, err := hex.DecodeString(o.Signature)
	if err != nil || len(sigBytes) != 64 {
		return errors.New("invalid signature format")
	}
	makerPubKey, err := hex.DecodeString(o.Maker)
	if err != nil || len(makerPubKey) != 32 {
		return errors.New("invalid maker address: expected 32-byte ed25519 pubkey hex")
	}
	hash := HashOrder(o)
	if !ed25519.Verify(ed25519.PublicKey(makerPubKey), hash, sigBytes) {
		return errors.New("invalid order signature")
	}
	return nil
}

// HashSettlement computes the payload that the operator signs for sell_shares.
// This is what the FunC contract verifies on-chain via check_data_signature.
// Layout must EXACTLY match verify_op_sig() in CTFExchange.fc:
//   market_id(8) | addr_hash(32) | outcome(1) |
//   shares(varint) | min_proceeds(varint) | expiry(4)
func HashSettlement(marketID uint64, addrHash []byte, outcome uint8,
	shares, minProceeds *big.Int, expiry uint32) []byte {

	h := sha256.New()
	b8 := make([]byte, 8)
	b4 := make([]byte, 4)

	binary.BigEndian.PutUint64(b8, marketID)
	h.Write(b8)

	// addr_hash must be exactly 32 bytes
	padded := make([]byte, 32)
	copy(padded[32-len(addrHash):], addrHash)
	h.Write(padded)

	h.Write([]byte{outcome})
	h.Write(bigIntToBytes32(shares))
	h.Write(bigIntToBytes32(minProceeds))

	binary.BigEndian.PutUint32(b4, expiry)
	h.Write(b4)

	return h.Sum(nil)
}

// SignSettlement creates a SignedSettlement authorising a sell_shares call.
// The operator calls this after validating the LMSR sell price.
// Equivalent to Polymarket operator calling fillOrder on-chain.
func (s *OperatorSigner) SignSettlement(
	marketID uint64,
	userAddr string,
	addrHashHex string,
	outcome uint8,
	shares *big.Int,
	minProceeds *big.Int,
	ttlSeconds int,
) (*SignedSettlement, error) {

	addrHash, err := hex.DecodeString(addrHashHex)
	if err != nil {
		return nil, err
	}
	expiry := uint32(time.Now().Unix()) + uint32(ttlSeconds)
	payload := HashSettlement(marketID, addrHash, outcome, shares, minProceeds, expiry)
	sig := ed25519.Sign(s.privateKey, payload)

	return &SignedSettlement{
		MarketID:    marketID,
		UserAddr:    userAddr,
		AddrHash:    addrHashHex,
		Outcome:     outcome,
		Shares:      shares,
		MinProceeds: minProceeds,
		Expiry:      expiry,
		Signature:   hex.EncodeToString(sig),
	}, nil
}

// DeriveMatchType mirrors Polymarket's _deriveMatchType() in Trading.sol.
// BUY+BUY=MINT (split collateral into tokens), SELL+SELL=MERGE (merge tokens
// into collateral), BUY+SELL=COMPLEMENTARY (standard fill).
func DeriveMatchType(taker, maker *Order) MatchType {
	if taker.Side == SideBuy && maker.Side == SideBuy {
		return MatchMint
	}
	if taker.Side == SideSell && maker.Side == SideSell {
		return MatchMerge
	}
	return MatchComplementary
}

// ValidatePriceCompatibility mirrors Polymarket's price check in Trading.sol.
// Uses cross-multiplication to avoid division precision loss.
// Returns true if taker price >= maker price (taker accepts maker's terms).
func ValidatePriceCompatibility(taker, maker *Order) bool {
	// Cross-multiply: takerMaker * makerTaker <= takerTaker * makerMaker
	// (equivalent to: takerMakerAmt/takerTakerAmt >= makerMakerAmt/makerTakerAmt)
	lhs := new(big.Int).Mul(taker.MakerAmount, maker.TakerAmount)
	rhs := new(big.Int).Mul(taker.TakerAmount, maker.MakerAmount)
	return lhs.Cmp(rhs) >= 0
}

// bigIntToBytes32 encodes a big.Int as a 32-byte big-endian slice.
func bigIntToBytes32(n *big.Int) []byte {
	if n == nil {
		return make([]byte, 32)
	}
	b := n.Bytes()
	if len(b) >= 32 {
		return b[len(b)-32:]
	}
	padded := make([]byte, 32)
	copy(padded[32-len(b):], b)
	return padded
}

// HashOrderHex returns hex-encoded order hash (used as order ID).
func HashOrderHex(o *Order) string {
	return hex.EncodeToString(HashOrder(o))
}
