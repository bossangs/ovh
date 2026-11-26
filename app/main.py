"""OVH 中文面板：FastAPI 后端。"""

from __future__ import annotations

import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .catalog import find_plan, load_market, plan_stock
from .config import ENDPOINTS, SUBSIDIARIES, Store, mask
from .logs import log, recent
from .machines import (
    install_templates,
    list_servers,
    list_boots,
    partition_schemes,
    reboot,
    server_detail,
    set_boot,
    set_rescue,
    start_install,
    tasks,
)
from .monitor import Monitor
from .notify import send_telegram
from .order import create_order, fetch_orders, order_status
from .ovh_client import OvhApiError, client_from_store

APP_VERSION = "1.0.0"
STATIC_DIR = Path(__file__).parent / "static"
COOKIE_NAME = "ovh_panel_session"

store = Store()
monitor = Monitor(store)


def session_token(password: str) -> str:
    return hashlib.sha256(f"{password}|ovh-panel-v1".encode()).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log(f"面板启动，区域 {store.endpoint} / {store.subsidiary}", "info")
    monitor.start()
    try:
        yield
    finally:
        await monitor.stop()


app = FastAPI(title="OVH 中文面板", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------- 鉴权
def check_auth(token: str | None) -> bool:
    password = store.password
    if not password:
        return True
    return bool(token) and hmac.compare_digest(token, session_token(password))


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(("/api/login", "/api/health", "/api/meta")):
        if not check_auth(request.cookies.get(COOKIE_NAME)):
            return JSONResponse({"error": "unauthorized", "message": "需要先登录面板"}, status_code=401)
    return await call_next(request)


@app.exception_handler(OvhApiError)
async def ovh_error_handler(request: Request, exc: OvhApiError):
    return JSONResponse(
        {
            "error": "ovh_api_error",
            "status": exc.status,
            "class": exc.api_class,
            "message": exc.message,
        },
        status_code=502 if exc.status >= 500 or exc.status == 0 else 400,
    )


# ---------------------------------------------------------------- 页面
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "app": "ovh-panel", "version": APP_VERSION}


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "endpoints": [{"code": k, "label": v["label"]} for k, v in ENDPOINTS.items()],
        "subsidiaries": SUBSIDIARIES,
        "current": {
            "endpoint": store.endpoint,
            "subsidiary": store.subsidiary,
            "has_credentials": store.has_credentials,
            "password_set": bool(store.password),
            "authenticated": True,
        },
    }


@app.post("/api/login")
async def login(response: Response, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    password = store.password
    if not password:
        return {"ok": True, "message": "面板未设置密码"}
    if not hmac.compare_digest(str(payload.get("password", "")), password):
        raise HTTPException(status_code=401, detail="密码不正确")
    token = session_token(password)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    log("面板登录成功", "info")
    return {"ok": True}


@app.post("/api/logout")
async def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ---------------------------------------------------------------- 设置
def settings_view() -> dict[str, Any]:
    data = store.data
    return {
        "endpoint": store.endpoint,
        "subsidiary": store.subsidiary,
        "application_key": mask(str(data.get("application_key") or "")),
        "application_key_set": bool(data.get("application_key")),
        "application_secret_set": bool(data.get("application_secret")),
        "consumer_key_set": bool(data.get("consumer_key")),
        "panel_password_set": bool(data.get("panel_password")),
        "telegram_bot_token": mask(str(data.get("telegram_bot_token") or "")),
        "telegram_token_set": bool(data.get("telegram_bot_token")),
        "telegram_chat_id": str(data.get("telegram_chat_id") or ""),
        "default_interval": int(data.get("default_interval") or 300),
        "has_credentials": store.has_credentials,
    }


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return settings_view()


@app.put("/api/settings")
async def put_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key in ("endpoint", "subsidiary"):
        if payload.get(key):
            updates[key] = str(payload[key]).strip()
    if updates.get("endpoint") and updates["endpoint"] not in ENDPOINTS:
        raise HTTPException(status_code=400, detail="未知的区域接口")
    if updates.get("endpoint") and not payload.get("subsidiary"):
        # 换区域但没给子区域时，落到该区域默认值，避免用错结算站点
        updates["subsidiary"] = ENDPOINTS[updates["endpoint"]]["subsidiary"]
    for key in ("application_key", "application_secret", "consumer_key", "telegram_bot_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            updates[key] = value.strip()
        if payload.get(f"clear_{key}"):
            updates[key] = ""
    if isinstance(payload.get("telegram_chat_id"), str):
        updates["telegram_chat_id"] = payload["telegram_chat_id"].strip()
    if isinstance(payload.get("panel_password"), str) and payload["panel_password"]:
        updates["panel_password"] = payload["panel_password"]
    if payload.get("clear_panel_password"):
        updates["panel_password"] = ""
    if payload.get("default_interval"):
        try:
            updates["default_interval"] = max(30, int(payload["default_interval"]))
        except (TypeError, ValueError):
            pass
    store.update(**updates)
    log("设置已更新" + (f"（{', '.join(updates)}）" if updates else "（无变化）"), "info")
    return settings_view()


@app.post("/api/telegram/test")
async def telegram_test() -> dict[str, Any]:
    token = str(store.get("telegram_bot_token") or "")
    chat = str(store.get("telegram_chat_id") or "")
    if not token or not chat:
        raise HTTPException(status_code=400, detail="请先保存 Bot Token 与 Chat ID")
    result = await send_telegram(
        token,
        chat,
        "✅ <b>OVH 中文面板</b>\nTelegram 通知测试成功，有货时会推送到这里。",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "发送失败")
    return {"ok": True, "message": "测试消息已发送，请查看 Telegram"}


@app.get("/api/account")
async def account() -> dict[str, Any]:
    if not store.has_credentials:
        return {"ok": False, "message": "尚未填写 API 密钥"}
    client = client_from_store(store)
    me = await client.get("/me")
    return {"ok": True, "me": me}


# ---------------------------------------------------------------- 库存
@app.get("/api/catalog")
async def catalog(
    refresh: bool = False,
    q: str = "",
    max_price: float | None = None,
    ks_only: bool = False,
    in_stock: bool = False,
    tier: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    market = await load_market(store.endpoint, store.subsidiary, force=refresh)
    plans = market["plans"]
    keyword = q.strip().lower()
    filtered: list[dict[str, Any]] = []
    for plan in plans:
        if ks_only and not plan["ks"]:
            continue
        if tier and plan["tier"] != tier:
            continue
        if max_price is not None and (plan["price"]["monthly"] or 0) > max_price:
            continue
        if in_stock and not plan["has_stock"]:
            continue
        if keyword:
            haystack = f"{plan['planCode']} {plan['name']} {plan['invoiceName']} {plan['cpu']}".lower()
            if keyword not in haystack:
                continue
        filtered.append(plan)
    return {
        "updated": market["updated"],
        "currency": market["currency"],
        "total": len(plans),
        "shown": len(filtered[:limit]),
        "plans": filtered[:limit],
    }


@app.get("/api/plans/{plan_code}/stock")
async def one_plan_stock(plan_code: str) -> dict[str, Any]:
    stock = await plan_stock(store.endpoint, plan_code)
    return {"planCode": plan_code, "stock": stock}


# ---------------------------------------------------------------- 监控规则
@app.get("/api/rules")
async def list_rules() -> dict[str, Any]:
    status = {s["id"]: s for s in monitor.status()["rules"]}
    rules = []
    for rule in store.rules():
        item = dict(rule)
        item["runtime"] = status.get(rule.get("id"), {})
        rules.append(item)
    return {"rules": rules, "monitor_running": monitor.running}


@app.post("/api/rules")
async def add_rule(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    plan_code = str(payload.get("planCode") or "").strip()
    if not plan_code:
        raise HTTPException(status_code=400, detail="请填写或选择机型代码")
    rule = {
        "id": store.new_rule_id(),
        "name": str(payload.get("name") or plan_code).strip(),
        "planCode": plan_code,
        "datacenters": [str(d) for d in (payload.get("datacenters") or []) if d],
        "min_state": payload.get("min_state") if payload.get("min_state") in {"1H-high", "1H-low", "72H"} else "1H-low",
        "interval": max(30, int(payload.get("interval") or store.get("default_interval") or 300)),
        "auto_order": bool(payload.get("auto_order")),
        "auto_pay": bool(payload.get("auto_pay")),
        "months": int(payload.get("months") or 1),
        "os": str(payload.get("os") or ""),
        "enabled": bool(payload.get("enabled", True)),
        "created": int(time.time()),
    }
    rules = store.rules() + [rule]
    store.save_rules(rules)
    log(f"新增加监控规则：{rule['name']}（{plan_code}）", "info")
    return {"ok": True, "rule": rule}


@app.put("/api/rules/{rule_id}")
async def update_rule(rule_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    rules = store.rules()
    for rule in rules:
        if rule.get("id") == rule_id:
            for key in ("name", "planCode", "min_state", "os"):
                if payload.get(key) is not None:
                    rule[key] = payload[key]
            if payload.get("datacenters") is not None:
                rule["datacenters"] = [str(d) for d in payload["datacenters"] if d]
            if payload.get("interval") is not None:
                rule["interval"] = max(30, int(payload["interval"]))
            for key in ("auto_order", "auto_pay", "enabled"):
                if payload.get(key) is not None:
                    rule[key] = bool(payload[key])
            if payload.get("months") is not None:
                rule["months"] = int(payload["months"])
            store.save_rules(rules)
            return {"ok": True, "rule": rule}
    raise HTTPException(status_code=404, detail="规则不存在")


@app.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: str) -> dict[str, Any]:
    rules = [r for r in store.rules() if r.get("id") != rule_id]
    store.save_rules(rules)
    return {"ok": True}


@app.get("/api/monitor")
async def monitor_status() -> dict[str, Any]:
    return monitor.status()


# ---------------------------------------------------------------- 下单
@app.post("/api/orders")
async def place_order(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    plan_code = str(payload.get("planCode") or "").strip()
    datacenter = str(payload.get("datacenter") or "").strip()
    if not plan_code:
        raise HTTPException(status_code=400, detail="缺少机型代码")
    if not datacenter:
        raise HTTPException(status_code=400, detail="请选择机房")
    preview = bool(payload.get("preview"))
    if not preview and not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="需要确认后才会真正下单")
    if not store.has_credentials:
        raise HTTPException(status_code=400, detail="请先在「系统设置」里填写 API 三件套")

    market = await load_market(store.endpoint, store.subsidiary)
    plan = find_plan(market, plan_code)
    if not plan:
        raise HTTPException(status_code=404, detail=f"目录里没有机型 {plan_code}")
    if datacenter not in (plan.get("order_config") or {}).get("datacenters", []):
        raise HTTPException(status_code=400, detail=f"{plan_code} 不能在机房 {datacenter} 下单")

    client = client_from_store(store)
    return await create_order(
        client,
        plan,
        datacenter,
        subsidiary=store.subsidiary,
        months=int(payload.get("months") or 1),
        os_template=payload.get("os") or None,
        auto_pay=bool(payload.get("auto_pay")),
        preview_only=preview,
    )


@app.get("/api/orders")
async def orders(limit: int = 15) -> dict[str, Any]:
    if not store.has_credentials:
        return {"orders": [], "message": "未配置 API 密钥"}
    client = client_from_store(store)
    items = await fetch_orders(client, limit=limit)
    return {"orders": items}


@app.get("/api/orders/{order_id}/status")
async def order_status_api(order_id: int) -> dict[str, Any]:
    client = client_from_store(store)
    return {"orderId": order_id, "status": await order_status(client, order_id)}


# ---------------------------------------------------------------- 机器管理
def require_credentials() -> None:
    if not store.has_credentials:
        raise HTTPException(status_code=400, detail="请先在「系统设置」里填写 API 三件套")


@app.get("/api/servers")
async def servers() -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return {"servers": await list_servers(client)}


@app.get("/api/servers/{name}")
async def server(name: str) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return await server_detail(client, name)


@app.post("/api/servers/{name}/reboot")
async def server_reboot(name: str) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return await reboot(client, name)


@app.get("/api/servers/{name}/boots")
async def server_boots(name: str) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return {"boots": await list_boots(client, name)}


@app.post("/api/servers/{name}/boot")
async def server_boot(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    boot_id = payload.get("bootId")
    if boot_id is None:
        raise HTTPException(status_code=400, detail="缺少 bootId")
    return await set_boot(client, name, int(boot_id), reboot=bool(payload.get("reboot", True)))


@app.post("/api/servers/{name}/rescue")
async def server_rescue(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return await set_rescue(
        client, name, bool(payload.get("enable", True)), reboot=bool(payload.get("reboot", True))
    )


@app.get("/api/servers/{name}/templates")
async def server_templates(name: str) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return {"templates": await install_templates(client, name)}


@app.get("/api/servers/{name}/partitions")
async def server_partitions(name: str, template: str = Query(...)) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return {"schemes": await partition_schemes(client, name, template)}


@app.post("/api/servers/{name}/install")
async def server_install(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_credentials()
    template = str(payload.get("template") or "").strip()
    scheme = str(payload.get("scheme") or "").strip()
    if not template or not scheme:
        raise HTTPException(status_code=400, detail="需要选择系统模板与分区方案")
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="重装会清空磁盘，需要确认")
    client = client_from_store(store)
    return await start_install(client, name, template, scheme, payload.get("hostname") or None)


@app.get("/api/servers/{name}/tasks")
async def server_tasks(name: str) -> dict[str, Any]:
    require_credentials()
    client = client_from_store(store)
    return {"tasks": await tasks(client, name)}


# ---------------------------------------------------------------- 日志
@app.get("/api/logs")
async def logs(limit: int = 200) -> dict[str, Any]:
    return {"logs": recent(limit)}
