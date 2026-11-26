"""把 OVH 公开目录（eco）与公开库存（datacenter/availabilities）合成一份可展示的机型清单。

实测（2026-11，ovh-ca）：
  GET /order/catalog/public/eco?ovhSubsidiary=CA            -> 12.7MB, 111 个 plan
  GET /dedicated/server/datacenter/availabilities            -> 4.3MB, 约 9000 条 FQN
     每条: {fqn, memory, planCode, server, storage, systemStorage, datacenters:[{datacenter, availability}]}
  availability 取值: 1H-high / 1H-low / 72H / comingSoon / unknown / unavailable
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .models_meta import STATE_LABEL, STATE_RANK, datacenter_name, model_info
from .ovh_client import OvhClient

CACHE_TTL = 120.0  # 秒，整份目录+库存的缓存时长（目录约 13MB，别拉太勤）
_catalog_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = asyncio.Lock()


# ---------------------------------------------------------------- 解析工具
_MEM_RE = re.compile(r"^ram-(\d+)g(.*)$")
_STORE_ITEM_RE = re.compile(r"(\d+)x(\d+)([a-z]+)(?:-pcie-gen(\d+))?")


def parse_memory(raw: str) -> dict[str, Any]:
    """'ram-16g-ddr3-noecc-1333' -> {size: 16, text: '16GB DDR3 1333 非ECC'}"""
    if not raw:
        return {"size": None, "text": ""}
    m = _MEM_RE.match(raw)
    if not m:
        return {"size": None, "text": raw}
    size = int(m.group(1))
    rest = m.group(2).strip("-")
    parts = [p for p in rest.split("-") if p]
    tokens: list[str] = []
    for p in parts:
        if p == "noecc":
            tokens.append("非ECC")
        elif p == "ecc":
            tokens.append("ECC")
        elif p in {"ddr2", "ddr3", "ddr4", "ddr5"}:
            tokens.append(p.upper())
        elif p.startswith("on-die"):
            tokens.append("On-Die ECC")
        elif p.isdigit():
            tokens.append(f"{p}MHz")
        else:
            tokens.append(p)
    text = f"{size}GB" + (" " + " ".join(tokens) if tokens else "")
    return {"size": size, "text": text, "raw": raw}


def parse_storage(raw: str, system: str = "") -> dict[str, Any]:
    """'softraid-2x480ssd' -> {text: '2×480GB SSD（软 RAID）'}"""
    if not raw:
        return {"text": "", "raw": raw}
    raid_map = {
        "softraid": "软 RAID",
        "hardraid": "硬 RAID",
        "hybridsoftraid": "混合软 RAID",
        "noraid": "无阵列",
    }
    prefix, _, rest = raw.partition("-")
    raid = raid_map.get(prefix, prefix)
    items = _STORE_ITEM_RE.findall(rest)
    if not items:
        return {"text": raw, "raw": raw}
    kind_map = {
        "ssd": "SSD",
        "sata": "SATA",
        "sa": "HDD",
        "hdd": "HDD",
        "nvme": "NVMe",
        "sas": "SAS",
        "ssdsata": "SATA SSD",
    }
    chunks: list[str] = []
    for count, size, kind, gen in items:
        label = kind_map.get(kind, kind.upper())
        if gen:
            label += f" PCIe Gen{gen}"
        chunks.append(f"{count}×{size}GB {label}")
    text = " + ".join(chunks)
    if raid and raid != "无阵列":
        text += f"（{raid}）"
    result = {"text": text, "raw": raw}
    if system:
        result["system"] = parse_storage(system)["text"]
    return result


def best_state(states: list[str]) -> str:
    if not states:
        return "unknown"
    return max(states, key=lambda s: STATE_RANK.get(s, 0))


def _monthly_price(plan: dict[str, Any]) -> dict[str, Any]:
    """取「按月续费」的默认价格；没有则退回任意按月价格。"""
    pricings = plan.get("pricings") or []
    chosen = None
    for p in pricings:
        caps = p.get("capacities") or []
        if "renew" in caps and p.get("interval") == 1 and p.get("intervalUnit") == "month" and p.get("mode") == "default":
            chosen = p
            break
    if chosen is None:
        for p in pricings:
            if p.get("interval") == 1 and p.get("intervalUnit") == "month":
                chosen = p
                break
    setup = None
    for p in pricings:
        caps = p.get("capacities") or []
        if "installation" in caps and p.get("interval") == 0 and (p.get("price") or 0) > 0:
            setup = p
            break
    money = lambda p: None if p is None else round((p.get("price") or 0) / 1e8, 2)  # noqa: E731
    return {
        "monthly": money(chosen),
        "monthly_formatted": (chosen or {}).get("formattedPrice", ""),
        "setup": money(setup),
        "setup_formatted": (setup or {}).get("formattedPrice", ""),
        "commitment": (chosen or {}).get("commitment", 0),
    }


def _tier(plan_code: str, invoice_name: str, commercial: dict[str, Any]) -> str:
    text = f"{plan_code} {invoice_name} {(commercial or {}).get('name', '')}".upper()
    if " ADVANCE" in f" {text}" or plan_code.startswith("24adv"):
        return "ADVANCE"
    if "RISELE" in plan_code.upper() or "RISE-LE" in text:
        return "RISE-LE"
    if plan_code.upper().startswith(("27SK", "24SK", "25SK", "26SK")) or re.search(r"\bKS-", text):
        return "KS"
    if "SYS" in plan_code.upper() or "SYS-" in text:
        return "SYS"
    if "RISE" in plan_code.upper() or "RISE-" in text:
        return "RISE"
    return "其他"


def _is_ks(tier: str) -> bool:
    return tier in {"KS", "SYS", "RISE-LE"}


def build_offers(
    catalog: dict[str, Any],
    avail: list[dict[str, Any]],
    endpoint: str = "ovh-ca",
) -> dict[str, Any]:
    currency = (catalog.get("locale") or {}).get("currencyCode", "")
    tax_rate = (catalog.get("locale") or {}).get("taxRate", 0)

    # 先按 planCode 汇总库存明细
    by_plan: dict[str, list[dict[str, Any]]] = {}
    for entry in avail:
        if not isinstance(entry, dict):
            continue
        by_plan.setdefault(entry.get("planCode", ""), []).append(entry)

    offers: list[dict[str, Any]] = []
    for plan in catalog.get("plans") or []:
        plan_code = plan.get("planCode", "")
        if not plan_code:
            continue
        invoice_name = plan.get("invoiceName", "")
        commercial = ((plan.get("blobs") or {}).get("commercial") or {})
        name = commercial.get("name") or invoice_name.split("|")[0].strip() or plan_code

        config_values: dict[str, list[str]] = {}
        for cfg in plan.get("configurations") or []:
            config_values[cfg.get("name", "")] = list(cfg.get("values") or [])

        datacenters = [dc for dc in config_values.get("dedicated_datacenter", []) if dc]

        configs: list[dict[str, Any]] = []
        stock: dict[str, str] = {dc: "unavailable" for dc in datacenters}
        ram_sizes: set[int] = set()
        for entry in by_plan.get(plan_code, []):
            mem = parse_memory(entry.get("memory", ""))
            sto = parse_storage(entry.get("storage", ""), entry.get("systemStorage") or "")
            dc_states: dict[str, str] = {}
            for item in entry.get("datacenters") or []:
                code = item.get("datacenter", "")
                state = item.get("availability", "unknown")
                dc_states[code] = state
                if STATE_RANK.get(state, 0) > STATE_RANK.get(stock.get(code, "unavailable"), 0):
                    stock[code] = state
                if code not in datacenters:
                    datacenters.append(code)
                    stock.setdefault(code, state)
            if mem.get("size"):
                ram_sizes.add(int(mem["size"]))
            configs.append(
                {
                    "fqn": entry.get("fqn", ""),
                    "memory": mem.get("text", ""),
                    "ram_size": mem.get("size"),
                    "storage": sto.get("text", ""),
                    "system_storage": sto.get("system", ""),
                    "stock": dc_states,
                    "best": best_state(list(dc_states.values())),
                }
            )

        configs.sort(key=lambda c: (c.get("ram_size") or 0, c.get("storage") or ""))

        info = model_info(plan_code, invoice_name)
        price = _monthly_price(plan)
        tier = _tier(plan_code, invoice_name, commercial)

        storage_texts = sorted({c["storage"] for c in configs if c["storage"]})
        offer = {
            "planCode": plan_code,
            "name": name,
            "invoiceName": invoice_name,
            "product": plan.get("product", ""),
            "tier": tier,
            "ks": _is_ks(tier),
            "cpu": info.get("cpu", ""),
            "cpu_cores": info.get("cores"),
            "cpu_threads": info.get("threads"),
            "cpu_clock": info.get("clock", ""),
            "cpu_score": info.get("score"),
            "ram_summary": (
                f"{min(ram_sizes)}–{max(ram_sizes)} GB" if len(ram_sizes) > 1 else (f"{max(ram_sizes)} GB" if ram_sizes else "")
            ),
            "storage_summary": " / ".join(storage_texts[:3]),
            "price": price,
            "currency": currency,
            "tax_rate": tax_rate,
            "datacenters": [{"code": dc, "name": datacenter_name(dc)} for dc in datacenters],
            "stock": stock,
            "best_state": best_state(list(stock.values())),
            "has_stock": any(STATE_RANK.get(s, 0) >= STATE_RANK["72H"] for s in stock.values()),
            "has_now": any(STATE_RANK.get(s, 0) >= STATE_RANK["1H-low"] for s in stock.values()),
            "configs": configs,
            "order_config": {
                "datacenters": datacenters,
                "regions": config_values.get("region", []),
                "os": config_values.get("dedicated_os", []),
                "mandatory": [
                    c.get("name") for c in (plan.get("configurations") or []) if c.get("isMandatory")
                ],
            },
        }
        offers.append(offer)

    offers.sort(key=lambda o: (o["price"]["monthly"] is None, o["price"]["monthly"] or 0))
    return {
        "endpoint": endpoint,
        "currency": currency,
        "tax_rate": tax_rate,
        "updated": int(time.time()),
        "count": len(offers),
        "plans": offers,
        "state_labels": STATE_LABEL,
    }


# ---------------------------------------------------------------- 抓取缓存
async def load_market(endpoint: str, subsidiary: str, force: bool = False) -> dict[str, Any]:
    """获取（并缓存）目录 + 库存合成结果。"""
    key = f"{endpoint}:{subsidiary}"
    now = time.time()
    cached = _catalog_cache.get(key)
    if cached and not force and now - cached[0] < CACHE_TTL:
        return cached[1]

    async with _lock:
        cached = _catalog_cache.get(key)
        if cached and not force and time.time() - cached[0] < CACHE_TTL:
            return cached[1]

        client = OvhClient(endpoint=endpoint)
        catalog, avail = await asyncio.gather(
            client.public("/order/catalog/public/eco", {"ovhSubsidiary": subsidiary}),
            client.public("/dedicated/server/datacenter/availabilities"),
        )
        market = build_offers(catalog, avail if isinstance(avail, list) else [], endpoint)
        _catalog_cache[key] = (time.time(), market)
        return market


def find_plan(market: dict[str, Any], plan_code: str) -> dict[str, Any] | None:
    for plan in market.get("plans", []):
        if plan["planCode"] == plan_code:
            return plan
    return None


async def plan_stock(endpoint: str, plan_code: str) -> dict[str, str]:
    """单独查询某个机型各机房的实时库存（比整份目录轻很多）。"""
    client = OvhClient(endpoint=endpoint)
    rows = await client.public(
        "/dedicated/server/datacenter/availabilities", {"planCode": plan_code}
    )
    stock: dict[str, str] = {}
    for entry in rows or []:
        for item in entry.get("datacenters") or []:
            code = item.get("datacenter", "")
            state = item.get("availability", "unknown")
            prev = stock.get(code)
            if prev is None or STATE_RANK.get(state, 0) > STATE_RANK.get(prev, 0):
                stock[code] = state
    return stock
