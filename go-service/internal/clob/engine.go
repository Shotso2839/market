// Package clob — офф-чейн matching engine.
//
// Polymarket аналог: оператор агрегирует ордера off-chain,
// матчит их, потом батчем вызывает fillOrders()/matchOrders() on-chain.
//
// У нас: Go-сервис хранит live-состояние рынков, обрабатывает buy/sell,
// Python FastAPI делегирует сюда ценообразование через HTTP.
package clob

import (
	"math"
	"sync"
	"time"

	"github.com/shotso2839/market/go-service/internal/lmsr"
	"github.com/shotso2839/market/go-service/internal/signing"
)

// MarketState — полное состояние рынка в памяти.
type MarketState struct {
	mu      sync.RWMutex
	Market  *lmsr.Market
	ClosesAt time.Time
	Status   int // 0=open 1=closed 2=resolved 3=cancelled
	Winner   int // 0=none 1=yes 2=no
}

const (
	StatusOpen      = 0
	StatusClosed    = 1
	StatusResolved  = 2
	StatusCancelled = 3
)

// Engine — главный объект сервиса.
type Engine struct {
	mu      sync.RWMutex
	markets map[string]*MarketState
	signer  *signing.OperatorSigner
}

// NewEngine создаёт движок с оператором.
func NewEngine(signer *signing.OperatorSigner) *Engine {
	return &Engine{
		markets: make(map[string]*MarketState),
		signer:  signer,
	}
}

// RegisterMarket — создать рынок (аналог TokenRegistered у Polymarket).
func (e *Engine) RegisterMarket(id string, bParam float64, closesAt time.Time) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.markets[id] = &MarketState{
		Market:   lmsr.NewMarket(id, bParam),
		ClosesAt: closesAt,
		Status:   StatusOpen,
	}
}

// GetMarket — получить состояние рынка.
func (e *Engine) GetMarket(id string) (*MarketState, bool) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	m, ok := e.markets[id]
	return m, ok
}

// BuyRequest — запрос на покупку от Python API.
type BuyRequest struct {
	MarketID   string
	SenderAddr []byte
	Outcome    int
	AmountNanos float64
}

// BuyResponse — ответ: сколько шеров и цена.
type BuyResponse struct {
	Shares      float64 `json:"shares"`
	CostNanos   float64 `json:"cost_nanos"`
	PriceBefore float64 `json:"price_before"`
	PriceAfter  float64 `json:"price_after"`
	PriceImpact float64 `json:"price_impact"`
}

// ProcessBuy — обработка покупки (fillOrder аналог).
// Python вызывает это перед отправкой tx пользователю.
func (e *Engine) ProcessBuy(req BuyRequest) (*BuyResponse, error) {
	m, ok := e.GetMarket(req.MarketID)
	if !ok {
		return nil, ErrMarketNotFound
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	if m.Status != StatusOpen {
		return nil, ErrMarketClosed
	}
	if time.Now().After(m.ClosesAt) {
		return nil, ErrBettingClosed
	}

	impact := m.Market.PriceImpact(req.Outcome, req.AmountNanos)
	shares, cost := m.Market.BuyShares(req.Outcome, req.AmountNanos)

	return &BuyResponse{
		Shares:      shares,
		CostNanos:   cost,
		PriceBefore: impact.PriceBefore,
		PriceAfter:  impact.PriceAfter,
		PriceImpact: impact.PriceImpact,
	}, nil
}

// SellRequest — запрос на продажу.
type SellRequest struct {
	MarketID   string
	SenderAddr []byte
	Outcome    int
	Shares     float64
}

// SellResponse — подписанный ордер готовый для контракта.
type SellResponse struct {
	ProceedsNanos float64 `json:"proceeds_nanos"`
	SigHex        string  `json:"sig_hex"`
	ExpiresAt     uint32  `json:"expires_at"`
	Outcome       int     `json:"outcome"`
	SharesUnits   uint64  `json:"shares_units"`
}

// ProcessSell — обработка продажи (matchOrders аналог).
// Go считает LMSR-цену → подписывает → возвращает подпись фронтенду.
// Фронтенд отправляет подпись в CTFExchange.fc op 0x12.
func (e *Engine) ProcessSell(req SellRequest) (*SellResponse, error) {
	m, ok := e.GetMarket(req.MarketID)
	if !ok {
		return nil, ErrMarketNotFound
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	if m.Status != StatusOpen {
		return nil, ErrMarketClosed
	}

	proceeds := m.Market.ProceedsNanos(req.Outcome, req.Shares)
	m.Market.SellShares(req.Outcome, req.Shares)

	sharesUnits := uint64(math.Round(req.Shares * lmsr.SharesScale))
	priceNanos := uint64(math.Round(proceeds))

	signed, err := e.signer.SignSellOrder(
		req.SenderAddr,
		uint8(req.Outcome),
		sharesUnits,
		priceNanos,
		120, // 2 минуты TTL
	)
	if err != nil {
		return nil, err
	}

	return &SellResponse{
		ProceedsNanos: proceeds,
		SigHex:        signed.SigHex,
		ExpiresAt:     signed.Order.ExpiresAt,
		Outcome:       req.Outcome,
		SharesUnits:   sharesUnits,
	}, nil
}

// PriceRequest — запрос текущей цены и price impact.
type PriceRequest struct {
	MarketID    string
	Outcome     int
	AmountNanos float64
}

// PriceResponse — ответ с ценой.
type PriceResponse struct {
	PriceYes    float64 `json:"price_yes"`
	PriceNo     float64 `json:"price_no"`
	ProbBps     int     `json:"prob_bps"`     // 5000 = 50%
	Impact      *lmsr.PriceImpactResult `json:"impact,omitempty"`
}

// GetPrice — текущая цена и price impact для UI.
func (e *Engine) GetPrice(req PriceRequest) (*PriceResponse, error) {
	m, ok := e.GetMarket(req.MarketID)
	if !ok {
		return nil, ErrMarketNotFound
	}

	m.mu.RLock()
	defer m.mu.RUnlock()

	pYes := m.Market.PriceYes()
	pNo  := m.Market.PriceNo()
	probBps := int(pYes * 10000)

	resp := &PriceResponse{
		PriceYes: pYes,
		PriceNo:  pNo,
		ProbBps:  probBps,
	}

	if req.AmountNanos > 0 {
		impact := m.Market.PriceImpact(req.Outcome, req.AmountNanos)
		resp.Impact = &impact
	}

	return resp, nil
}

// Resolve — разрешение рынка оператором (oracle).
func (e *Engine) Resolve(marketID string, winner int) error {
	m, ok := e.GetMarket(marketID)
	if !ok {
		return ErrMarketNotFound
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.Status >= StatusResolved {
		return ErrAlreadyResolved
	}
	m.Status = StatusResolved
	m.Winner = winner
	return nil
}

// Sentinel errors
var (
	ErrMarketNotFound  = marketErr("market not found")
	ErrMarketClosed    = marketErr("market is closed")
	ErrBettingClosed   = marketErr("betting window closed")
	ErrAlreadyResolved = marketErr("market already resolved")
)

type marketErr string

func (e marketErr) Error() string { return string(e) }
