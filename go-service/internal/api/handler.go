// Package api — HTTP сервер Go-сервиса.
//
// Python FastAPI делегирует сюда все CLOB-операции:
//   POST /price       — текущая цена и price impact
//   POST /buy         — обработка покупки
//   POST /sell        — обработка продажи (возвращает подпись)
//   POST /resolve     — разрешение рынка (от оракула)
//   POST /register    — активация рынка
//   GET  /market/:id  — состояние рынка
//   GET  /health      — healthcheck
package api

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/shotso2839/market/go-service/internal/clob"
	"github.com/shotso2839/market/go-service/internal/settlement"
	"github.com/shotso2839/market/go-service/internal/signing"
)

// Handler — главный обработчик запросов.
type Handler struct {
	engine *clob.Engine
	signer *signing.OperatorSigner
}

func NewHandler(engine *clob.Engine, signer *signing.OperatorSigner) *Handler {
	return &Handler{engine: engine, signer: signer}
}

// Router — настройка роутов.
func (h *Handler) Router() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(middleware.RealIP)
	r.Use(corsMiddleware)

	r.Get("/health", h.health)
	r.Get("/operator/pubkey", h.operatorPubkey)

	r.Post("/markets/register", h.registerMarket)
	r.Get("/markets/{id}", h.getMarket)
	r.Post("/markets/{id}/price", h.getPrice)
	r.Post("/markets/{id}/buy", h.processBuy)
	r.Post("/markets/{id}/sell", h.processSell)
	r.Post("/markets/{id}/resolve", h.resolveMarket)

	return r
}

// ── Handlers ─────────────────────────────────────────────────────────────

func (h *Handler) health(w http.ResponseWriter, r *http.Request) {
	respond(w, http.StatusOK, map[string]string{"status": "ok", "service": "ton-pred-clob"})
}

func (h *Handler) operatorPubkey(w http.ResponseWriter, r *http.Request) {
	respond(w, http.StatusOK, map[string]string{
		"pubkey_hex": h.signer.PubKeyHex(),
	})
}

// registerMarket — аналог TokenRegistered у Polymarket.
func (h *Handler) registerMarket(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ID       string  `json:"id"`
		BParam   float64 `json:"b_param"`
		ClosesAt int64   `json:"closes_at"` // unix timestamp
	}
	if !decode(w, r, &req) {
		return
	}
	if req.ID == "" || req.BParam <= 0 {
		respondError(w, http.StatusBadRequest, "invalid params")
		return
	}
	closesAt := time.Unix(req.ClosesAt, 0)
	h.engine.RegisterMarket(req.ID, req.BParam, closesAt)

	// Сформировать register-payload для контракта (op 0x15)
	// Python должен отправить его в TON вместе с деплоем
	// TODO: senderAddr from context
	sigHex, _ := h.signer.SignRegister(make([]byte, 32), uint32(req.ClosesAt))
	p := &settlement.RegisterPayload{SigHex: sigHex}
	pBytes, _ := p.Bytes()

	respond(w, http.StatusOK, map[string]any{
		"market_id":       req.ID,
		"pubkey_hex":      h.signer.PubKeyHex(),
		"register_payload": settlement.HexPayload(pBytes),
	})
}

func (h *Handler) getMarket(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	m, ok := h.engine.GetMarket(id)
	if !ok {
		respondError(w, http.StatusNotFound, "market not found")
		return
	}
	m.mu.RLock()
	defer m.mu.RUnlock()
	respond(w, http.StatusOK, map[string]any{
		"id":        id,
		"status":    m.Status,
		"price_yes": m.Market.PriceYes(),
		"price_no":  m.Market.PriceNo(),
		"prob_bps":  int(m.Market.PriceYes() * 10000),
		"closes_at": m.ClosesAt.Unix(),
	})
}

func (h *Handler) getPrice(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	var req struct {
		Outcome     int     `json:"outcome"`
		AmountNanos float64 `json:"amount_nanos"`
	}
	if !decode(w, r, &req) {
		return
	}
	resp, err := h.engine.GetPrice(clob.PriceRequest{
		MarketID:    id,
		Outcome:     req.Outcome,
		AmountNanos: req.AmountNanos,
	})
	if err != nil {
		respondError(w, http.StatusBadRequest, err.Error())
		return
	}
	respond(w, http.StatusOK, resp)
}

// processBuy — обработка покупки.
// Возвращает payload для tx (op 0x10) и ожидаемое кол-во шеров.
func (h *Handler) processBuy(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	var req struct {
		SenderAddr  string  `json:"sender_addr_hex"`
		Outcome     int     `json:"outcome"`
		AmountNanos float64 `json:"amount_nanos"`
		MinShares   uint64  `json:"min_shares"`
	}
	if !decode(w, r, &req) {
		return
	}
	senderBytes := hexToBytes32(req.SenderAddr)

	buyResp, err := h.engine.ProcessBuy(clob.BuyRequest{
		MarketID:    id,
		SenderAddr:  senderBytes,
		Outcome:     req.Outcome,
		AmountNanos: req.AmountNanos,
	})
	if err != nil {
		respondError(w, http.StatusBadRequest, err.Error())
		return
	}

	// Сформировать TON tx payload (op 0x10 fill_order)
	p := &settlement.FillOrderPayload{
		Outcome:   uint8(req.Outcome),
		MinShares: req.MinShares,
	}

	respond(w, http.StatusOK, map[string]any{
		"shares":       buyResp.Shares,
		"cost_nanos":   buyResp.CostNanos,
		"price_before": buyResp.PriceBefore,
		"price_after":  buyResp.PriceAfter,
		"price_impact": buyResp.PriceImpact,
		"tx_payload":   settlement.HexPayload(p.Bytes()),
		"op_code":      "0x10",
	})
}

// processSell — обработка продажи с подписью оператора.
// Возвращает подписанный payload (op 0x12 match_orders).
// Клиент отправляет этот payload в CTFExchange.fc.
func (h *Handler) processSell(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	var req struct {
		SenderAddr string  `json:"sender_addr_hex"`
		Outcome    int     `json:"outcome"`
		Shares     float64 `json:"shares"`
	}
	if !decode(w, r, &req) {
		return
	}
	senderBytes := hexToBytes32(req.SenderAddr)

	sellResp, err := h.engine.ProcessSell(clob.SellRequest{
		MarketID:   id,
		SenderAddr: senderBytes,
		Outcome:    req.Outcome,
		Shares:     req.Shares,
	})
	if err != nil {
		respondError(w, http.StatusBadRequest, err.Error())
		return
	}

	// Сформировать TON tx payload (op 0x12 match_orders)
	p := &settlement.MatchOrdersPayload{
		Outcome:    uint8(sellResp.Outcome),
		Shares:     sellResp.SharesUnits,
		PriceNanos: uint64(sellResp.ProceedsNanos),
		ExpiresAt:  sellResp.ExpiresAt,
		SigHex:     sellResp.SigHex,
	}
	pBytes, err := p.Bytes()
	if err != nil {
		respondError(w, http.StatusInternalServerError, err.Error())
		return
	}

	respond(w, http.StatusOK, map[string]any{
		"proceeds_nanos": sellResp.ProceedsNanos,
		"sig_hex":        sellResp.SigHex,
		"expires_at":     sellResp.ExpiresAt,
		"tx_payload":     settlement.HexPayload(pBytes),
		"op_code":        "0x12",
	})
}

// resolveMarket — разрешение рынка оракулом.
func (h *Handler) resolveMarket(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	var req struct {
		Outcome    int    `json:"outcome"` // 1=yes 2=no
		SenderAddr string `json:"sender_addr_hex"`
		ClosesAt   uint32 `json:"closes_at"`
	}
	if !decode(w, r, &req) {
		return
	}

	if err := h.engine.Resolve(id, req.Outcome); err != nil {
		respondError(w, http.StatusBadRequest, err.Error())
		return
	}

	senderBytes := hexToBytes32(req.SenderAddr)
	sigHex, _ := h.signer.SignResolve(senderBytes, uint8(req.Outcome), req.ClosesAt)

	p := &settlement.ResolvePayload{Outcome: uint8(req.Outcome), SigHex: sigHex}
	pBytes, _ := p.Bytes()

	respond(w, http.StatusOK, map[string]any{
		"market_id":  id,
		"outcome":    req.Outcome,
		"sig_hex":    sigHex,
		"tx_payload": settlement.HexPayload(pBytes),
		"op_code":    "0x13",
	})
}

// ── Helpers ───────────────────────────────────────────────────────────────

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func respond(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(body)
}

func respondError(w http.ResponseWriter, code int, msg string) {
	respond(w, code, map[string]string{"error": msg})
}

func decode(w http.ResponseWriter, r *http.Request, dst any) bool {
	if err := json.NewDecoder(r.Body).Decode(dst); err != nil {
		respondError(w, http.StatusBadRequest, "invalid json: "+err.Error())
		return false
	}
	return true
}

func hexToBytes32(h string) []byte {
	b := make([]byte, 32)
	if len(h) >= 2 {
		decoded, err := decodeHex(h)
		if err == nil && len(decoded) <= 32 {
			copy(b[32-len(decoded):], decoded)
		}
	}
	return b
}

func decodeHex(s string) ([]byte, error) {
	if len(s)%2 != 0 {
		s = "0" + s
	}
	result := make([]byte, len(s)/2)
	for i := 0; i < len(s); i += 2 {
		b := uint8(0)
		for _, c := range s[i : i+2] {
			b <<= 4
			switch {
			case c >= '0' && c <= '9':
				b |= uint8(c - '0')
			case c >= 'a' && c <= 'f':
				b |= uint8(c-'a') + 10
			case c >= 'A' && c <= 'F':
				b |= uint8(c-'A') + 10
			}
		}
		result[i/2] = b
	}
	return result, nil
}
