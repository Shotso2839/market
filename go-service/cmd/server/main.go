// cmd/server/main.go — точка входа Go CLOB сервиса.
//
// Запуск: go run cmd/server/main.go
// или:    OPERATOR_KEY_HEX=<hex> PORT=8081 go run cmd/server/main.go
package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/shotso2839/market/go-service/internal/api"
	"github.com/shotso2839/market/go-service/internal/clob"
	"github.com/shotso2839/market/go-service/internal/signing"
)

func main() {
	// ── Оператор ─────────────────────────────────────────────────────
	var signer *signing.OperatorSigner
	var err error

	privHex := os.Getenv("OPERATOR_KEY_HEX")
	if privHex != "" {
		signer, err = signing.NewOperatorSignerFromHex(privHex)
		if err != nil {
			log.Fatalf("Invalid OPERATOR_KEY_HEX: %v", err)
		}
		log.Printf("Operator pubkey: %s", signer.PubKeyHex())
	} else {
		signer, err = signing.NewOperatorSigner()
		if err != nil {
			log.Fatalf("Failed to generate operator key: %v", err)
		}
		log.Printf("Generated new operator keypair")
		log.Printf("Operator pubkey: %s", signer.PubKeyHex())
		log.Printf("!!! Save this private key to OPERATOR_KEY_HEX: put it in .env !!!")
	}

	// ── CLOB Engine ───────────────────────────────────────────────────
	engine := clob.NewEngine(signer)

	// ── HTTP Server ───────────────────────────────────────────────────
	handler := api.NewHandler(engine, signer)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8081"
	}

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      handler.Router(),
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		log.Printf("TON Pred CLOB service starting on :%s", port)
		log.Printf("Python FastAPI should call this service at http://localhost:%s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server error: %v", err)
		}
	}()

	<-quit
	log.Println("Shutting down...")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	srv.Shutdown(ctx)
	log.Println("Stopped")
}
