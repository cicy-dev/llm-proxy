#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MITM_PROXY_PORT=18080

echo "=== LLM Proxy Installer ==="

CERT_DIR="$HOME/.mitmproxy"
CERT_FILE="$CERT_DIR/mitmproxy-ca-cert.pem"
CERT_PEM="$CERT_DIR/mitmproxy-ca-cert.pem"
CERT_CRT="$CERT_DIR/mitmproxy-ca-cert.crt"

install_cert() {
    echo "[1/3] Checking mitmproxy CA certificate..."
    
    if [ -f "$CERT_FILE" ]; then
        echo "Certificate found at $CERT_FILE"
    else
        echo "Certificate not found. Running mitmdump to generate CA..."
        timeout 5 mitmdump --mode regular@$MITM_PROXY_PORT > /dev/null 2>&1 || true
    fi
    
    if [ ! -f "$CERT_FILE" ]; then
        echo "ERROR: Failed to generate CA certificate"
        exit 1
    fi
    
    if [ ! -f "$CERT_CRT" ] && [ -f "$CERT_PEM" ]; then
        cp "$CERT_PEM" "$CERT_CRT"
    fi
    
    echo "[2/3] Installing CA certificate to system..."
    
    if [ -f /etc/ca-certificates.conf ]; then
        if [ ! -f /usr/local/share/ca-certificates/mitmproxy.crt ]; then
            sudo cp "$CERT_CRT" /usr/local/share/ca-certificates/mitmproxy.crt
            sudo update-ca-certificates
            echo "Certificate installed to system"
        else
            echo "Certificate already installed"
        fi
    fi
    
    if command -v trust &> /dev/null; then
        sudo trust anchor --store "$CERT_PEM" 2>/dev/null || true
    fi
    
    if [ -d "$HOME/.local/share/ca-certificates" ]; then
        if [ ! -f "$HOME/.local/share/ca-certificates/mitmproxy.crt" ]; then
            cp "$CERT_CRT" "$HOME/.local/share/ca-certificates/"
            sudo update-ca-certificates 2>/dev/null || true
        fi
    fi
    
    echo "[3/3] Certificate setup complete"
}

start_proxy() {
    echo "Starting mitmproxy on port $MITM_PROXY_PORT..."
    
    cd "$SCRIPT_DIR"
    
    mitmdump --mode regular@$MITM_PROXY_PORT \
              --ssl-insecure \
              -s addons.py \
              --set block_global=false \
              --set confdir=$CERT_DIR \
              "$@"
}

if [ "$1" = "--install-cert" ]; then
    install_cert
elif [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --install-cert    Install CA certificate to system"
    echo "  --help, -h        Show this help message"
    echo ""
    echo "Environment variables for agents:"
    echo "  HTTP_PROXY=http://localhost:$MITM_PROXY_PORT"
    echo "  HTTPS_PROXY=http://localhost:$MITM_PROXY_PORT"
    echo "  X-Pane-Id: <pane_id> (custom header)"
else
    install_cert
    start_proxy
fi
