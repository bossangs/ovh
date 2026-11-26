"""OVHcloud API 客户端：自己实现 X-Ovh-* 签名，方便 async 调用与错误透传。

签名规则（OVH 官方）：
    $1$ + SHA1(AS + "+" + CK + "+" + METHOD + "+" + URL + "+" + BODY + "+" + TIMESTAMP)
其中 URL 是含查询串的完整地址，BODY 是紧凑 JSON（无空格），TIMESTAMP 为秒级时间戳。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import ENDPOINTS


class OvhApiError(Exception):
    """OVH 返回的错误，保留原始 class/message 方便前端显示。"""

    def __init__(self, status: int, message: str, api_class: str = "", path: str = "") -> None:
        self.status = status
        self.message = message
        self.api_class = api_class
        self.path = path
        super().__init__(f"[{status}] {api_class or 'error'}: {message}")


class OvhClient:
    def __init__(
        self,
        endpoint: str = "ovh-ca",
        application_key: str = "",
        application_secret: str = "",
        consumer_key: str = "",
        timeout: float = 60.0,
    ) -> None:
        info = ENDPOINTS.get(endpoint, ENDPOINTS["ovh-ca"])
        self.endpoint_key = endpoint if endpoint in ENDPOINTS else "ovh-ca"
        self.api = info["api"]
        self.ak = application_key.strip()
        self.as_ = application_secret.strip()
        self.ck = consumer_key.strip()
        self.timeout = timeout
        self._delta: float | None = None
        self._delta_at: float = 0.0

    # ------------------------------------------------------------ 基础工具
    @property
    def authenticated(self) -> bool:
        return bool(self.ak and self.as_ and self.ck)

    def _headers(self, method: str, url: str, body: str, ts: int) -> dict[str, str]:
        raw = "+".join([self.as_, self.ck, method, url, body, str(ts)])
        signature = "$1$" + hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return {
            "X-Ovh-Application": self.ak,
            "X-Ovh-Consumer": self.ck,
            "X-Ovh-Timestamp": str(ts),
            "X-Ovh-Signature": signature,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _now(self, client: httpx.AsyncClient) -> int:
        """使用服务器时间，避免本机时钟漂移导致签名失效。"""
        now = time.time()
        if self._delta is None or now - self._delta_at > 1800:
            try:
                resp = await client.get(f"{self.api}/auth/time")
                if resp.status_code == 200:
                    self._delta = float(resp.text.strip()) - now
                    self._delta_at = now
            except httpx.HTTPError:
                self._delta = 0.0
                self._delta_at = now
        return int(time.time() + (self._delta or 0))

    @staticmethod
    def _build_url(base: str, path: str, params: dict[str, Any] | None) -> str:
        url = f"{base}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean, doseq=True)}"
        return url

    async def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        method = method.upper()
        url = self._build_url(self.api, path, params)
        payload = (
            json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            if body is not None
            else ""
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in (0, 1):
                headers = {"Accept": "application/json", "Content-Type": "application/json"}
                if auth:
                    if not self.authenticated:
                        raise OvhApiError(
                            401,
                            "尚未填写 Application Key / Secret / Consumer Key，无法调用需要登录的接口",
                            "NotConfigured",
                            path,
                        )
                    ts = await self._now(client)
                    headers = self._headers(method, url, payload, ts)
                try:
                    resp = await client.request(
                        method, url, content=payload or None, headers=headers
                    )
                except httpx.HTTPError as exc:
                    raise OvhApiError(0, f"网络错误：{exc}", "NetworkError", path) from exc

                # 时钟偏差：刷新一次服务器时间后重试
                if resp.status_code == 403 and auth and attempt == 0:
                    self._delta = None
                    continue
                return self._parse(resp, path)

    @staticmethod
    def _parse(resp: httpx.Response, path: str) -> Any:
        if resp.status_code >= 400:
            message, api_class = resp.text, ""
            try:
                data = resp.json()
                if isinstance(data, dict):
                    message = str(data.get("message") or message)
                    api_class = str(data.get("class") or "")
            except ValueError:
                pass
            raise OvhApiError(resp.status_code, message, api_class, path)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ------------------------------------------------------------ 语法糖
    async def get(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> Any:
        return await self.call("GET", path, params=params, auth=auth)

    async def post(self, path: str, body: Any = None, auth: bool = True) -> Any:
        return await self.call("POST", path, body={} if body is None else body, auth=auth)

    async def put(self, path: str, body: Any = None, auth: bool = True) -> Any:
        return await self.call("PUT", path, body={} if body is None else body, auth=auth)

    async def delete(self, path: str, auth: bool = True) -> Any:
        return await self.call("DELETE", path, auth=auth)

    # ------------------------------------------------------------ 公开接口
    async def public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.call("GET", path, params=params, auth=False)


def client_from_store(store: Any, **kwargs: Any) -> OvhClient:
    return OvhClient(
        endpoint=store.endpoint,
        application_key=str(store.get("application_key") or ""),
        application_secret=str(store.get("application_secret") or ""),
        consumer_key=str(store.get("consumer_key") or ""),
        **kwargs,
    )


def public_client(endpoint: str) -> OvhClient:
    """库存/目录查询不需要密钥。"""
    return OvhClient(endpoint=endpoint)
