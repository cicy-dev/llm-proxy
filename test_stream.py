#!/usr/bin/env python3
import os
import sys
import json

os.environ['HTTP_PROXY'] = 'http://127.0.0.1:8083'  ````````````
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:8083'
os.environ['X-Pane-Id'] = 'test_pane_001'

import requests

url = "https://api.openai.com/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"
}
data = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Say hello in 3 words"}],
    "stream": True
}

try:
    resp = requests.post(url, headers=headers, json=data, stream=True, timeout=10, proxies={'http': 'http://127.0.0.1:8083', 'https': 'http://127.0.0.1:8083'})
    for line in resp.iter_lines():
        if line:
            print(line.decode())
except Exception as e:
    print(f"Error: {e}")
