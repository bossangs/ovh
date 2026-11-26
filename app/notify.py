"""Telegram 通知。"""

from __future__ import annotations

from typing import Any

import httpx

from .logs import log


async def send_telegram(token: str, chat_id: str, text: str, timeout: float = 15.0) -> dict[str, Any]:
    if not token or not chat_id:
        return {"ok": False, "error": "未配置 Telegram Bot Token 或 Chat ID"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        data: dict[str, Any]
        try:
            data = resp.json()
        except ValueError:
            data = {"ok": False, "error": resp.text[:200]}
        if resp.status_code >= 400 or not data.get("ok"):
            description = data.get("description") or f"HTTP {resp.status_code}"
            log(f"Telegram 推送失败：{description}", "error")
            return {"ok": False, "error": description}
        return {"ok": True}
    except httpx.HTTPError as exc:
        log(f"Telegram 推送网络错误：{exc}", "error")
        return {"ok": False, "error": f"网络错误：{exc}"}
