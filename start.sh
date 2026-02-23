#!/bin/bash

cd "$(dirname "$0")"

echo "Starting LLM Proxy on port 18080..."

mitmdump \
    --mode regular@18080 \
    --ssl-insecure \
    -s addons.py \
    --set block_global=false \
    --set confdir="$HOME/.mitmproxy" \
    2>&1
