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

    def _notify_tg(self, pane_id: str, text: str, record_id: int = None):
        cfg = self._get_tg_config(pane_id)
        if not cfg:
            return
        url = f"https://api.telegram.org/bot{cfg['tg_token']}/sendMessage"
        payload = {"chat_id": cfg["tg_chat_id"], "text": text}
        if record_id:
            token = self._load_api_token()
            detail_url = f"http://localhost:14444/api/qa/{record_id}?token={token}"
            payload["reply_markup"] = json.dumps({
                "inline_keyboard": [[{"text": "📄 Detail", "url": detail_url}]]
            })
        try:
            _requests.post(url, json=payload, timeout=10)
        except Exception as e:
            ctx.log.error(f"[TG SEND ERROR] {e}")

    def _load_api_token(self) -> str:
        try:
            with open("/home/w3c_offical/global.json") as f:
                return json.load(f).get("api_token", "")
        except Exception:
            return ""

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

        _executor.submit(self._save_qa_and_notify, pane_id, flow) if pane_id else _executor.submit(self._save_to_db, flow)

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

    def _extract_llm_question(self, body: str) -> str:
        """Extract user message from LLM API request."""
        try:
            data = json.loads(body)
            messages = data.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        return " ".join(c.get("text", "") for c in content if c.get("type") == "text")
                    return content
        except Exception:
            pass
        return ""

    def _extract_model(self, req_body: str, res_body: str) -> str:
        for body in [res_body, req_body]:
            try:
                return json.loads(body).get("model", "")
            except Exception:
                pass
        return ""

    def _extract_token_usage(self, body: str) -> str:
        try:
            usage = json.loads(body).get("usage")
            if usage:
                return json.dumps(usage)
        except Exception:
            pass
        return ""

    def _save_qa_and_notify(self, pane_id: str, flow: http.HTTPFlow):
        try:
            req_body = flow.request.content.decode("utf-8", errors="ignore") if flow.request.content else ""
            res_body = flow.response.content.decode("utf-8", errors="ignore") if flow.response.content else ""
            question = self._extract_llm_question(req_body)
            answer = self._extract_llm_answer(res_body)
            model = self._extract_model(req_body, res_body)
            token_usage = self._extract_token_usage(res_body)

            if not answer:
                return

            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO llm_qa_history (pane_id, model, question, answer, token_usage) VALUES (%s,%s,%s,%s,%s)",
                (pane_id, model, question[:65535], answer[:65535], token_usage)
            )
            record_id = cursor.lastrowid
            conn.commit()
            cursor.close()
            conn.close()

            preview = answer[:50] + ("..." if len(answer) > 50 else "")
            msg = f"🤖 #{record_id} | {model}\n{preview}"
            self._notify_tg(pane_id, msg, record_id if len(answer) > 50 else None)
        except Exception as e:
            ctx.log.error(f"[QA SAVE ERROR] {e}")

    def _notify_tg_from_flow(self, pane_id: str, flow: http.HTTPFlow):
        self._save_qa_and_notify(pane_id, flow)

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
