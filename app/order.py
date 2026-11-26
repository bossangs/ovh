"""下单流程：建购物车 -> 绑定账号 -> 加 Eco/KS 商品 -> 填机房/系统 -> 结算。

OVH 的顺序（公开文档 /order/cart）：
  POST /order/cart                              创建购物车
  POST /order/cart/{cartId}/assign              绑定到当前账号
  POST /order/cart/{cartId}/eco                 添加独立服务器（planCode/duration/pricingMode）
  GET  /order/cart/{cartId}/item/{itemId}/requiredConfiguration  该 item 的必填配置
  POST /order/cart/{cartId}/item/{itemId}/configuration   选机房、系统（每次只收一条 label/value）
  GET  /order/cart/{cartId}/checkout            取合同与金额
  POST /order/cart/{cartId}/checkout            生成订单（可选自动扣款）
"""

from __future__ import annotations

from typing import Any

from .logs import log
from .ovh_client import OvhApiError, OvhClient

# 机房 -> eco 目录里的 region 取值
_APAC = {"sgp", "syd", "ynm"}


def resolve_region(plan: dict[str, Any], datacenter: str) -> str | None:
    regions = (plan.get("order_config") or {}).get("regions") or []
    if not regions:
        return None
    if datacenter in _APAC and "apac" in regions:
        return "apac"
    if datacenter == "bhs" and "canada" in regions:
        return "canada"
    if datacenter != "bhs" and "europe" in regions:
        return "europe"
    return regions[0]


def _duration(months: int) -> str:
    return f"P{int(months)}M"


async def _required_labels(client: OvhClient, cart_id: str, item_id: Any) -> list[str]:
    """该 item 的必填配置 label。接口拿不到时返回空表，不阻塞下单。"""
    try:
        data = await client.get(
            f"/order/cart/{cart_id}/item/{item_id}/requiredConfiguration"
        )
    except OvhApiError:
        return []
    labels: list[str] = []
    for cfg in data or []:
        if not isinstance(cfg, dict) or not cfg.get("label"):
            continue
        if cfg.get("required") or cfg.get("isMandatory"):
            labels.append(str(cfg["label"]))
    return labels


async def _apply_configuration(
    client: OvhClient,
    cart_id: str,
    item_id: Any,
    configuration: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    """逐条提交配置，返回（已生效, 失败说明）。

    POST /order/cart/{cartId}/item/{itemId}/configuration 的 body 只接受单个
    {"label": ..., "value": ...} 对象，一次传数组会直接 400 Bad Request。
    某一条被拒（例如机型不支持 region）时记下来继续下一条，交由结算阶段判断。
    """
    applied: list[dict[str, str]] = []
    failures: list[str] = []
    for cfg in configuration:
        label = str(cfg.get("label") or "")
        value = str(cfg.get("value") or "")
        if not label:
            continue
        try:
            await client.post(
                f"/order/cart/{cart_id}/item/{item_id}/configuration",
                {"label": label, "value": value},
            )
        except OvhApiError as exc:
            failures.append(f"{label}={value}（{exc.message}）")
            continue
        applied.append({"label": label, "value": value})
    return applied, failures


def _config_hint(
    exc: OvhApiError, missing: list[str], failures: list[str]
) -> OvhApiError:
    """结算接口缺配置时只回一句 400，这里补上到底缺哪一项。"""
    hints: list[str] = []
    if missing:
        hints.append("缺少必填配置：" + "、".join(missing))
    if failures:
        hints.append("未生效的配置：" + "；".join(failures))
    if not hints:
        return exc
    return OvhApiError(
        exc.status,
        f"{exc.message}（{'；'.join(hints)}），请检查所选机房与系统是否被该机型支持",
        exc.api_class,
        exc.path,
    )


async def fetch_orders(client: OvhClient, limit: int = 15) -> list[dict[str, Any]]:
    """最近订单。先拿 id 列表，再逐个补详情。"""
    try:
        ids = await client.get("/me/order", params={"sort": "DESC"})
    except OvhApiError:
        # 部分区域不支持 sort 参数，退回默认顺序
        ids = await client.get("/me/order")
    if not isinstance(ids, list):
        return []
    result: list[dict[str, Any]] = []
    for order_id in ids[:limit]:
        try:
            detail = await client.get(f"/me/order/{order_id}")
        except OvhApiError:
            detail = {"orderId": order_id}
        result.append(detail)
    return result


async def order_status(client: OvhClient, order_id: int) -> str:
    try:
        return str(await client.get(f"/me/order/{order_id}/status"))
    except OvhApiError as exc:
        return f"查询失败：{exc.message}"


async def create_order(
    client: OvhClient,
    plan: dict[str, Any],
    datacenter: str,
    subsidiary: str,
    months: int = 1,
    os_template: str | None = None,
    auto_pay: bool = False,
    preview_only: bool = False,
) -> dict[str, Any]:
    """走完购物车流程。preview_only 只到结算信息，不真正下单。"""
    plan_code = plan["planCode"]
    cart_id: str | None = None
    steps: list[str] = []
    applied: list[dict[str, str]] = []
    failures: list[str] = []
    missing: list[str] = []

    try:
        cart = await client.post(
            "/order/cart",
            {"ovhSubsidiary": subsidiary, "description": f"ovh-panel {plan_code}"},
        )
        cart_id = str((cart or {}).get("cartId") or "") if isinstance(cart, dict) else ""
        if not cart_id:
            raise OvhApiError(500, f"创建购物车返回异常：{cart}", "CartError")
        steps.append(f"已创建购物车 {cart_id}")

        await client.post(f"/order/cart/{cart_id}/assign")
        steps.append("购物车已绑定当前账号")

        try:
            item = await client.post(
                f"/order/cart/{cart_id}/eco",
                {
                    "planCode": plan_code,
                    "duration": _duration(months),
                    "pricingMode": "default",
                    "quantity": 1,
                },
            )
        except OvhApiError as exc:
            message = exc.message
            low = message.lower()
            if "pricing" in low or "price" in low or "duration" in low:
                message = f"{message}（该机型在 {months} 个月租期下可能没有 default 定价，请换租期再试）"
            raise OvhApiError(exc.status, message, exc.api_class, exc.path) from exc
        item_id = item.get("itemId") if isinstance(item, dict) else None
        if item_id is None:
            raise OvhApiError(500, f"添加商品返回异常：{item}", "CartItemError")
        steps.append(f"已加入商品 {plan_code}（item {item_id}）")

        order_config = plan.get("order_config") or {}
        configuration: list[dict[str, str]] = []
        if datacenter:
            configuration.append({"label": "dedicated_datacenter", "value": datacenter})
        region = resolve_region(plan, datacenter)
        if region:
            configuration.append({"label": "region", "value": region})
        os_choices = order_config.get("os") or []
        chosen_os = os_template or ("none_64.en" if "none_64.en" in os_choices else (os_choices[0] if os_choices else ""))
        if chosen_os:
            configuration.append({"label": "dedicated_os", "value": chosen_os})

        required = await _required_labels(client, cart_id, item_id)
        applied, failures = await _apply_configuration(
            client, cart_id, item_id, configuration
        )
        if applied:
            steps.append(
                "已设置 " + "、".join(f"{c['label']}={c['value']}" for c in applied)
            )
        if failures:
            steps.append("部分配置未生效：" + "；".join(failures))
        applied_labels = {c["label"] for c in applied}
        missing = [label for label in required if label not in applied_labels]
        if missing:
            steps.append("仍缺必填配置：" + "、".join(missing))

        cart_full = await client.get(f"/order/cart/{cart_id}")
        try:
            checkout = await client.get(f"/order/cart/{cart_id}/checkout")
        except OvhApiError as exc:
            raise _config_hint(exc, missing, failures) from exc
        steps.append("已获取结算信息")

        result: dict[str, Any] = {
            "ok": True,
            "preview_only": preview_only,
            "cart_id": cart_id,
            "item_id": item_id,
            "plan_code": plan_code,
            "datacenter": datacenter,
            "months": months,
            "os": chosen_os,
            "configuration": applied,
            "missing_config": missing,
            "steps": steps,
            "prices": (checkout or {}).get("prices") or (cart_full or {}).get("prices"),
            "contracts": len((checkout or {}).get("contracts") or []),
        }

        if preview_only:
            log(f"下单预览完成：{plan_code} @ {datacenter}，购物车 {cart_id}", "info", plan=plan_code)
            return result

        try:
            order = await client.post(
                f"/order/cart/{cart_id}/checkout",
                {
                    "autoPayWithPreferredPaymentMethod": bool(auto_pay),
                    "waiveRetractationPeriod": False,
                },
            )
        except OvhApiError as exc:
            raise _config_hint(exc, missing, failures) from exc
        order_id = (order or {}).get("orderId")
        result.update(
            {
                "order_id": order_id,
                "order_url": (order or {}).get("url", ""),
                "auto_pay": bool(auto_pay),
                "prices": (order or {}).get("prices") or result.get("prices"),
            }
        )
        if order_id:
            result["status"] = await order_status(client, order_id)
        log(
            f"已下单 {plan_code} @ {datacenter}，订单号 {order_id}，"
            f"{'已自动支付' if auto_pay else '待支付'}",
            "success",
            plan=plan_code,
            order_id=order_id,
        )
        return result

    except OvhApiError as exc:
        steps.append(f"失败：{exc.message}")
        log(f"下单失败 {plan_code} @ {datacenter}：{exc.message}", "error", plan=plan_code)
        # 失败时尽量清掉购物车，避免留下空车
        if cart_id:
            try:
                await client.delete(f"/order/cart/{cart_id}")
                steps.append(f"已删除购物车 {cart_id}")
            except OvhApiError:
                pass
        return {
            "ok": False,
            "error": exc.message,
            "status": exc.status,
            "class": exc.api_class,
            "configuration": applied,
            "missing_config": missing,
            "steps": steps,
            "cart_id": cart_id,
        }
