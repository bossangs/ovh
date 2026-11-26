/* OVH 中文面板前端：原生 JS，无构建步骤 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  meta: null,
  settings: null,
  plans: [],
  currency: "CAD",
  rules: [],
  servers: [],
};

/* ------------------------------------------------------------------ 工具 */
function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function money(value, currency) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(2)} ${currency || state.currency || ""}`.trim();
}

function relTime(ts) {
  if (!ts) return "从未";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 0) return "刚刚";
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

const STATE_TEXT = {
  "1H-high": "现货充足",
  "1H-low": "少量现货",
  "72H": "72 小时交付",
  comingSoon: "即将上架",
  unknown: "状态未知",
  unavailable: "无货",
};

const TIER_TEXT = { KS: "KS", SYS: "SYS", "RISE-LE": "RISE-LE", RISE: "RISE", ADVANCE: "ADVANCE", 其他: "其它" };

let toastTimer = null;
function toast(message, kind = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3600);
}

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  let data = {};
  try {
    data = await res.json();
  } catch (err) {
    data = {};
  }
  if (res.status === 401) {
    showLogin();
    throw new Error("请先登录面板");
  }
  if (!res.ok) {
    throw new Error(data.detail || data.message || `请求失败（HTTP ${res.status}）`);
  }
  return data;
}

/* ------------------------------------------------------------------ 登录 */
function showLogin() {
  $("#login-overlay").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = $("#login-password").value;
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password } });
    $("#login-overlay").classList.add("hidden");
    $("#login-password").value = "";
    boot();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

/* ------------------------------------------------------------------ 弹窗 */
function openModal(title, bodyHtml, footHtml = "") {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHtml;
  $("#modal-foot").innerHTML = footHtml;
  $("#modal").classList.remove("hidden");
}

function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal-body").innerHTML = "";
  $("#modal-foot").innerHTML = "";
}

$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (event) => {
  if (event.target.id === "modal") closeModal();
});

/* ------------------------------------------------------------------ 标签页 */
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    const panel = $(`#tab-${tab.dataset.tab}`);
    if (panel) panel.classList.add("active");
    if (tab.dataset.tab === "rules") loadRules();
    if (tab.dataset.tab === "orders") loadOrders();
    if (tab.dataset.tab === "logs") loadLogs();
    if (tab.dataset.tab === "machines" && !state.servers.length) loadServers();
  });
});

/* ------------------------------------------------------------------ 初始化 */
async function loadMeta() {
  state.meta = await api("/api/meta");
  const endpointSelect = $("#s-endpoint");
  endpointSelect.innerHTML = state.meta.endpoints
    .map((e) => `<option value="${e.code}">${esc(e.label)}</option>`)
    .join("");
  endpointSelect.addEventListener("change", () => {
    const list = state.meta.subsidiaries[endpointSelect.value] || [];
    $("#s-subsidiary").innerHTML = list
      .map((s) => `<option value="${s.code}">${esc(s.label)}</option>`)
      .join("");
  });
  const pill = $("#pill-region");
  pill.textContent = `区域：${state.meta.current.endpoint} / ${state.meta.current.subsidiary}`;
}

async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data;
  $("#s-endpoint").value = data.endpoint;
  const subs = state.meta.subsidiaries[data.endpoint] || [];
  $("#s-subsidiary").innerHTML = subs
    .map((s) => `<option value="${s.code}">${esc(s.label)}</option>`)
    .join("");
  if (subs.some((s) => s.code === data.subsidiary)) $("#s-subsidiary").value = data.subsidiary;
  $("#s-ak").value = data.application_key || "";
  $("#s-as").value = "";
  $("#s-ck").value = "";
  $("#s-tg-token").value = data.telegram_bot_token || "";
  $("#s-tg-chat").value = data.telegram_chat_id || "";
  $("#s-interval").value = data.default_interval || 300;

  const acct = $("#pill-account");
  acct.textContent = data.has_credentials ? "密钥：已配置" : "密钥：未配置";
  acct.className = `pill ${data.has_credentials ? "ok" : "bad"}`;
  $("#pill-region").textContent = `区域：${data.endpoint} / ${data.subsidiary}`;

  $("#btn-logout").hidden = !data.panel_password_set;
  $("#settings-state").textContent = [
    data.application_key_set ? "AK 已设置" : "AK 未设置",
    data.application_secret_set ? "AS 已设置" : "AS 未设置",
    data.consumer_key_set ? "CK 已设置" : "CK 未设置",
    data.panel_password_set ? "面板密码已设置" : "面板无密码",
  ].join(" · ");
}

async function boot() {
  await loadMeta();
  await loadSettings();
  loadStock();
  loadRules();
}

/* ------------------------------------------------------------------ 库存 */
function dcChips(plan) {
  const entries = Object.entries(plan.stock || {});
  if (!entries.length) return '<span class="dc">暂无机房数据</span>';
  const order = ["1H-high", "1H-low", "72H", "comingSoon", "unknown", "unavailable"];
  entries.sort((a, b) => order.indexOf(a[1]) - order.indexOf(b[1]));
  return entries
    .map(([code, st]) => {
      const label = (plan.datacenters.find((d) => d.code === code) || {}).name || code.toUpperCase();
      return `<span class="dc st-${esc(st)}" title="${esc(STATE_TEXT[st] || st)}">${esc(label)} · ${esc(STATE_TEXT[st] || st)}</span>`;
    })
    .join("");
}

function planCard(plan) {
  const p = plan.price || {};
  const cpuLine = plan.cpu
    ? `${esc(plan.cpu)}${plan.cpu_cores ? ` · ${plan.cpu_cores} 核 ${plan.cpu_threads} 线程` : ""}${plan.cpu_clock ? ` · ${esc(plan.cpu_clock)}` : ""}`
    : "—";
  const stockBadge = plan.has_now
    ? '<span class="badge ok">有现货</span>'
    : plan.has_stock
    ? '<span class="badge warn">可预定</span>'
    : '<span class="badge bad">无货</span>';
  return `
  <div class="card" data-plan="${esc(plan.planCode)}">
    <div class="plan-head">
      <div>
        <div class="plan-title">
          <h3>${esc(plan.name)}</h3>
          <span class="badge ks">${esc(TIER_TEXT[plan.tier] || plan.tier)}</span>
          ${stockBadge}
        </div>
        <div class="plan-code">${esc(plan.planCode)}</div>
      </div>
      <div class="price">
        <strong>${money(p.monthly, plan.currency)}</strong>
        <span>每月${p.setup ? ` · 安装费 ${money(p.setup, plan.currency)}` : ""}</span>
      </div>
    </div>
    <div class="specs">
      <span><b>处理器</b>${cpuLine}</span>
      <span><b>内存</b>${esc(plan.ram_summary || "—")}</span>
      <span><b>硬盘</b>${esc(plan.storage_summary || "—")}</span>
    </div>
    <div class="dcs">${dcChips(plan)}</div>
    <div class="card-actions">
      <button class="btn small" data-act="order">下单</button>
      <button class="btn small" data-act="watch">设置监控</button>
      <button class="btn small ghost" data-act="configs">配置明细</button>
    </div>
  </div>`;
}

async function loadStock(force = false) {
  const list = $("#stock-list");
  list.innerHTML = '<div class="empty"><span class="spin"></span> 正在读取 OVH 公开目录与库存…</div>';
  const params = new URLSearchParams();
  if (force) params.set("refresh", "1");
  const q = $("#f-q").value.trim();
  if (q) params.set("q", q);
  const price = $("#f-price").value;
  if (price) params.set("max_price", price);
  if ($("#f-ks").checked) params.set("ks_only", "1");
  if ($("#f-stock").checked) params.set("in_stock", "1");
  const tier = $("#f-tier").value;
  if (tier) params.set("tier", tier);
  try {
    const data = await api(`/api/catalog?${params.toString()}`);
    state.plans = data.plans;
    state.currency = data.currency;
    $("#stock-updated").textContent = `更新于 ${new Date(data.updated * 1000).toLocaleTimeString()} · 共 ${data.total} 个机型，符合条件 ${data.shown} 个`;
    const total = data.plans.length;
    const nowCount = data.plans.filter((p) => p.has_now).length;
    const orderCount = data.plans.filter((p) => p.has_stock && !p.has_now).length;
    $("#stock-summary").innerHTML = `
      <div class="stat"><b>${total}</b><span>符合条件机型</span></div>
      <div class="stat"><b style="color:var(--ok)">${nowCount}</b><span>当前有现货</span></div>
      <div class="stat"><b style="color:var(--warn)">${orderCount}</b><span>可预定（72h）</span></div>
      <div class="stat"><b>${data.total}</b><span>目录总机型</span></div>`;
    list.innerHTML = data.plans.length
      ? data.plans.map(planCard).join("")
      : '<div class="empty">没有符合条件的机型，试试放宽筛选。</div>';
    const datalist = $("#plan-codes");
    datalist.innerHTML = data.plans
      .map((p) => `<option value="${esc(p.planCode)}">${esc(p.name)} · ${esc(p.cpu || "")}</option>`)
      .join("");
  } catch (err) {
    list.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

$("#btn-refresh").addEventListener("click", () => loadStock(true));
$("#f-q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadStock(); });
["#f-price", "#f-tier", "#f-ks", "#f-stock"].forEach((sel) =>
  $(sel).addEventListener("change", () => loadStock())
);

$("#stock-list").addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-act]");
  if (!btn) return;
  const code = btn.closest("[data-plan]").dataset.plan;
  const plan = state.plans.find((p) => p.planCode === code);
  if (!plan) return;
  if (btn.dataset.act === "order") openOrderModal(plan);
  if (btn.dataset.act === "watch") openRuleModal(plan);
  if (btn.dataset.act === "configs") openConfigModal(plan);
});

/* ------------------------------------------------------------------ 下单弹窗 */
function openOrderModal(plan) {
  const dcs = (plan.order_config.datacenters || []).map((dc) => {
    const st = plan.stock[dc] || "unknown";
    const name = (plan.datacenters.find((d) => d.code === dc) || {}).name || dc;
    return `<option value="${esc(dc)}">${esc(name)} · ${esc(STATE_TEXT[st] || st)}</option>`;
  }).join("");
  const osList = plan.order_config.os || [];
  const osOptions = osList.length
    ? osList.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("")
    : '<option value="none_64.en">none_64.en</option>';
  openModal(
    `下单：${plan.name}（${plan.planCode}）`,
    `
    <label>机房<select id="o-dc">${dcs}</select></label>
    <label>租期（月）<input type="number" id="o-months" value="1" min="1" max="12"></label>
    <label>安装系统<select id="o-os">${osOptions}</select></label>
    <label class="check"><input type="checkbox" id="o-autopay"> 使用账号默认付款方式自动支付（否则只生成待支付的付款链接）</label>
    <p class="muted small">参考月价 ${money(plan.price.monthly, plan.currency)}；实际金额以 OVH 结算页为准。购物车绑定与商品配置都会记录在「运行日志」。</p>
    <div id="o-result"></div>`,
    `<button class="btn" id="o-preview">预览结算</button>
     <button class="btn primary" id="o-confirm">确认下单</button>`
  );
  const payload = () => ({
    planCode: plan.planCode,
    datacenter: $("#o-dc").value,
    months: Number($("#o-months").value || 1),
    os: $("#o-os").value,
    auto_pay: $("#o-autopay").checked,
  });

  $("#o-preview").addEventListener("click", async () => {
    const box = $("#o-result");
    box.innerHTML = '<p class="muted"><span class="spin"></span> 正在创建购物车并计算金额…</p>';
    try {
      const data = await api("/api/orders", { method: "POST", body: { ...payload(), preview: true } });
      box.innerHTML = renderOrderResult(data);
    } catch (err) {
      box.innerHTML = `<p style="color:var(--bad)">${esc(err.message)}</p>`;
    }
  });

  $("#o-confirm").addEventListener("click", async () => {
    if (!confirm(`确认对 ${plan.planCode} @ ${$("#o-dc").value} 提交订单？`)) return;
    const box = $("#o-result");
    box.innerHTML = '<p class="muted"><span class="spin"></span> 正在下单…</p>';
    try {
      const data = await api("/api/orders", { method: "POST", body: { ...payload(), confirm: true } });
      box.innerHTML = renderOrderResult(data);
      toast(data.ok ? "下单完成" : "下单失败", data.ok ? "ok" : "err");
    } catch (err) {
      box.innerHTML = `<p style="color:var(--bad)">${esc(err.message)}</p>`;
    }
  });
}

function renderOrderResult(data) {
  const prices = data.prices || {};
  const withTax = prices.withTax ? (prices.withTax.value / 1e8).toFixed(2) : "";
  const withoutTax = prices.withoutTax ? (prices.withoutTax.value / 1e8).toFixed(2) : "";
  const steps = (data.steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const amount = withTax
    ? `<p><b>含税合计：${withTax} ${esc(prices.withTax.currencyCode || "")}</b>（不含税 ${withoutTax}）</p>`
    : "";
  const link = data.order_url
    ? `<p>付款链接：<a href="${esc(data.order_url)}" target="_blank" rel="noopener">${esc(data.order_url)}</a></p>`
    : "";
  const head = data.ok
    ? `<p style="color:var(--ok)"><b>${data.preview_only ? "结算预览完成" : `订单已生成：${esc(data.order_id === undefined ? "" : data.order_id)}`}</b></p>`
    : `<p style="color:var(--bad)"><b>失败：${esc(data.error || "未知错误")}</b></p>`;
  return `
    <div class="card" style="margin-top:12px">
      ${head}
      ${amount}
      ${link}
      ${data.status ? `<p>订单状态：${esc(data.status)}</p>` : ""}
      <ul class="muted small">${steps}</ul>
    </div>`;
}

/* ------------------------------------------------------------------ 配置明细 */
function openConfigModal(plan) {
  const rows = (plan.configs || [])
    .map((c) => {
      const stock = Object.entries(c.stock || {})
        .map(([dc, st]) => `${dc}:${STATE_TEXT[st] || st}`)
        .join("、");
      return `<tr><td>${esc(c.memory || "—")}</td><td>${esc(c.storage || "—")}</td><td style="font-size:11px">${esc(c.best ? STATE_TEXT[c.best] || c.best : "")}</td><td style="font-size:11px">${esc(stock)}</td></tr>`;
    })
    .join("");
  openModal(
    `${plan.name} 配置明细`,
    `<p class="muted small">同一机型下不同内存/硬盘组合的实时库存（来自 datacenter/availabilities 的 FQN 明细）。</p>
     <table class="config-table">
       <thead><tr><th>内存</th><th>硬盘</th><th>最佳状态</th><th>各机房</th></tr></thead>
       <tbody>${rows || '<tr><td colspan="4">暂无明细</td></tr>'}</tbody>
     </table>`
  );
}

/* ------------------------------------------------------------------ 规则 */
function openRuleModal(plan) {
  openModal(
    `为 ${plan.name} 添加监控`,
    `<label>机型代码<input id="rm-plan" value="${esc(plan.planCode)}"></label>
     <label>备注名称<input id="rm-name" value="${esc(plan.name)} 有货提醒"></label>
     <label>机房（逗号分隔，留空=全部）<input id="rm-dcs" value="${esc((plan.order_config.datacenters || []).join(","))}"></label>
     <label>触发条件
       <select id="rm-state">
         <option value="1H-low">现货即可</option>
         <option value="1H-high">只要现货充足</option>
         <option value="72H">72 小时内有货即可</option>
       </select></label>
     <label>轮询间隔（秒）<input type="number" id="rm-interval" value="180" min="30" step="30"></label>
     <label class="check"><input type="checkbox" id="rm-auto"> 有货自动下单</label>`,
    `<button class="btn primary" id="rm-save">保存规则</button>`
  );
  $("#rm-save").addEventListener("click", async () => {
    try {
      await api("/api/rules", {
        method: "POST",
        body: {
          planCode: $("#rm-plan").value.trim(),
          name: $("#rm-name").value.trim(),
          datacenters: $("#rm-dcs").value.split(",").map((s) => s.trim()).filter(Boolean),
          min_state: $("#rm-state").value,
          interval: Number($("#rm-interval").value || 180),
          auto_order: $("#rm-auto").checked,
        },
      });
      closeModal();
      toast("监控规则已保存", "ok");
      loadRules();
    } catch (err) {
      toast(err.message, "err");
    }
  });
}

$("#btn-add-rule").addEventListener("click", async () => {
  try {
    await api("/api/rules", {
      method: "POST",
      body: {
        planCode: $("#r-plan").value.trim(),
        name: $("#r-name").value.trim(),
        datacenters: $("#r-dcs").value.split(",").map((s) => s.trim()).filter(Boolean),
        min_state: $("#r-state").value,
        interval: Number($("#r-interval").value || 180),
        auto_order: $("#r-auto").checked,
        auto_pay: $("#r-autopay").checked,
        months: Number($("#r-months").value || 1),
      },
    });
    toast("监控规则已保存", "ok");
    $("#r-plan").value = "";
    $("#r-name").value = "";
    loadRules();
  } catch (err) {
    toast(err.message, "err");
  }
});

async function loadRules() {
  const box = $("#rules-list");
  try {
    const data = await api("/api/rules");
    state.rules = data.rules;
    if (!data.rules.length) {
      box.innerHTML = '<div class="empty">还没有监控规则。到「库存监控」里挑一个机型点「设置监控」即可。</div>';
      return;
    }
    box.innerHTML = data.rules
      .map((rule) => {
        const rt = rule.runtime || {};
        const stockText = Object.entries(rt.last_state || {})
          .map(([dc, st]) => `${dc}:${STATE_TEXT[st] || st}`)
          .join("、");
        return `
        <div class="card" data-rule="${esc(rule.id)}">
          <div class="plan-head">
            <div>
              <div class="plan-title">
                <h3>${esc(rule.name || rule.planCode)}</h3>
                <span class="badge ${rule.enabled ? "ok" : "bad"}">${rule.enabled ? "运行中" : "已暂停"}</span>
                ${rule.auto_order ? '<span class="badge warn">自动下单</span>' : ""}
              </div>
              <div class="plan-code">${esc(rule.planCode)} · 每 ${rule.interval} 秒 · 条件 ${esc(STATE_TEXT[rule.min_state] || rule.min_state)}</div>
            </div>
          </div>
          <div class="specs">
            <span><b>上次检查</b>${relTime(rt.last_check)} ${rt.last_error ? `· <span style="color:var(--bad)">${esc(rt.last_error)}</span>` : ""}</span>
            <span><b>命中次数</b>${rt.hits || 0} · 已下单 ${rt.orders || 0}</span>
            <span><b>最近库存</b>${esc(stockText || "—")}</span>
          </div>
          <div class="card-actions">
            <button class="btn small" data-act="toggle">${rule.enabled ? "暂停" : "启用"}</button>
            <button class="btn small ghost" data-act="check">立即检查</button>
            <button class="btn small danger" data-act="delete">删除</button>
          </div>
        </div>`;
      })
      .join("");
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

$("#rules-list").addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-act]");
  try {
    if (!btn) return;
    const id = btn.closest("[data-rule]").dataset.rule;
    if (btn.dataset.act === "toggle") {
      const rule = state.rules.find((r) => r.id === id);
      await api(`/api/rules/${id}`, { method: "PUT", body: { enabled: !rule.enabled } });
      loadRules();
    }
    if (btn.dataset.act === "check") {
      const rule = state.rules.find((r) => r.id === id);
      const data = await api(`/api/plans/${encodeURIComponent(rule.planCode)}/stock`);
      const lines = Object.entries(data.stock)
        .map(([dc, st]) => `${dc}：${STATE_TEXT[st] || st}`)
        .join("\n");
      openModal(`实时库存：${rule.planCode}`, `<pre style="white-space:pre-wrap">${esc(lines || "无数据")}</pre>`);
    }
    if (btn.dataset.act === "delete") {
      if (!confirm("确定删除这条监控规则？")) return;
      await api(`/api/rules/${id}`, { method: "DELETE" });
      loadRules();
    }
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ------------------------------------------------------------------ 订单 */
async function loadOrders() {
  const box = $("#orders-list");
  box.innerHTML = '<div class="empty"><span class="spin"></span> 正在读取订单…</div>';
  try {
    const data = await api("/api/orders");
    if (!data.orders || !data.orders.length) {
      box.innerHTML = `<div class="empty">${esc(data.message || "还没有订单记录")}</div>`;
      return;
    }
    box.innerHTML = data.orders
      .map((order) => `
        <div class="card">
          <div class="plan-head">
            <div>
              <div class="plan-title"><h3>订单 #${esc(order.orderId)}</h3><span class="badge">${esc(order.orderId ? "" : "")}</span></div>
              <div class="plan-code">${esc(order.date || "")} ${esc(order.description || "")}</div>
            </div>
            <div class="price"><strong>${order.prices && order.prices.withTax ? money(order.prices.withTax.value / 1e8, order.prices.withTax.currencyCode) : "—"}</strong><span>含税</span></div>
          </div>
          <div class="specs">
            <span><b>状态</b>${esc(order.status || order.state || "—")}</span>
            <span><b>付款状态</b>${esc(order.paymentStatus || "—")}</span>
          </div>
          ${order.url ? `<div class="card-actions"><a class="btn small" href="${esc(order.url)}" target="_blank" rel="noopener">打开付款链接</a></div>` : ""}
        </div>`)
      .join("");
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

$("#btn-load-orders").addEventListener("click", loadOrders);

/* ------------------------------------------------------------------ 机器 */
function serverCard(server) {
  const hw = server.hardware || {};
  const mem = hw.memory ? `${hw.memory.size ? hw.memory.size : ""} ${hw.memory.description || ""}`.trim() : "—";
  const disks = (hw.disk || [])
    .map((d) => `${d.numberOfDisks ? d.numberOfDisks + " × " : ""}${d.diskSize ? d.diskSize : ""}${d.diskType || ""}${d.raidTechnology ? ` (${d.raidTechnology})` : ""}`)
    .join("、");
  return `
  <div class="card" data-server="${esc(server.name)}">
    <div class="plan-head">
      <div>
        <div class="plan-title"><h3>${esc(server.name)}</h3><span class="badge ${server.state === "ok" ? "ok" : "warn"}">${esc(server.state || "未知")}</span></div>
        <div class="plan-code">${esc(server.datacenter || "")} · ${esc(server.reverse || "")}</div>
      </div>
      <div class="price"><strong style="font-size:14px">${esc(server.expiration || "—")}</strong><span>到期时间</span></div>
    </div>
    <div class="specs">
      <span><b>型号</b>${esc(hw.description || "—")}</span>
      <span><b>处理器</b>${esc(hw.processor || "—")}</span>
      <span><b>内存</b>${esc(mem || "—")}</span>
      <span><b>硬盘</b>${esc(disks || "—")}</span>
    </div>
    <div class="card-actions">
      <button class="btn small" data-act="detail">详情</button>
      <button class="btn small" data-act="tasks">任务</button>
      <button class="btn small" data-act="boots">启动项</button>
      <button class="btn small" data-act="rescue">救援模式</button>
      <button class="btn small" data-act="reinstall">重装系统</button>
      <button class="btn small danger" data-act="reboot">重启</button>
    </div>
  </div>`;
}

async function loadServers() {
  const box = $("#servers-list");
  box.innerHTML = '<div class="empty"><span class="spin"></span> 正在读取服务器列表…</div>';
  try {
    const data = await api("/api/servers");
    state.servers = data.servers || [];
    box.innerHTML = state.servers.length
      ? state.servers.map(serverCard).join("")
      : '<div class="empty">该账号下没有独立服务器，或 API 密钥权限不足（需要 /dedicated/server 的 GET 权限）。</div>';
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

$("#btn-load-servers").addEventListener("click", loadServers);

$("#servers-list").addEventListener("click", async (event) => {
  const btn = event.target.closest("button[data-act]");
  if (!btn) return;
  const name = btn.closest("[data-server]").dataset.server;
  const act = btn.dataset.act;
  try {
    if (act === "reboot") {
      if (!confirm(`确定重启 ${name}？`)) return;
      const data = await api(`/api/servers/${name}/reboot`, { method: "POST", body: {} });
      toast(`重启指令已发送（任务 ${data.task && data.task.taskId ? data.task.taskId : ""}）`, "ok");
    }
    if (act === "detail") {
      const data = await api(`/api/servers/${name}`);
      const ips = (data.ips || []).map((i) => i.ip || i.ipAddress).join("、");
      const hw = data.hardware || {};
      openModal(`服务器详情：${name}`, `
        <div class="specs">
          <span><b>型号</b>${esc(hw.description || "—")}</span>
          <span><b>处理器</b>${esc(hw.processor || "—")}</span>
          <span><b>内存</b>${esc((hw.memorySize && (hw.memorySize.description || hw.memorySize.size)) || "—")}</span>
          <span><b>IP</b>${esc(ips || "—")}</span>
          <span><b>机房</b>${esc((data.detail && data.detail.datacenter) || "—")}</span>
          <span><b>到期</b>${esc((data.serviceInfos && data.serviceInfos.expiration) || "—")}</span>
          <span><b>续费方式</b>${esc((data.serviceInfos && data.serviceInfos.renewalType) || "—")}</span>
          <span><b>启动项 ID</b>${esc(data.detail && data.detail.bootId !== undefined ? data.detail.bootId : "—")}</span>
        </div>
        <pre style="white-space:pre-wrap;font-size:11px;color:var(--muted)">${esc(JSON.stringify(hw, null, 1)).slice(0, 4000)}</pre>`);
    }
    if (act === "tasks") {
      const data = await api(`/api/servers/${name}/tasks`);
      const rows = (data.tasks || [])
        .map((t) => `<tr><td>${esc(t.taskId)}</td><td>${esc(t.function)}</td><td>${esc(t.status)}</td><td>${esc(t.startDate || "")}</td><td>${esc(t.comment || "")}</td></tr>`)
        .join("");
      openModal(`${name} 的任务`, `<table class="config-table"><thead><tr><th>ID</th><th>功能</th><th>状态</th><th>开始</th><th>备注</th></tr></thead><tbody>${rows || '<tr><td colspan="5">暂无任务</td></tr>'}</tbody></table>`);
    }
    if (act === "boots") {
      const data = await api(`/api/servers/${name}/boots`);
      const rows = (data.boots || [])
        .map((b) => `<tr><td>${esc(b.bootId)}</td><td>${esc(b.bootType)}</td><td>${esc(b.kernel || "")}</td><td><button class="btn small" data-boot="${esc(b.bootId)}">设为启动项并重启</button></td></tr>`)
        .join("");
      openModal(`${name} 启动项`, `<table class="config-table"><thead><tr><th>ID</th><th>类型</th><th>内核</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="4">暂无启动项</td></tr>'}</tbody></table>`);
      $$("#modal-body button[data-boot]").forEach((el) =>
        el.addEventListener("click", async () => {
          try {
            await api(`/api/servers/${name}/boot`, { method: "POST", body: { bootId: Number(el.dataset.boot), reboot: true } });
            toast("启动项已切换并重启", "ok");
            closeModal();
          } catch (err) {
            toast(err.message, "err");
          }
        })
      );
    }
    if (act === "rescue") {
      if (!confirm(`切换到救援模式并重启 ${name}？`)) return;
      const data = await api(`/api/servers/${name}/rescue`, { method: "POST", body: { enable: true, reboot: true } });
      toast(`已切到救援模式（bootId ${data.bootId}），重启中`, "ok");
    }
    if (act === "reinstall") {
      const data = await api(`/api/servers/${name}/templates`);
      const templates = data.templates || [];
      openModal(`${name} 重装系统`, `
        <label>系统模板<select id="i-template">${templates.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("")}</select></label>
        <label>分区方案<select id="i-scheme"><option value="">先选择模板</option></select></label>
        <label>主机名（可选）<input id="i-host" placeholder="例如 my-server"></label>
        <label class="check"><input type="checkbox" id="i-confirm"> 我知道这会清空磁盘数据</label>`,
        `<button class="btn primary" id="i-start">开始重装</button>`);
      const loadSchemes = async () => {
        const tpl = $("#i-template").value;
        if (!tpl) return;
        try {
          const res = await api(`/api/servers/${name}/partitions?template=${encodeURIComponent(tpl)}`);
          $("#i-scheme").innerHTML = (res.schemes || [])
            .map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`)
            .join("");
        } catch (err) {
          $("#i-scheme").innerHTML = `<option value="">${esc(err.message)}</option>`;
        }
      };
      $("#i-template").addEventListener("change", loadSchemes);
      loadSchemes();
      $("#i-start").addEventListener("click", async () => {
        if (!$("#i-confirm").checked) return toast("请先勾选确认", "err");
        try {
          await api(`/api/servers/${name}/install`, {
            method: "POST",
            body: {
              template: $("#i-template").value,
              scheme: $("#i-scheme").value,
              hostname: $("#i-host").value,
              confirm: true,
            },
          });
          toast("已开始重装，进度看「任务」", "ok");
          closeModal();
        } catch (err) {
          toast(err.message, "err");
        }
      });
    }
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ------------------------------------------------------------------ 设置 */
$("#btn-save-settings").addEventListener("click", async () => {
  const body = {
    endpoint: $("#s-endpoint").value,
    subsidiary: $("#s-subsidiary").value,
    telegram_chat_id: $("#s-tg-chat").value.trim(),
    default_interval: Number($("#s-interval").value || 300),
  };
  const ak = $("#s-ak").value.trim();
  const as = $("#s-as").value.trim();
  const ck = $("#s-ck").value.trim();
  const token = $("#s-tg-token").value.trim();
  const password = $("#s-password").value;
  if (ak) body.application_key = ak;
  if (as) body.application_secret = as;
  if (ck) body.consumer_key = ck;
  if (token) body.telegram_bot_token = token;
  if (password) body.panel_password = password;
  try {
    await api("/api/settings", { method: "PUT", body });
    toast("设置已保存", "ok");
    $("#s-password").value = "";
    await loadSettings();
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#btn-test-tg").addEventListener("click", async () => {
  try {
    const data = await api("/api/telegram/test", { method: "POST", body: {} });
    toast(data.message || "已发送", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#btn-check-account").addEventListener("click", async () => {
  try {
    const data = await api("/api/account");
    if (!data.ok) return toast(data.message, "err");
    const me = data.me || {};
    toast(`账号可用：${me.nichandle || me.customerCode || ""} ${me.email ? "· " + me.email : ""}`, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("#btn-logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: {} });
  location.reload();
});

/* ------------------------------------------------------------------ 日志 */
async function loadLogs() {
  const box = $("#logs-list");
  try {
    const data = await api("/api/logs?limit=150");
    box.innerHTML = (data.logs || [])
      .map((l) => `<div class="log-item"><span class="t">${esc(l.time)}</span><span class="lv ${esc(l.level)}">${esc(l.level)}</span><span>${esc(l.message)}</span></div>`)
      .join("") || '<div class="empty">暂无日志</div>';
  } catch (err) {
    box.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

$("#btn-logs").addEventListener("click", loadLogs);

/* ------------------------------------------------------------------ 启动 */
(async function start() {
  try {
    await boot();
  } catch (err) {
    if (err.message !== "请先登录面板") toast(err.message, "err");
  }
  setInterval(() => {
    if ($("#tab-stock").classList.contains("active") && !document.hidden) loadStock();
  }, 120000);
})();
