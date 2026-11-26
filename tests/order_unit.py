"""下单流程离线自测：用假客户端跑一遍 create_order，不需要 OVH 密钥。

重点验证：POST /order/cart/{cartId}/item/{itemId}/configuration 必须是逐条
提交单个 {"label": ..., "value": ...} 对象（一次性传数组会被 OVH 判 400）。
另外覆盖：配置被拒时继续下单、结算缺必填配置时报出缺哪一项、失败自动清车。

用法：python tests/order_unit.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OVH_DATA_DIR", tempfile.mkdtemp(prefix="ovh-order-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.order import create_order  # noqa: E402
from app.ovh_client import OvhApiError  # noqa: E402

PLAN = {
    "planCode": "27skle01-v1",
    "order_config": {
        "datacenters": ["bhs", "gra"],
        "regions": ["europe", "canada"],
        "os": ["none_64.en", "debian12_64"],
        "mandatory": ["dedicated_datacenter", "dedicated_os"],
    },
}


class FakeClient:
    """按真实接口形状应答的假 OvhClient。"""

    def __init__(self, reject_labels=(), checkout_error=None):
        self.reject_labels = set(reject_labels)
        self.checkout_error = checkout_error
        self.config_posts = []
        self.calls = []

    async def get(self, path, params=None, auth=True):
        self.calls.append(("GET", path, None, None))
        if path.endswith("/requiredConfiguration"):
            return [
                {"label": "dedicated_datacenter", "required": True},
                {"label": "region", "required": False},
                {"label": "dedicated_os", "required": True},
            ]
        if path.endswith("/checkout"):
            return {
                "prices": {"withTax": {"value": 1299000000, "currencyCode": "CAD"}},
                "contracts": [{"name": "c1"}, {"name": "c2"}],
            }
        if path.startswith("/me/order/"):
            return "delivered"
        return {"prices": {}, "items": []}

    async def post(self, path, body=None, auth=True):
        self.calls.append(("POST", path, body, None))
        if path == "/order/cart":
            return {"cartId": "cart-1"}
        if path.endswith("/assign"):
            return None
        if path.endswith("/eco"):
            return {"itemId": 42}
        if path.endswith("/item/42/configuration"):
            # 与真实 API 对齐：body 必须是单个 {label, value}
            if not isinstance(body, dict) or set(body) != {"label", "value"}:
                raise OvhApiError(400, f"configuration 参数错误：{body!r}", "BadRequest")
            if body["label"] in self.reject_labels:
                raise OvhApiError(400, f"invalid value {body['value']} for {body['label']}", "BadRequest")
            self.config_posts.append(body)
            return 10 + len(self.config_posts)
        if path.endswith("/checkout"):
            if self.checkout_error:
                raise self.checkout_error
            return {"orderId": 98765, "url": "https://ca.ovh.com/pay/98765", "prices": {}}
        raise AssertionError(f"unexpected POST {path}")

    async def delete(self, path, auth=True):
        self.calls.append(("DELETE", path, None, None))
        return None

    def deleted_carts(self):
        return [c[1] for c in self.calls if c[0] == "DELETE"]


def check(cond, label):
    if cond:
        print(f"  ok   {label}")
        return True
    print(f"  FAIL {label}")
    raise SystemExit(1)


async def case_happy_preview():
    print("[1] 预览下单：逐条提交机房/区域/系统")
    client = FakeClient()
    result = await create_order(
        client, PLAN, "gra", "CA", months=1, os_template="debian12_64", preview_only=True
    )
    check(result["ok"] is True, "预览成功")
    check([c["label"] for c in client.config_posts] == ["dedicated_datacenter", "region", "dedicated_os"],
          f"configuration 逐条提交且顺序正确：{[c['label'] for c in client.config_posts]}")
    check(all(isinstance(c, dict) and c["label"] and c["value"] for c in client.config_posts),
          "每条都是单个 {label,value} 对象")
    check(result["os"] == "debian12_64", "使用了指定系统")
    check(result["missing_config"] == [], "没有缺必填配置")
    check(result["contracts"] == 2, "读到合同数")
    eco = [c for c in client.calls if c[0] == "POST" and c[1].endswith("/eco")][0][2]
    check(eco == {"planCode": "27skle01-v1", "duration": "P1M", "pricingMode": "default", "quantity": 1},
          f"/eco 参数：{eco}")
    check(not client.deleted_carts(), "预览不清车（保留给用户继续付款）")


async def case_confirm_order():
    print("[2] 确认下单：生成订单号与付款链接")
    client = FakeClient()
    result = await create_order(client, PLAN, "bhs", "CA", months=3, auto_pay=False)
    check(result["ok"] is True, "下单成功")
    check(result["order_id"] == 98765, "订单号正确")
    check(result["order_url"].startswith("https://"), "付款链接存在")
    check(result["months"] == 3, "租期传递正确")
    checkout = [c for c in client.calls if c[0] == "POST" and c[1].endswith("/checkout")][0][2]
    check(checkout == {"autoPayWithPreferredPaymentMethod": False, "waiveRetractationPeriod": False},
          f"结算参数：{checkout}")
    eco = [c for c in client.calls if c[0] == "POST" and c[1].endswith("/eco")][0][2]
    check(eco["duration"] == "P3M", "租期 3 个月 -> P3M")


async def case_optional_label_rejected():
    print("[3] 非必填的 region 被拒：不该因此中止整单")
    client = FakeClient(reject_labels={"region"})
    result = await create_order(client, PLAN, "sgp", "CA", preview_only=True)
    check(result["ok"] is True, "仍然预览成功")
    labels = [c["label"] for c in client.config_posts]
    check("region" not in labels and "dedicated_datacenter" in labels and "dedicated_os" in labels,
          f"只跳过被拒项：{labels}")
    check(result["missing_config"] == [], "region 不是必填，不算缺配置")
    check(any("部分配置未生效" in s for s in result["steps"]), "步骤里记录了未生效项")


async def case_required_missing():
    print("[4] 必填的 dedicated_os 被拒：结算 400 要说清缺哪一项，并清车")
    err = OvhApiError(400, "Custom field dedicated_os is mandatory for product 27skle01-v1", "BadRequest")
    client = FakeClient(reject_labels={"dedicated_os"}, checkout_error=err)
    result = await create_order(client, PLAN, "gra", "CA", os_template="none_64.en")
    check(result["ok"] is False, "下单失败")
    check("缺少必填配置：dedicated_os" in result["error"], f"错误信息补全了缺失项：{result['error']}")
    check(result["missing_config"] == ["dedicated_os"], "结果里带 missing_config")
    check(client.deleted_carts() == ["/order/cart/cart-1"], "失败后清理购物车")


async def main():
    await case_happy_preview()
    await case_confirm_order()
    await case_optional_label_rejected()
    await case_required_missing()
    print("\n全部通过")


if __name__ == "__main__":
    asyncio.run(main())
