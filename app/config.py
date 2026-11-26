"""配置存储：数据写入 data/config.json（被 .gitignore 忽略），可用环境变量初始化。"""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("OVH_DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.json"

# 各区域 API 入口。OVHcloud 的 OAuth2 签名 API 在 /1.0 下，三个区域的域名不同。
ENDPOINTS: dict[str, dict[str, Any]] = {
    "ovh-ca": {
        "label": "加拿大（ovh-ca）",
        "api": "https://ca.api.ovh.com/1.0",
        "subsidiary": "CA",
    },
    "ovh-eu": {
        "label": "欧洲（ovh-eu）",
        "api": "https://eu.api.ovh.com/1.0",
        "subsidiary": "IE",
    },
    "ovh-us": {
        "label": "美国（ovh-us）",
        "api": "https://api.us.ovhcloud.com/1.0",
        "subsidiary": "US",
    },
}

# 各区域常见的 ovhSubsidiary 取值，前端下拉用
SUBSIDIARIES: dict[str, list[dict[str, str]]] = {
    "ovh-ca": [
        {"code": "CA", "label": "加拿大 CA"},
        {"code": "QC", "label": "加拿大魁北克 QC"},
        {"code": "WE", "label": "加拿大西部 WE"},
        {"code": "IE", "label": "爱尔兰 IE（欧元）"},
    ],
    "ovh-eu": [
        {"code": "IE", "label": "爱尔兰 IE（欧元）"},
        {"code": "FR", "label": "法国 FR"},
        {"code": "GB", "label": "英国 GB"},
        {"code": "DE", "label": "德国 DE"},
        {"code": "ES", "label": "西班牙 ES"},
        {"code": "IT", "label": "意大利 IT"},
        {"code": "NL", "label": "荷兰 NL"},
        {"code": "PL", "label": "波兰 PL"},
        {"code": "PT", "label": "葡萄牙 PT"},
        {"code": "FI", "label": "芬兰 FI"},
        {"code": "LT", "label": "立陶宛 LT"},
        {"code": "CZ", "label": "捷克 CZ"},
    ],
    "ovh-us": [
        {"code": "US", "label": "美国 US"},
    ],
}

DEFAULT_CONFIG: dict[str, Any] = {
    "endpoint": "ovh-ca",
    "subsidiary": "CA",
    "application_key": "",
    "application_secret": "",
    "consumer_key": "",
    "panel_password": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "default_interval": 300,
    "rules": [],
}

ENV_SEED = {
    "endpoint": "OVH_ENDPOINT",
    "subsidiary": "OVH_SUBSIDIARY",
    "application_key": "OVH_APPLICATION_KEY",
    "application_secret": "OVH_APPLICATION_SECRET",
    "consumer_key": "OVH_CONSUMER_KEY",
    "panel_password": "PANEL_PASSWORD",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
}


def mask(value: str, keep: int = 4) -> str:
    """把密钥遮蔽成 sk-****abcd 的形式。"""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * max(4, len(value) - keep) + value[-keep:]


class Store:
    """线程安全的配置读写。"""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    # ---------------------------------------------------------------- 读写
    def load(self) -> dict[str, Any]:
        with self._lock:
            data = dict(DEFAULT_CONFIG)
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data.update(loaded)
                except (OSError, json.JSONDecodeError):
                    pass
            else:
                # 首次启动时用环境变量播种，方便 Docker 部署
                for key, env in ENV_SEED.items():
                    value = os.getenv(env, "").strip()
                    if value:
                        data[key] = value
                self._data = data
                self.save()
                return data
            self._data = data
            return data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            for key, value in values.items():
                if value is not None:
                    self._data[key] = value
            self.save()
            return dict(self._data)

    # ---------------------------------------------------------------- 便捷
    @property
    def endpoint(self) -> str:
        ep = self.get("endpoint", "ovh-ca")
        return ep if ep in ENDPOINTS else "ovh-ca"

    @property
    def subsidiary(self) -> str:
        return str(self.get("subsidiary") or ENDPOINTS[self.endpoint]["subsidiary"])

    @property
    def api_base(self) -> str:
        return ENDPOINTS[self.endpoint]["api"]

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.get("application_key")
            and self.get("application_secret")
            and self.get("consumer_key")
        )

    @property
    def password(self) -> str:
        return str(self.get("panel_password") or "")

    # ---------------------------------------------------------------- 规则
    def rules(self) -> list[dict[str, Any]]:
        rules = self.get("rules") or []
        return [r for r in rules if isinstance(r, dict)]

    def save_rules(self, rules: list[dict[str, Any]]) -> None:
        self.update(rules=rules)

    def new_rule_id(self) -> str:
        return "r_" + secrets.token_hex(4)


def env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}
