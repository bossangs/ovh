"""后台库存监控：按规则轮询公开库存接口，有货推 Telegram，可选自动下单。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .catalog import find_plan, load_market, plan_stock
from .config import Store
from .logs import log
from .models_meta import STATE_LABEL, STATE_RANK, datacenter_name
from .notify import send_telegram
from .order import create_order
from .ovh_client import OvhApiError, client_from_store

HIT_COOLDOWN = 1800  # 同一规则同一状态，30 分钟内不重复推送
TICK = 5.0


class Monitor:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, dict[str, Any]] = {}
        self._busy: set[str] = set()

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name="ovh-monitor")
        log("库存监控已启动", "info")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "rules": [self.rule_status(r) for r in self.store.rules()],
        }

    def rule_status(self, rule: dict[str, Any]) -> dict[str, Any]:
        st = self._state.get(rule.get("id", ""), {})
        return {
            "id": rule.get("id"),
            "name": rule.get("name"),
            "planCode": rule.get("planCode"),
            "enabled": bool(rule.get("enabled", True)),
            "last_check": st.get("last_check", 0),
            "last_hit": st.get("last_hit", 0),
            "last_state": st.get("last_state", {}),
            "hits": st.get("hits", 0),
            "orders": st.get("orders", 0),
            "last_error": st.get("last_error", ""),
            "busy": rule.get("id") in self._busy,
        }

    # ------------------------------------------------------------ 主循环
    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            try:
                now = time.time()
                for rule in self.store.rules():
                    if not rule.get("enabled", True):
                        continue
                    rule_id = rule.get("id", "")
                    if not rule_id or rule_id in self._busy:
                        continue
                    st = self._state.setdefault(rule_id, {})
                    interval = max(30, int(rule.get("interval") or self.store.get("default_interval") or 300))
                    if now - st.get("last_check", 0) < interval:
                        continue
                    self._busy.add(rule_id)
                    asyncio.create_task(self._check(rule))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 保活
                log(f"监控循环异常：{exc}", "error")
            await asyncio.sleep(TICK)

    async def _check(self, rule: dict[str, Any]) -> None:
        rule_id = rule["id"]
        st = self._state.setdefault(rule_id, {})
        try:
            endpoint = self.store.endpoint
            stock = await plan_stock(endpoint, rule["planCode"])
            st["last_check"] = time.time()
            st["last_error"] = ""

            dcs = [d for d in (rule.get("datacenters") or []) if d] or list(stock.keys())
            threshold = STATE_RANK.get(rule.get("min_state", "1H-low"), STATE_RANK["1H-low"])
            hits = {
                dc: stock.get(dc, "unavailable")
                for dc in dcs
                if STATE_RANK.get(stock.get(dc, "unavailable"), 0) >= threshold
            }
            prev = st.get("last_state") or {}
            st["last_state"] = {dc: stock.get(dc, "unavailable") for dc in dcs}
            st["stock"] = stock

            if not hits:
                return

            st["hits"] = st.get("hits", 0) + 1
            st["last_hit"] = time.time()

            # 冷却：状态没变化且刚推过就不重复推
            signature = ",".join(f"{dc}:{state}" for dc, state in sorted(hits.items()))
            if signature == st.get("last_signature") and time.time() - st.get("last_notify", 0) < HIT_COOLDOWN:
                return
            st["last_signature"] = signature
            st["last_notify"] = time.time()

            name = rule.get("name") or rule["planCode"]
            lines = [
                f"🟢 <b>{name}</b> 有货了",
                f"机型：<code>{rule['planCode']}</code>",
                "机房：",
            ]
            for dc, state in sorted(hits.items()):
                lines.append(f"  • {datacenter_name(dc)}：{STATE_LABEL.get(state, state)}")
            if prev:
                changed = [dc for dc in hits if prev.get(dc) != hits[dc]]
                if changed:
                    lines.append(f"变化机房：{', '.join(datacenter_name(d) for d in changed)}")
            text = "\n".join(lines)
            log(f"检测到有货：{rule['planCode']} -> {signature}", "success", rule=rule_id)
            await send_telegram(
                str(self.store.get("telegram_bot_token") or ""),
                str(self.store.get("telegram_chat_id") or ""),
                text,
            )

            if rule.get("auto_order") and st.get("last_order", 0) + HIT_COOLDOWN < time.time():
                await self._auto_order(rule, hits)
        except OvhApiError as exc:
            st["last_error"] = exc.message
            st["last_check"] = time.time()
            log(f"规则 {rule.get('name') or rule_id} 查询失败：{exc.message}", "error", rule=rule_id)
        except Exception as exc:  # noqa: BLE001
            st["last_error"] = str(exc)
            st["last_check"] = time.time()
            log(f"规则 {rule.get('name') or rule_id} 异常：{exc}", "error", rule=rule_id)
        finally:
            self._busy.discard(rule_id)

    async def _auto_order(self, rule: dict[str, Any], hits: dict[str, str]) -> None:
        st = self._state.setdefault(rule["id"], {})
        best_dc = max(hits, key=lambda d: STATE_RANK.get(hits[d], 0))
        try:
            market = await load_market(self.store.endpoint, self.store.subsidiary)
            plan = find_plan(market, rule["planCode"])
            if not plan:
                log(f"自动下单失败：目录里找不到 {rule['planCode']}", "error", rule=rule["id"])
                return
            client = client_from_store(self.store)
            result = await create_order(
                client,
                plan,
                best_dc,
                subsidiary=self.store.subsidiary,
                months=int(rule.get("months") or 1),
                os_template=rule.get("os") or None,
                auto_pay=bool(rule.get("auto_pay")),
            )
            st["last_order"] = time.time()
            if result.get("ok"):
                st["orders"] = st.get("orders", 0) + 1
                msg = (
                    f"✅ 自动下单成功\n机型：<code>{rule['planCode']}</code>\n"
                    f"机房：{datacenter_name(best_dc)}\n订单号：{result.get('order_id')}\n"
                    f"付款链接：{result.get('order_url') or '（自动支付）'}"
                )
            else:
                msg = f"❌ 自动下单失败\n{result.get('error')}"
            await send_telegram(
                str(self.store.get("telegram_bot_token") or ""),
                str(self.store.get("telegram_chat_id") or ""),
                msg,
            )
        except OvhApiError as exc:
            log(f"自动下单接口错误：{exc.message}", "error", rule=rule["id"])
        except Exception as exc:  # noqa: BLE001
            log(f"自动下单异常：{exc}", "error", rule=rule["id"])
