// Package lmsr implements the Logarithmic Market Scoring Rule AMM.
// Ensures sell orders always have a price — "exit at any time" mechanism.
//
// LMSR: cost(q) = b × ln(e^(q_yes/b) + e^(q_no/b))
//       price_yes = e^(q_yes/b) / (e^(q_yes/b) + e^(q_no/b))
package lmsr

import (
	"math"
	"math/big"
	"sync"
)

const nanoPerTON = 1_000_000_000.0

type MarketState struct {
	B    float64
	QYes float64
	QNo  float64
}

type Engine struct {
	mu       sync.RWMutex
	markets  map[uint64]*MarketState
	defaultB float64
}

func NewEngine(defaultBTON float64) *Engine {
	return &Engine{
		markets:  make(map[uint64]*MarketState),
		defaultB: defaultBTON * nanoPerTON,
	}
}

func (e *Engine) RegisterMarket(marketID uint64, bTON float64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.markets[marketID] = &MarketState{B: bTON * nanoPerTON}
}

func (e *Engine) get(marketID uint64) *MarketState {
	if m, ok := e.markets[marketID]; ok {
		return m
	}
	m := &MarketState{B: e.defaultB}
	e.markets[marketID] = m
	return m
}

func lmsrCost(qYes, qNo, b float64) float64 {
	return b * math.Log(math.Exp(qYes/b)+math.Exp(qNo/b))
}

func (e *Engine) Price(marketID uint64, outcome uint8) float64 {
	e.mu.RLock()
	m := e.get(marketID)
	e.mu.RUnlock()
	ey := math.Exp(m.QYes / m.B)
	en := math.Exp(m.QNo / m.B)
	if outcome == 1 {
		return ey / (ey + en)
	}
	return en / (ey + en)
}

func (e *Engine) SellProceeds(marketID uint64, outcome uint8, sharesNano *big.Int) *big.Int {
	e.mu.RLock()
	m := e.get(marketID)
	e.mu.RUnlock()
	s := float64(sharesNano.Int64())
	before := lmsrCost(m.QYes, m.QNo, m.B)
	var after float64
	if outcome == 1 {
		after = lmsrCost(math.Max(0, m.QYes-s), m.QNo, m.B)
	} else {
		after = lmsrCost(m.QYes, math.Max(0, m.QNo-s), m.B)
	}
	p := before - after
	if p < 0 { p = 0 }
	return big.NewInt(int64(math.Floor(p)))
}

func (e *Engine) SharesForTON(marketID uint64, outcome uint8, nanoTON *big.Int) *big.Int {
	e.mu.RLock()
	m := e.get(marketID)
	e.mu.RUnlock()
	target := float64(nanoTON.Int64())
	p := e.Price(marketID, outcome)
	if p < 0.001 { p = 0.001 }
	lo, hi := 0.0, target/p*2
	for i := 0; i < 64; i++ {
		mid := (lo + hi) / 2
		var c float64
		if outcome == 1 {
			c = lmsrCost(m.QYes+mid, m.QNo, m.B) - lmsrCost(m.QYes, m.QNo, m.B)
		} else {
			c = lmsrCost(m.QYes, m.QNo+mid, m.B) - lmsrCost(m.QYes, m.QNo, m.B)
		}
		if math.Abs(c-target) < 1 { break }
		if c < target { lo = mid } else { hi = mid }
	}
	return big.NewInt(int64(math.Floor((lo + hi) / 2)))
}

func (e *Engine) UpdateAfterBuy(marketID uint64, outcome uint8, sharesNano *big.Int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	m := e.get(marketID)
	s := float64(sharesNano.Int64())
	if outcome == 1 { m.QYes += s } else { m.QNo += s }
}

func (e *Engine) UpdateAfterSell(marketID uint64, outcome uint8, sharesNano *big.Int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	m := e.get(marketID)
	s := float64(sharesNano.Int64())
	if outcome == 1 {
		m.QYes = math.Max(0, m.QYes-s)
	} else {
		m.QNo = math.Max(0, m.QNo-s)
	}
}

func (e *Engine) PriceImpact(marketID uint64, outcome uint8, amountNano *big.Int) map[string]interface{} {
	e.mu.RLock()
	m := e.get(marketID)
	e.mu.RUnlock()
	pBefore := e.Price(marketID, outcome)
	shares := e.SharesForTON(marketID, outcome, amountNano)
	sf := float64(shares.Int64())
	var pAfter float64
	if outcome == 1 {
		ey := math.Exp((m.QYes + sf) / m.B)
		en := math.Exp(m.QNo / m.B)
		pAfter = ey / (ey + en)
	} else {
		ey := math.Exp(m.QYes / m.B)
		en := math.Exp((m.QNo + sf) / m.B)
		pAfter = en / (ey + en)
	}
	avgP := 0.0
	if shares.Sign() > 0 {
		avgP = float64(amountNano.Int64()) / float64(shares.Int64()) / nanoPerTON
	}
	return map[string]interface{}{
		"shares":        shares.String(),
		"cost_nano":     amountNano.String(),
		"price_before":  pBefore,
		"price_after":   pAfter,
		"avg_price":     avgP,
		"price_impact":  math.Abs(pAfter - pBefore),
		"potential_win": shares.String(),
	}
}
