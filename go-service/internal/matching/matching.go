// Package matching implements the off-chain order matching engine.
//
// Mirrors Polymarket's off-chain CLOB operator that calls fillOrder /
// matchOrders on-chain. In our architecture the Go operator:
//   1. Maintains in-memory order books per market
//   2. Matches compatible BUY/SELL orders (LMSR-priced)
//   3. Signs settlement authorisations for the FunC contract
//   4. Exposes HTTP API for the Python backend to consume
package matching

import (
	"errors"
	"math/big"
	"sort"
	"sync"

	"github.com/shotso2839/market/go-service/internal/exchange"
	"github.com/shotso2839/market/go-service/internal/lmsr"
)

// OrderBook holds active orders for a single market outcome.
// Equivalent to Polymarket's per-token order book in the CLOB API.
type OrderBook struct {
	mu     sync.RWMutex
	Bids   []*exchange.Order // BUY orders, descending price
	Asks   []*exchange.Order // SELL orders, ascending price
}

// MatchResult is what the matching engine returns to the settlement layer.
type MatchResult struct {
	TakerOrder    *exchange.Order
	MakerOrders   []*exchange.Order
	FillAmounts   []*big.Int // maker fill amounts
	MatchType     exchange.MatchType
	TotalProceeds *big.Int // collateral released to taker (SELL) or shares (BUY)
	Fee           *big.Int // fee in nanoTON
}

// Engine manages order books for all active markets.
// Polymarket equivalent: the off-chain CLOB matching service.
type Engine struct {
	mu      sync.RWMutex
	books   map[uint64]*OrderBook // key: marketID
	lmsr    *lmsr.Engine
	feeBps  uint16
}

// NewEngine creates a matching engine backed by an LMSR pricer.
func NewEngine(liquidityParam float64, feeBps uint16) *Engine {
	return &Engine{
		books:  make(map[uint64]*OrderBook),
		lmsr:   lmsr.NewEngine(liquidityParam),
		feeBps: feeBps,
	}
}

// GetOrCreateBook returns the order book for a market, creating it if needed.
func (e *Engine) GetOrCreateBook(marketID uint64) *OrderBook {
	e.mu.Lock()
	defer e.mu.Unlock()
	if _, ok := e.books[marketID]; !ok {
		e.books[marketID] = &OrderBook{}
	}
	return e.books[marketID]
}

// AddOrder validates and inserts an order into the book, then attempts matching.
// Mirrors Polymarket's operator receiving a new order via API.
func (e *Engine) AddOrder(o *exchange.Order) (*MatchResult, error) {
	if err := exchange.ValidateOrderSignature(o); err != nil {
		return nil, err
	}
	book := e.GetOrCreateBook(o.MarketID)
	book.mu.Lock()
	defer book.mu.Unlock()

	if o.Side == exchange.SideBuy {
		book.Bids = append(book.Bids, o)
		sortBids(book.Bids)
	} else {
		book.Asks = append(book.Asks, o)
		sortAsks(book.Asks)
	}

	return e.tryMatch(book, o)
}

// tryMatch attempts to match the new order against the opposite side.
// Mirrors Polymarket's _fillOrder internal function in Trading.sol.
func (e *Engine) tryMatch(book *OrderBook, taker *exchange.Order) (*MatchResult, error) {
	if taker.Side == exchange.SideBuy {
		return e.matchBuyOrder(book, taker)
	}
	return e.matchSellOrder(book, taker)
}

// matchBuyOrder fills a BUY order against available SELL (ask) orders.
func (e *Engine) matchBuyOrder(book *OrderBook, taker *exchange.Order) (*MatchResult, error) {
	if len(book.Asks) == 0 {
		return nil, nil // no match yet, order is resting
	}

	remaining := new(big.Int).Set(taker.MakerAmount) // collateral available
	var matched []*exchange.Order
	var fillAmts []*big.Int
	totalShares := new(big.Int)

	for _, ask := range book.Asks {
		if remaining.Sign() == 0 { break }
		if !exchange.ValidatePriceCompatibility(taker, ask) { break }

		// How much collateral this ask can absorb
		fill := minBigInt(remaining, ask.MakerAmount) // shares from ask
		collateralNeeded := new(big.Int).Mul(fill, ask.TakerAmount)
		collateralNeeded.Div(collateralNeeded, ask.MakerAmount)

		if collateralNeeded.Cmp(remaining) > 0 {
			// Partial fill
			fill.Mul(remaining, ask.MakerAmount)
			fill.Div(fill, ask.TakerAmount)
		}

		matched = append(matched, ask)
		fillAmts = append(fillAmts, fill)
		totalShares.Add(totalShares, fill)
		remaining.Sub(remaining, collateralNeeded)
	}

	if len(matched) == 0 {
		return nil, nil
	}

	fee := calcFee(taker.MakerAmount, e.feeBps)
	return &MatchResult{
		TakerOrder:    taker,
		MakerOrders:   matched,
		FillAmounts:   fillAmts,
		MatchType:     exchange.DeriveMatchType(taker, matched[0]),
		TotalProceeds: totalShares,
		Fee:           fee,
	}, nil
}

// matchSellOrder fills a SELL order against available BUY (bid) orders.
// Key function: this is what enables "exit at any time" — a SELL order
// is matched either against an existing BUY or against the LMSR AMM.
func (e *Engine) matchSellOrder(book *OrderBook, taker *exchange.Order) (*MatchResult, error) {
	sharesToSell := new(big.Int).Set(taker.MakerAmount)
	var matched []*exchange.Order
	var fillAmts []*big.Int
	totalProceeds := new(big.Int)

	// First: try matching against resting BID orders (P2P)
	for _, bid := range book.Bids {
		if sharesToSell.Sign() == 0 { break }
		if !exchange.ValidatePriceCompatibility(taker, bid) { break }

		fill := minBigInt(sharesToSell, bid.TakerAmount)
		proceeds := new(big.Int).Mul(fill, bid.MakerAmount)
		proceeds.Div(proceeds, bid.TakerAmount)

		matched = append(matched, bid)
		fillAmts = append(fillAmts, fill)
		totalProceeds.Add(totalProceeds, proceeds)
		sharesToSell.Sub(sharesToSell, fill)
	}

	// Second: fill remainder against LMSR AMM (always available)
	if sharesToSell.Sign() > 0 {
		lmsrProceeds := e.lmsr.SellProceeds(taker.MarketID, taker.Outcome, sharesToSell)
		totalProceeds.Add(totalProceeds, lmsrProceeds)
		sharesToSell.SetInt64(0)
	}

	if totalProceeds.Sign() == 0 {
		return nil, errors.New("no proceeds available")
	}

	// Check against taker's minimum (slippage protection)
	if totalProceeds.Cmp(taker.TakerAmount) < 0 {
		return nil, errors.New("proceeds below minimum: slippage too high")
	}

	fee := calcFee(totalProceeds, e.feeBps)
	return &MatchResult{
		TakerOrder:    taker,
		MakerOrders:   matched,
		FillAmounts:   fillAmts,
		MatchType:     exchange.MatchComplementary,
		TotalProceeds: totalProceeds,
		Fee:           fee,
	}, nil
}

// CancelOrder removes an order from the book.
// Mirrors Polymarket's order cancellation mechanism.
func (e *Engine) CancelOrder(marketID uint64, orderHash string) error {
	book := e.GetOrCreateBook(marketID)
	book.mu.Lock()
	defer book.mu.Unlock()

	book.Bids = filterOut(book.Bids, orderHash)
	book.Asks = filterOut(book.Asks, orderHash)
	return nil
}

// LMSRPrice returns the current LMSR price for an outcome.
// Used by the frontend to display live market prices.
func (e *Engine) LMSRPrice(marketID uint64, outcome uint8) float64 {
	return e.lmsr.Price(marketID, outcome)
}

// PriceImpact computes buy cost and price impact for a given TON amount.
func (e *Engine) PriceImpact(marketID uint64, outcome uint8, amountNano *big.Int) map[string]interface{} {
	return e.lmsr.PriceImpact(marketID, outcome, amountNano)
}

// ─── helpers ──────────────────────────────────────────────────

func calcFee(amount *big.Int, feeBps uint16) *big.Int {
	fee := new(big.Int).Mul(amount, big.NewInt(int64(feeBps)))
	fee.Div(fee, big.NewInt(10000))
	return fee
}

func minBigInt(a, b *big.Int) *big.Int {
	if a.Cmp(b) <= 0 { return new(big.Int).Set(a) }
	return new(big.Int).Set(b)
}

func sortBids(orders []*exchange.Order) {
	sort.Slice(orders, func(i, j int) bool {
		// Higher bid price first: makerAmount/takerAmount desc
		li := new(big.Int).Mul(orders[i].MakerAmount, orders[j].TakerAmount)
		ri := new(big.Int).Mul(orders[j].MakerAmount, orders[i].TakerAmount)
		return li.Cmp(ri) > 0
	})
}

func sortAsks(orders []*exchange.Order) {
	sort.Slice(orders, func(i, j int) bool {
		// Lower ask price first: makerAmount/takerAmount asc
		li := new(big.Int).Mul(orders[i].MakerAmount, orders[j].TakerAmount)
		ri := new(big.Int).Mul(orders[j].MakerAmount, orders[i].TakerAmount)
		return li.Cmp(ri) < 0
	})
}

func filterOut(orders []*exchange.Order, hash string) []*exchange.Order {
	out := orders[:0]
	for _, o := range orders {
		if exchange.HashOrderHex(o) != hash {
			out = append(out, o)
		}
	}
	return out
}
