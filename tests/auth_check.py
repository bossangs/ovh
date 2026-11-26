"""面板密码鉴权自测：设置密码 -> 未登录 401 -> 登录拿 cookie -> 恢复正常。"""

import time

import httpx

BASE = "http://127.0.0.1:8013"

with httpx.Client(timeout=60, base_url=BASE) as c:
    for _ in range(40):
        try:
            if c.get("/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)

    c.put("/api/settings", json={"panel_password": "test-pass-123"})
    print("登录后设置密码 ->", c.get("/api/settings").status_code, "(应为 401)")

    r = c.post("/api/login", json={"password": "wrong"})
    print("错误密码登录 ->", r.status_code, r.text)

    r = c.post("/api/login", json={"password": "test-pass-123"})
    print("正确密码登录 ->", r.status_code, "cookie:", "ovh_panel_session" in c.cookies)
    print("带 cookie 读设置 ->", c.get("/api/settings").status_code)
    print("meta 无需登录 ->", c.get("/api/meta").status_code)
    c.put("/api/settings", json={"clear_panel_password": True})
    print("清理密码 ->", c.get("/api/settings").status_code)
