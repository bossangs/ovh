"""监控流程自测：加一条会命中的规则，等后台轮询一次，确认日志与运行时状态。"""

import time

import httpx

BASE = "http://127.0.0.1:8011"

with httpx.Client(timeout=90) as c:
    for _ in range(60):
        try:
            if c.get(BASE + "/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)

    print("plan stock:", c.get(BASE + "/api/plans/27skle01-v1/stock").text[:300])
    print("plan stock rise:", c.get(BASE + "/api/plans/24rise01-v2/stock").text[:300])

    rule = c.post(
        BASE + "/api/rules",
        json={
            "planCode": "24rise01-v2",
            "name": "RISE-1 有货测试",
            "datacenters": ["bhs"],
            "interval": 30,
            "min_state": "1H-low",
        },
    ).json()["rule"]
    print("rule:", rule["id"])
    time.sleep(38)
    rules = c.get(BASE + "/api/rules").json()["rules"]
    print("runtime:", rules[0]["runtime"])
    print("logs:")
    for item in c.get(BASE + "/api/logs?limit=10").json()["logs"]:
        print("  ", item["time"], item["level"], item["message"])
    c.delete(BASE + f"/api/rules/{rule['id']}")
