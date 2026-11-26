"""中文机器控制：服务器列表、硬件、到期时间、重启、救援模式、重装系统、任务。"""

from __future__ import annotations

from typing import Any

from .logs import log
from .ovh_client import OvhApiError, OvhClient


async def _safe(coro, default: Any = None) -> Any:
    try:
        return await coro
    except OvhApiError:
        return default


async def list_servers(client: OvhClient) -> list[dict[str, Any]]:
    names = await client.get("/dedicated/server")
    if not isinstance(names, list):
        return []
    result: list[dict[str, Any]] = []
    for name in names:
        detail = await _safe(client.get(f"/dedicated/server/{name}"), {}) or {}
        infos = await _safe(client.get(f"/dedicated/server/{name}/serviceInfos"), {}) or {}
        hw = await _safe(client.get(f"/dedicated/server/{name}/specifications/hardware"), {}) or {}
        result.append(
            {
                "name": name,
                "state": detail.get("state", ""),
                "reverse": detail.get("reverse", ""),
                "datacenter": detail.get("datacenter", ""),
                "rack": detail.get("rack", ""),
                "monitoring": detail.get("monitoring", ""),
                "bootId": detail.get("bootId"),
                "expiration": infos.get("expiration", ""),
                "renewalType": infos.get("renewalType", ""),
                "serviceStatus": infos.get("status", ""),
                "hardware": {
                    "description": hw.get("description", ""),
                    "processor": hw.get("processor", ""),
                    "memory": hw.get("memorySize", {}),
                    "disk": hw.get("disk", []),
                    "bandwidth": hw.get("bandwidth", {}),
                    "usbKeys": hw.get("usbKeys"),
                },
            }
        )
    return result


async def server_detail(client: OvhClient, name: str) -> dict[str, Any]:
    detail = await client.get(f"/dedicated/server/{name}")
    infos = await _safe(client.get(f"/dedicated/server/{name}/serviceInfos"), {}) or {}
    hw = await _safe(client.get(f"/dedicated/server/{name}/specifications/hardware"), {}) or {}
    ips = await _safe(client.get(f"/dedicated/server/{name}/ips"), []) or []
    return {
        "detail": detail,
        "serviceInfos": infos,
        "hardware": hw,
        "ips": ips,
    }


async def reboot(client: OvhClient, name: str) -> dict[str, Any]:
    task = await client.post(f"/dedicated/server/{name}/reboot")
    log(f"已发送重启指令：{name}", "info", server=name)
    return {"ok": True, "task": task}


async def list_boots(client: OvhClient, name: str) -> list[dict[str, Any]]:
    ids = await client.get(f"/dedicated/server/{name}/boot")
    boots: list[dict[str, Any]] = []
    for boot_id in (ids or [])[:30]:
        info = await _safe(client.get(f"/dedicated/server/{name}/boot/{boot_id}"), {}) or {}
        boots.append(
            {
                "bootId": boot_id,
                "bootType": info.get("bootType", ""),
                "kernel": info.get("kernel", ""),
                "description": info.get("description", ""),
                "active": bool(info.get("active")),
            }
        )
    return boots


async def set_boot(client: OvhClient, name: str, boot_id: int, reboot: bool = True) -> dict[str, Any]:
    await client.put(f"/dedicated/server/{name}", {"bootId": int(boot_id)})
    log(f"已设置启动模式：{name} -> bootId {boot_id}", "info", server=name)
    if reboot:
        await client.post(f"/dedicated/server/{name}/reboot")
        log(f"切换启动模式后已重启：{name}", "info", server=name)
    return {"ok": True, "bootId": boot_id, "rebooted": reboot}


async def rescue_ids(client: OvhClient, name: str) -> list[int]:
    ids = await client.get(f"/dedicated/server/{name}/boot", params={"bootType": "rescue"})
    return [int(i) for i in (ids or [])]


async def set_rescue(client: OvhClient, name: str, enable: bool, reboot: bool = True) -> dict[str, Any]:
    """进入救援模式：取型号第一条 rescue 启动项；恢复正常：用 harddisk 启动项。"""
    boot_id: int | None = None
    if enable:
        ids = await rescue_ids(client, name)
        if not ids:
            raise OvhApiError(404, "该服务器没有可用的救援模式启动项", "NoRescueBoot")
        boot_id = ids[0]
    else:
        ids = await client.get(f"/dedicated/server/{name}/boot", params={"bootType": "harddisk"})
        if ids:
            boot_id = int(ids[0])
        else:
            raise OvhApiError(404, "找不到硬盘启动项，请到「启动项」里手动选择", "NoBoot")
    result = await set_boot(client, name, boot_id, reboot=reboot)
    result["mode"] = "rescue" if enable else "normal"
    return result


async def install_templates(client: OvhClient, name: str) -> list[str]:
    data = await client.get(f"/dedicated/server/{name}/install/compatibleTemplates")
    if isinstance(data, dict):
        merged: list[str] = []
        for key in ("available", "compatible"):
            for item in data.get(key) or []:
                if item not in merged:
                    merged.append(item)
        return sorted(merged)
    return sorted(data or [])


async def partition_schemes(client: OvhClient, name: str, template: str) -> list[dict[str, Any]]:
    data = await client.get(
        f"/dedicated/server/{name}/install/compatibleTemplatePartitionSchemes",
        params={"templateName": template},
    )
    schemes = []
    for item in data or []:
        if isinstance(item, dict):
            schemes.append(item)
        else:
            schemes.append({"name": str(item)})
    return schemes


async def start_install(
    client: OvhClient,
    name: str,
    template: str,
    scheme: str,
    hostname: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "templateName": template,
        "partitionSchemeName": scheme,
        "details": {},
    }
    if hostname:
        body["details"]["customHostname"] = hostname
    task = await client.post(f"/dedicated/server/{name}/install/start", body)
    log(f"已开始重装：{name} -> {template}", "warning", server=name, template=template)
    return {"ok": True, "task": task, "template": template, "scheme": scheme}


async def tasks(client: OvhClient, name: str, limit: int = 12) -> list[dict[str, Any]]:
    ids = await client.get(f"/dedicated/server/{name}/task")
    result: list[dict[str, Any]] = []
    for task_id in (ids or [])[-limit:][::-1]:
        info = await _safe(client.get(f"/dedicated/server/{name}/task/{task_id}"), {}) or {}
        result.append(
            {
                "taskId": task_id,
                "function": info.get("function", ""),
                "status": info.get("status", ""),
                "comment": info.get("comment", ""),
                "startDate": info.get("startDate", ""),
                "doneDate": info.get("doneDate", ""),
                "lastUpdate": info.get("lastUpdate", ""),
            }
        )
    return result
