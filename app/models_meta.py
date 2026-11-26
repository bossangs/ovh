"""静态元数据：型号 CPU 信息、机房名称、库存状态含义。

公开目录接口只给出 planCode 与套内配置（内存/硬盘），并不包含 CPU 型号，
因此对 ECO 目录里没有在 invoiceName 中写 CPU 的 LE 机型，这里做一份对照表
（来源：eco.ovhcloud.com / kimsufi.com 各机型参数页，2026-11 核对）；
其余机型直接取 invoiceName 中 "型号 | CPU" 的后半段。
"""

from __future__ import annotations

import re

# planCode -> 处理器信息
MODELS: dict[str, dict[str, object]] = {
    "27skle01-v1": {"cpu": "Intel Xeon E3-1245 v2", "cores": 4, "threads": 8, "clock": "3.4–3.8 GHz", "score": 4756},
    "27skle02-v1": {"cpu": "Intel Xeon E5-1620 v2", "cores": 4, "threads": 8, "clock": "3.7–3.9 GHz", "score": 6545},
    "27skle03-v1": {"cpu": "Intel Xeon E-2274G", "cores": 4, "threads": 8, "clock": "4.0–4.9 GHz", "score": 9789},
    "27skle04-v1": {"cpu": "Intel Xeon E3-1270 v6", "cores": 4, "threads": 8, "clock": "3.8–4.2 GHz"},
    "27skle05-v1": {"cpu": "Intel Xeon E5-1650 v4", "cores": 6, "threads": 12, "clock": "3.6–4.0 GHz"},
    "27skle06-v1": {"cpu": "AMD EPYC 7351P", "cores": 16, "threads": 32, "clock": "2.4–2.9 GHz"},
    "27sysstorle01-v1": {"cpu": "Intel Xeon D-2141I", "cores": 8, "threads": 16, "clock": "2.2–3.0 GHz"},
    "27risele01-v1": {"cpu": "AMD Ryzen 7 PRO 3700", "cores": 8, "threads": 16, "clock": "3.6–4.4 GHz", "score": 22800},
    "27risele02-v1": {"cpu": "AMD Ryzen 5 5600X", "cores": 6, "threads": 12, "clock": "3.7–4.6 GHz"},
    "27risele03-v1": {"cpu": "AMD Ryzen 7 5800X", "cores": 8, "threads": 16, "clock": "3.8–4.7 GHz"},
}

# OVH 机房代码 -> 中文名称
DATACENTERS: dict[str, str] = {
    "bhs": "加拿大·博阿尔努瓦 BHS",
    "gra": "法国·格拉沃利讷 GRA",
    "rbx": "法国·鲁贝 RBX",
    "sbg": "法国·斯特拉斯堡 SBG",
    "waw": "波兰·华沙 WAW",
    "lon": "英国·伦敦 LON",
    "fra": "德国·法兰克福 FRA",
    "ynm": "印度·孟买 YNM",
    "sgp": "新加坡 SGP",
    "syd": "澳大利亚·悉尼 SYD",
    "par": "法国·巴黎 PAR",
    "eu-west-par-a": "法国·巴黎 PAR-A",
    "eu-west-par-b": "法国·巴黎 PAR-B",
    "eu-west-par-c": "法国·巴黎 PAR-C",
}

# 库存状态：数字越大越容易买到
STATE_RANK: dict[str, int] = {
    "1H-high": 5,
    "1H-low": 4,
    "72H": 3,
    "comingSoon": 2,
    "unknown": 1,
    "unavailable": 0,
}

STATE_LABEL: dict[str, str] = {
    "1H-high": "现货充足",
    "1H-low": "少量现货",
    "72H": "72 小时内交付",
    "comingSoon": "即将上架",
    "unknown": "状态未知",
    "unavailable": "无货",
}

# 监控规则可选阈值 -> 达到该状态即视为「有货」
RULE_STATES = ["1H-high", "1H-low", "72H"]


def datacenter_name(code: str) -> str:
    return DATACENTERS.get(code, code.upper())


def cpu_from_invoice(invoice_name: str) -> str:
    """从 'KS-2 | Intel Xeon-D 1540' 这样的名称里取出 CPU。"""
    if "|" in invoice_name:
        return invoice_name.split("|", 1)[1].strip()
    return ""


def model_info(plan_code: str, invoice_name: str = "") -> dict[str, object]:
    info = dict(MODELS.get(plan_code, {}))
    if not info.get("cpu"):
        cpu = cpu_from_invoice(invoice_name)
        if cpu:
            info["cpu"] = cpu
            m = re.search(r"(\d+)\s*c\s*/\s*(\d+)\s*t", cpu)
            if m:
                info["cores"] = int(m.group(1))
                info["threads"] = int(m.group(2))
    return info
