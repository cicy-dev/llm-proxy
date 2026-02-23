import json
import os
import mysql.connector
import requests as _requests
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
        self._tg_cache = {}  # pane_id -> {tg_token, tg_chat_id}

    def _get_tg_config(self, pane_id: str) -> dict:
        if pane_id in self._tg_cache:
            return self._tg_cache[pane_id]
        try:
            conn = mysql.connector.connect(
                host=self.db_config["host"],
                user=self.db_config["user"],
                password=self.db_config["password"],
                database="tts_bot",
            )
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT tg_token, tg_chat_id FROM ttyd_config WHERE pane_id=%s AND tg_enable=1",
                (pane_id,)
            )
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if row and row.get("tg_token") and row.get("tg_chat_id"):
                self._tg_cache[pane_id] = row
                return row
        except Exception as e:
            ctx.log.error(f"[TG CONFIG ERROR] {e}")
        return {}

    def _notify_tg(self, pane_id: str, text: str):
        cfg = self._get_tg_config(pane_id)
        if not cfg:
            return
        url = f"https://api.telegram.org/bot{cfg['tg_token']}/sendMessage"
        if len(text) > 4000:
            text = text[-4000:]
        try:
            _requests.post(url, json={"chat_id": cfg["tg_chat_id"], "text": text}, timeout=10)
        except Exception as e:
            ctx.log.error(f"[TG SEND ERROR] {e}")

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

        pane_id = flow.request.headers.get("X-Pane-Id", "")
        if pane_id:
            _executor.submit(self._notify_tg_from_flow, pane_id, flow)

    def _extract_llm_answer(self, body: str) -> str:
        """Extract text content from LLM API response."""
        try:
            data = json.loads(body)
            # OpenAI format
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                return msg.get("content", "")
            # Anthropic format
            content = data.get("content", [])
            if content and isinstance(content, list):
                parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(parts)
        except Exception:
            pass
        return ""

    def _notify_tg_from_flow(self, pane_id: str, flow: http.HTTPFlow):
        try:
            res_body = flow.response.content.decode("utf-8", errors="ignore") if flow.response.content else ""
            answer = self._extract_llm_answer(res_body)
            if answer:
                self._notify_tg(pane_id, f"🤖 {pane_id}\n{answer}")
        except Exception as e:
            ctx.log.error(f"[TG NOTIFY ERROR] {e}")

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
