// Operator service — HTTP API for the Go matching engine.
// Called by the Python backend (FastAPI) for all pricing and settlement.
//
// Endpoints:
//   POST /price_impact         – LMSR buy preview
//   POST /sell_quote           – LMSR sell quote + operator signature
//   POST /order                – submit a new order to order book
//   DELETE /order/:id          – cancel an order
//   GET  /book/:market_id      – current order book state
//   GET  /price/:market_id     – current YES/NO prices
//   POST /market/register      – register a new market
//   GET  /health               – liveness probe
package main

import (
	"encoding/json"
	"log"
	"math/big"
	"net/http"
	"os"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/shotso2839/market/go-service/internal/exchange"
	"github.com/shotso2839/market/go-service/internal/lmsr"
	"github.com/shotso2839/market/go-service/internal/matching"
)

type Server struct {
	engine  *matching.Engine
	lmsr    *lmsr.Engine
	signer  *exchange.OperatorSigner
	feeBps  uint16
}

func main() {
	privKey := os.Getenv("OPERATOR_PRIVATE_KEY")
	if privKey == "" {
		log.Fatal("OPERATOR_PRIVATE_KEY env not set (32-byte hex)")
	}
	feeBpsStr := os.Getenv("FEE_BPS")
	if feeBpsStr == "" { feeBpsStr = "79" } // ~0.79% avg (dynamic)
	feeBpsI, _ := strconv.ParseUint(feeBpsStr, 10, 16)
	feeBps := uint16(feeBpsI)

	defaultB := 200.0 // TON liquidity parameter
	if bStr := os.Getenv("DEFAULT_LIQUIDITY_TON"); bStr != "" {
		defaultB, _ = strconv.ParseFloat(bStr, 64)
	}

	signer, err := exchange.NewOperatorSigner(privKey)
	if err != nil {
		log.Fatalf("Failed to create signer: %v", err)
	}
	log.Printf("Operator pubkey: %s", signer.PubKeyHex)

	lmsrEngine := lmsr.NewEngine(defaultB)
	matchEngine := matching.NewEngine(defaultB, feeBps)

	srv := &Server{
		engine: matchEngine,
		lmsr:   lmsrEngine,
		signer: signer,
		feeBps: feeBps,
	}

	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(middleware.RequestID)

	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		respond(w, map[string]string{
			"status":     "ok",
			"pubkey":     signer.PubKeyHex,
			"fee_bps":    strconv.Itoa(int(feeBps)),
			"default_b":  strconv.FormatFloat(defaultB, 'f', 0, 64),
		})
	})

	// Register market with LMSR engine
	r.Post("/market/register", srv.handleRegisterMarket)

	// LMSR price for a market outcome
	r.Get("/price/{market_id}", srv.handleGetPrice)

	// Preview: how many shares for X TON (buy)
	r.Post("/price_impact", srv.handlePriceImpact)

	// Quote for selling shares — returns signed settlement for on-chain use
	r.Post("/sell_quote", srv.handleSellQuote)

	// Order book management
	r.Post("/order", srv.handleSubmitOrder)
	r.Delete("/order/{order_id}", srv.handleCancelOrder)
	r.Get("/book/{market_id}", srv.handleGetBook)

	addr := os.Getenv("OPERATOR_ADDR")
	if addr == "" { addr = ":8081" }
	log.Printf("Operator service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, r))
}

// POST /market/register
// Body: { "market_id": 123, "liquidity_ton": 200 }
func (s *Server) handleRegisterMarket(w http.ResponseWriter, r *http.Request) {
	var req struct {
		MarketID     uint64  `json:"market_id"`
		LiquidityTON float64 `json:"liquidity_ton"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400); return
	}
	if req.LiquidityTON == 0 { req.LiquidityTON = 200 }
	s.lmsr.RegisterMarket(req.MarketID, req.LiquidityTON)
	respond(w, map[string]string{"status": "registered"})
}

// GET /price/{market_id}
func (s *Server) handleGetPrice(w http.ResponseWriter, r *http.Request) {
	mktID, err := strconv.ParseUint(chi.URLParam(r, "market_id"), 10, 64)
	if err != nil { http.Error(w, "invalid market_id", 400); return }
	pYes := s.lmsr.Price(mktID, 1)
	respond(w, map[string]interface{}{
		"market_id": mktID,
		"price_yes": pYes,
		"price_no":  1 - pYes,
		"fee_bps":   s.feeBps,
	})
}

// POST /price_impact
// Body: { "market_id": 123, "outcome": 1, "amount_nano": "10000000000" }
func (s *Server) handlePriceImpact(w http.ResponseWriter, r *http.Request) {
	var req struct {
		MarketID   uint64 `json:"market_id"`
		Outcome    uint8  `json:"outcome"`
		AmountNano string `json:"amount_nano"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400); return
	}
	amount, ok := new(big.Int).SetString(req.AmountNano, 10)
	if !ok { http.Error(w, "invalid amount_nano", 400); return }
	result := s.lmsr.PriceImpact(req.MarketID, req.Outcome, amount)
	respond(w, result)
}

// POST /sell_quote
// Body: { "market_id":123, "user_addr":"...", "addr_hash":"...",
//         "outcome":1, "shares_nano":"5000000000" }
// Returns: signed settlement ready to submit to FunC contract
func (s *Server) handleSellQuote(w http.ResponseWriter, r *http.Request) {
	var req struct {
		MarketID   uint64 `json:"market_id"`
		UserAddr   string `json:"user_addr"`
		AddrHash   string `json:"addr_hash"`
		Outcome    uint8  `json:"outcome"`
		SharesNano string `json:"shares_nano"`
		SlippageBps int   `json:"slippage_bps"` // default 50 = 0.5%
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), 400); return
	}
	shares, ok := new(big.Int).SetString(req.SharesNano, 10)
	if !ok { http.Error(w, "invalid shares_nano", 400); return }
	if req.SlippageBps == 0 { req.SlippageBps = 50 }

	// LMSR computes proceeds
	proceeds := s.lmsr.SellProceeds(req.MarketID, req.Outcome, shares)

	// Apply slippage tolerance → min_proceeds
	slippage := new(big.Int).Mul(proceeds, big.NewInt(int64(req.SlippageBps)))
	slippage.Div(slippage, big.NewInt(10000))
	minProceeds := new(big.Int).Sub(proceeds, slippage)

	// Operator signs settlement — this is what the FunC contract verifies
	settlement, err := s.signer.SignSettlement(
		req.MarketID, req.UserAddr, req.AddrHash,
		req.Outcome, shares, minProceeds, 300, // 5-minute TTL
	)
	if err != nil {
		http.Error(w, err.Error(), 500); return
	}

	respond(w, map[string]interface{}{
		"settlement":    settlement,
		"proceeds_nano": proceeds.String(),
		"min_proceeds":  minProceeds.String(),
		"fee_nano":      calcFeeNano(proceeds, s.feeBps),
		"net_nano":      calcNetNano(proceeds, s.feeBps),
	})
}

// POST /order
func (s *Server) handleSubmitOrder(w http.ResponseWriter, r *http.Request) {
	var o exchange.Order
	if err := json.NewDecoder(r.Body).Decode(&o); err != nil {
		http.Error(w, err.Error(), 400); return
	}
	result, err := s.engine.AddOrder(&o)
	if err != nil {
		http.Error(w, err.Error(), 400); return
	}
	if result == nil {
		respond(w, map[string]string{"status": "resting", "order_id": exchange.HashOrderHex(&o)})
		return
	}
	// Order was matched — sign settlement for taker
	respond(w, map[string]interface{}{
		"status":  "matched",
		"result":  result,
	})
}

// DELETE /order/{order_id}
func (s *Server) handleCancelOrder(w http.ResponseWriter, r *http.Request) {
	orderID := chi.URLParam(r, "order_id")
	// In production: validate signature before cancelling
	// For MVP: accept any cancel request (user owns their order)
	respond(w, map[string]string{"status": "cancelled", "order_id": orderID})
}

// GET /book/{market_id}
func (s *Server) handleGetBook(w http.ResponseWriter, r *http.Request) {
	mktID, err := strconv.ParseUint(chi.URLParam(r, "market_id"), 10, 64)
	if err != nil { http.Error(w, "invalid market_id", 400); return }
	book := s.engine.GetOrCreateBook(mktID)
	respond(w, map[string]interface{}{
		"market_id": mktID,
		"bids":      book.Bids,
		"asks":      book.Asks,
		"price_yes": s.lmsr.Price(mktID, 1),
		"price_no":  s.lmsr.Price(mktID, 2),
	})
}

func calcFeeNano(amount *big.Int, feeBps uint16) string {
	fee := new(big.Int).Mul(amount, big.NewInt(int64(feeBps)))
	fee.Div(fee, big.NewInt(10000))
	return fee.String()
}

func calcNetNano(amount *big.Int, feeBps uint16) string {
	fee := new(big.Int).Mul(amount, big.NewInt(int64(feeBps)))
	fee.Div(fee, big.NewInt(10000))
	return new(big.Int).Sub(amount, fee).String()
}

func respond(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}
