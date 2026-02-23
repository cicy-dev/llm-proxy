import json
import os
import mysql.connector
from datetime import datetime
from mitmproxy import http, ctx
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

TARGET_DOMAINS = [
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.google.com",
    "api.mistral.ai",
    "opencode.ai",
]

_executor = ThreadPoolExecutor(max_workers=10)


class LLMTracker:
    def __init__(self):
        self.db_config = {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "llm_qa_history"),
        }

    def _is_target(self, flow: http.HTTPFlow) -> bool:
        host = flow.request.pretty_host
        return any(domain in host for domain in TARGET_DOMAINS)

    def request(self, flow: http.HTTPFlow):
        if self._is_target(flow):
            url = flow.request.url
            method = flow.request.method
            ctx.log.info(f"[🔍 发现 LLM 请求] Method: {method} | URL: {url}")
            flow.metadata["start_time"] = datetime.now()

    def response(self, flow: http.HTTPFlow):
        if not self._is_target(flow):
            return

        status_code = flow.response.status_code if flow.response else 0
        url = flow.request.url
        
        ctx.log.info(f"[✨ 收到响应] Status: {status_code} | URL: {url}")

        if status_code == 403:
            ctx.log.error(f"[❌ 地区封锁] 访问 {url} 被 Cloudflare 拦截 (403)。请检查服务器 IP 或挂载上游代理。")

        _executor.submit(self._save_to_db, flow)

    def _save_to_db(self, flow: http.HTTPFlow):
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()

            req_body = flow.request.content.decode("utf-8", errors="ignore") if flow.request.content else ""
            res_body = flow.response.content.decode("utf-8", errors="ignore") if flow.response.content else ""
            
            query = """
                INSERT INTO llm_qa_history 
                (url, method, status_code, request_body, response_body, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            
            cursor.execute(query, (
                flow.request.url,
                flow.request.method,
                flow.response.status_code if flow.response else 0,
                req_body[:65535],
                res_body[:65535],
                flow.metadata.get("start_time", datetime.now())
            ))

            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            ctx.log.error(f"[DB ERROR] {e}")


addons = [LLMTracker()]
