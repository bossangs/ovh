# OVH 中文面板

面向 **OVHcloud / Kimsufi** 独立服务器（主攻 KS / Eco / RISE-LE 低价款）的中文 Web 控制面板：**实时库存监控 + 有货 Telegram 提醒 + 一键/自动下单 + 机器重启、救援模式、重装系统**。

- 后端：Python 3.11 + FastAPI + Uvicorn + httpx（自己实现 OVH 的 `X-Ovh-*` 签名，不依赖官方 SDK）
- 前端：单页原生 HTML/CSS/JS，无需 Node、无需编译打包
- 部署：Docker 一行启动，也可以直接 `uvicorn` 跑；配置与日志落在 `data/`，不会进 Git

> 默认对接加拿大区 `https://ca.api.ovh.com/1.0`（`ovhSubsidiary=CA`），面板里可切换 `ovh-eu` / `ovh-us` 及对应子区域。

---

## 功能

| 模块 | 说明 |
| --- | --- |
| 系统设置 | 网页端配置区域接口（ovh-ca / ovh-eu / ovh-us）、`ovhSubsidiary`、AK/AS/CK、面板访问密码、Telegram Bot Token 与 Chat ID（支持一键测试推送）、默认轮询间隔 |
| 库存监控 | 拉取 OVH 公开目录 + 公开库存接口，展示机型代码、CPU/内存/硬盘、月价与安装费、每个机房的实时库存；支持关键词、最高月价、档位（KS / SYS / RISE / ADVANCE）、只看有货等筛选 |
| 监控规则 | 按机型添加规则：指定机房、轮询间隔、触发条件（现货 / 现货充足 / 72 小时），有货时推 Telegram；可选「有货自动下单」「下单后自动支付」 |
| 下单 | 走完整购物车流程（建车 → assign → 加商品 → 选机房/系统 → 结算），支持「预览结算（不产生订单）」与「确认下单」；可只生成付款链接，也可用账号默认付款方式自动扣款 |
| 我的机器 | 列出 `/dedicated/server`，显示型号、CPU、内存、硬盘、机房、到期时间；一键重启、切换启动项、进出救援模式、查询兼容系统模板并重装、查看执行任务 |
| 运行日志 | 所有接口动作、下单步骤、监控命中都记录在面板与 `data/logs.jsonl` |

---

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/bossangs/ovh.git
cd ovh
docker compose up -d --build
```

打开 <http://服务器IP:8000> 即可。数据保存在 `./data`，重建容器不丢。

不想用 compose 也可以：

```bash
docker build -t ovh-panel .
docker run -d --name ovh-panel -p 8000:8000 -v "$PWD/data:/app/data" --restart unless-stopped ovh-panel
```

### 方式二：本地 Python

```bash
git clone https://github.com/bossangs/ovh.git
cd ovh
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Windows 不想开终端可以新建一个 `start.bat`：

```bat
@echo off
cd /d %~dp0
call .venv\Scripts\activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
```

### 可选：用环境变量预置

```bash
cp .env.example .env   # 填好 AK/AS/CK、Telegram、面板密码
docker compose up -d --build
```

环境变量只在**首次启动**时写入 `data/config.json`，之后以网页里保存的配置为准。

---

## 获取 OVH API 密钥

1. 打开对应区域的令牌页：
   - 加拿大：<https://ca.api.ovh.com/createToken/>
   - 欧洲：<https://eu.api.ovh.com/createToken/>
   - 美国：<https://api.us.ovhcloud.com/createToken/>
2. 登录你的 OVH 账号；
3. 填表：
   - **Script name**：随便写，例如 `ovh-panel`
   - **Validity**：`Unlimited`
   - **Rights**：新增四行，分别 `GET` `POST` `PUT` `DELETE`，路径都填 `/*`
4. 点 **Authorize**，拿到 `Application Key`、`Application Secret`、`Consumer Key`，粘贴到面板「系统设置」里保存。

> 只想看库存、不需要下单和管机器的话，**可以不填密钥**：库存与监控走的是 OVH 公开接口。
> 只读权限也够用：把 `Rights` 只保留 `GET /*` 即可查看机器与订单，但下单/重启/重装会被 OVH 拒绝。

---

## 使用说明

### 库存监控

打开页面默认就在这一页。看到心仪机型，三个按钮：

- **下单**：选机房、租期、安装系统，先「预览结算」看金额，再「确认下单」。
- **设置监控**：把该机型加进监控规则，机房默认填好。
- **配置明细**：同一机型不同内存/硬盘组合的逐条库存（来自 API 的 FQN 明细）。

库存含义（OVH 原始状态 → 面板显示）：

| API 状态 | 含义 |
| --- | --- |
| `1H-high` | 现货充足，1 小时内可交付 |
| `1H-low` | 少量现货 |
| `72H` | 可下单，72 小时内交付 |
| `comingSoon` | 即将上架 |
| `unknown` | 状态未知 |
| `unavailable` | 无货 |

### 监控规则

新增规则时填机型代码（输入框带目录联想）、机房列表（逗号分隔，留空=全部机房）、轮询间隔与触发条件。

- 监控使用**单机型**库存接口，不需要拉整份目录，几十秒一轮也不重；
- 同一规则同一状态 30 分钟内不会重复推送，避免刷屏；
- 勾了「有货自动下单」后，命中即建购物车并下单；再勾「自动支付」会用账号默认付款方式直接扣款，**这是真实下单，请确认自己的付款方式与额度**。

### 我的机器

点「读取我的独立服务器」后，每台机器一张卡片：

- **重启**：`POST /dedicated/server/{name}/reboot`
- **启动项**：列出所有 boot，可直接设为启动项并重启
- **救援模式**：切到 rescue boot 并重启
- **重装系统**：拉取兼容模板 → 选分区方案 → 确认后 `install/start`
- **任务**：查看 reboot / install 等任务的执行进度

---

## 已对接的真实接口

以下接口在 2026-11 对 `ca.api.ovh.com` 实测确认（仓库中的解析逻辑与返回结构一致）：

| 接口 | 鉴权 | 说明 |
| --- | --- | --- |
| `GET /1.0/order/catalog/public/eco?ovhSubsidiary=CA` | 公开 | 约 12.7MB，返回 `plans[]`：`planCode`、`invoiceName`、`pricings[]`（月价 / 安装费）、`configurations[]`（可选机房、region、OS） |
| `GET /1.0/dedicated/server/datacenter/availabilities` | 公开 | 约 4.3MB、9000 条：按 `fqn`（机型.内存.硬盘）给出每个机房的 `availability`；加 `?planCode=` 可只查一个机型 |
| `GET /1.0/me`、`GET /1.0/me/order` | 密钥 | 账号信息与订单列表 |
| `POST /1.0/order/cart` → `/assign` → `/eco` → `/item/{id}/configuration` → `/checkout` | 密钥 | 下单流程 |
| `GET/POST/PUT /1.0/dedicated/server…` | 密钥 | 机器列表、详情、`serviceInfos`、`specifications/hardware`、`reboot`、`boot`、`install/*`、`task` |

注意：老的 `GET /dedicated/server/availabilities?country=ca` 现在需要登录（返回 401），所以本项目统一使用公开的 `datacenter/availabilities`。

---

## 目录结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由、鉴权中间件、页面入口
│   ├── config.py        # 配置存储（data/config.json）、区域与子区域表
│   ├── ovh_client.py    # OVH API 客户端（X-Ovh 签名、服务器时间校准）
│   ├── catalog.py       # 目录 + 库存合并、规格解析、缓存
│   ├── models_meta.py   # 机型 CPU 对照、机房中文名、库存状态
│   ├── order.py         # 购物车与下单
│   ├── machines.py      # 机器管理（重启/救援/重装/任务）
│   ├── monitor.py       # 后台轮询、Telegram 推送、自动下单
│   ├── notify.py        # Telegram 发送
│   ├── logs.py          # 运行日志
│   └── static/          # index.html / style.css / app.js
├── data/                # 运行时数据（.gitignore，仅保留 .gitkeep）
├── tests/               # 冒烟测试脚本
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 数据与安全

- 所有密钥只保存在 `data/config.json`（文件权限 600）或被环境变量读取；仓库里不含任何真实密钥，`data/` 已进 `.gitignore`。
- 网页接口默认返回**遮蔽后**的密钥（如 `********abcd`），设置页留空表示「不修改」。
- 公网部署请务必设置「面板访问密码」，或放在 Cloudflare Tunnel / Nginx Basic Auth 后面。
- 日志只记录操作结果与错误信息，不记录 AK/AS/CK。

---

## 自测

```bash
# 起一个临时服务后
python3 tests/smoke.py           # 首页、静态资源、设置、目录、库存、规则 CRUD、日志
python3 tests/monitor_check.py   # 加一条会命中的规则，确认后台轮询与命中日志
```

两个脚本默认访问 `http://127.0.0.1:8011`，按需改 `BASE`。

---

## 常见问题

**打开页面一片空白 / 接口 401？**
设置了面板密码，先在登录框输入密码；忘记密码就删掉 `data/config.json` 里的 `panel_password`（或整个文件）后重启。

**提示「尚未填写 API 三件套」？**
下单与机器管理需要密钥；库存与监控不需要。

**下单报 403 / 权限不足？**
令牌的 Rights 要覆盖对应动作（POST `/order/cart/*`、PUT `/dedicated/server/*` 等），最省事是给 `GET/POST/PUT/DELETE` 的 `/*`。

**订单生成了但没付款？**
未开启自动支付时，订单是「待支付」状态，面板会给出 `url` 付款链接，去 OVH 控制台付款即可。订单一般会在一段时间后过期。

**时间对不上导致签名失败？**
客户端会自动向 `/{api}/auth/time` 校准服务器时间，一般无需处理；若系统时间差得离谱，建议开启 NTP。

---

## 免责声明

本项目只是对 OVHcloud 官方公开 API 的封装，不提供任何绕过库存、风控或计费的手段。自动下单会真实产生费用，请自行确认价格、付款方式与账号额度。使用产生的任何费用与后果由使用者承担。
