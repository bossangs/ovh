import json
import time

import httpx

BASE = "http://127.0.0.1:8011"


def show(name, resp, limit=600):
    text = resp.text
    print(f"\n=== {name}: HTTP {resp.status_code} len={len(text)}")
    print(text[:limit])


with httpx.Client(timeout=120) as c:
    for _ in range(60):
        try:
            r = c.get(BASE + "/api/health")
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    show("health", c.get(BASE + "/api/health"))
    show("index", c.get(BASE + "/"), 200)
    show("css", c.get(BASE + "/static/style.css"), 120)
    show("js", c.get(BASE + "/static/app.js"), 120)
    show("meta", c.get(BASE + "/api/meta"))
    show("settings", c.get(BASE + "/api/settings"))

    r = c.put(BASE + "/api/settings", json={"endpoint": "ovh-ca", "subsidiary": "CA", "telegram_chat_id": "", "default_interval": 240})
    show("put settings", r)

    t0 = time.time()
    r = c.get(BASE + "/api/catalog", params={"ks_only": 1})
    print(f"\n=== catalog: HTTP {r.status_code} in {time.time()-t0:.1f}s")
    if r.status_code == 200:
        data = r.json()
        print("total", data["total"], "shown", data["shown"], "currency", data["currency"])
        for p in data["plans"][:6]:
            print(" ", p["planCode"], "|", p["name"], "|", p["price"]["monthly"], p["currency"], "|", p["cpu"], "|", p["ram_summary"], "|", p["storage_summary"][:40], "| stock:", p["stock"])
        print("count KS-LE:", sum(1 for p in data["plans"] if p["planCode"].startswith("27skle")))
    else:
        print(r.text[:300])

    show("plan stock", c.get(BASE + "/api/plans/27skle01-v1/stock"))

    r = c.post(BASE + "/api/rules", json={"planCode": "27skle01-v1", "name": "测试规则", "datacenters": ["bhs", "gra"], "interval": 60, "min_state": "1H-low"})
    show("add rule", r)
    rule_id = r.json().get("rule", {}).get("id")
    show("list rules", c.get(BASE + "/api/rules"))
    show("update rule", c.put(BASE + f"/api/rules/{rule_id}", json={"enabled": False}))
    show("delete rule", c.delete(BASE + f"/api/rules/{rule_id}"))

    show("servers(no creds)", c.get(BASE + "/api/servers"))
    show("orders(no creds)", c.get(BASE + "/api/orders"))
    show("logs", c.get(BASE + "/api/logs"), 900)
    show("404 cat", c.get(BASE + "/api/plans/xx/stock"))
