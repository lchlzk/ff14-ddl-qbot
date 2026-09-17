"use strict";

const state = {
  csrf: "", view: "overview", page: 1, overview: null,
  bot: "", bots: [], botStatus: null,
  queries: { groups: "", roles: "", learning: "" },
  filters: {
    learningScope: "", learningStatus: "all", learningOrder: "recent",
    galleryMode: "all", galleryCategory: "", galleryScope: "",
    feedbackState: "open",
  },
};
const titles = {
  overview: ["SYSTEM OVERVIEW", "运行概览"],
  bots: ["BOT REGISTRY", "机器人管理"],
  groups: ["GROUP CONTROL", "群设置"],
  roles: ["AI ROLE CONTROL", "AI 角色"],
  learning: ["LEARNING LIBRARY", "学习词库"],
  gallery: ["MEDIA STORAGE", "图库"],
  feedback: ["PRIVATE INBOX", "反馈箱"],
  audit: ["SECURITY JOURNAL", "操作记录"],
};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let renderController = null;

function h(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

function when(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false,
  }).format(new Date(Number(value) * 1000));
}

function duration(seconds) {
  const day = Math.floor(seconds / 86400);
  const hour = Math.floor(seconds % 86400 / 3600);
  const minute = Math.floor(seconds % 3600 / 60);
  return day ? `${day}天 ${hour}小时` : hour ? `${hour}小时 ${minute}分钟` : `${minute}分钟`;
}

function toast(message, error = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 3600);
}

function ask(title, note) {
  const dialog = $("#confirmDialog");
  $("#confirmTitle").textContent = title;
  $("#confirmNote").textContent = note;
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    if (state.csrf) headers["X-Admin-CSRF"] = state.csrf;
  }
  const signal = options.signal || (method === "GET" ? renderController?.signal : undefined);
  const response = await fetch(path, { ...options, signal, method, headers, credentials: "same-origin" });
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* server returned no JSON */ }
  signal?.throwIfAborted();
  if (response.status === 401) {
    showLogin();
    throw new Error("登录已失效，请重新登录。", { cause: "auth" });
  }
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload?.data;
}

function scoped(path) {
  const url = new URL(path, location.origin);
  if (state.bot) url.searchParams.set("bot", state.bot);
  return url.pathname + url.search;
}

async function loadBots() {
  const data = await api("/admin/api/bots");
  state.botStatus = data;
  state.bots = data.items;
  if (!state.bots.some((item) => item.app_id === state.bot)) {
    state.bot = data.active.find((id) => state.bots.some((item) => item.app_id === id))
      || state.bots[0]?.app_id || "";
  }
  const select = $("#botSelect");
  select.replaceChildren();
  if (!state.bots.length) select.append(new Option("尚未配置", ""));
  state.bots.forEach((item) => {
    const labels = {
      connected: "已连接", connecting: "连接中", listening: "Webhook 监听中",
      disabled: "已停用", pending: "待应用", error: "连接异常",
    };
    const suffix = ` · ${labels[item.state] || "状态未知"}`;
    select.append(new Option(`${item.label}${suffix}`, item.app_id));
  });
  select.value = state.bot;
  select.disabled = state.bots.length === 0;
}

function showLogin() {
  state.csrf = "";
  $("#appShell").hidden = true;
  $("#loginShell").hidden = false;
  $("#loginPassword").value = "";
  $("#loginUsername").focus();
}

function showApp() {
  $("#loginShell").hidden = true;
  $("#appShell").hidden = false;
}

function setHeader(view) {
  $("#viewEyebrow").textContent = titles[view][0];
  $("#viewTitle").textContent = titles[view][1];
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
}

function loading() {
  $("#content").innerHTML = '<div class="card-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
}

function empty(title, note) {
  return `<div class="empty"><b>${h(title)}</b>${h(note)}</div>`;
}

function searchBox(view, placeholder) {
  return `<form class="search-form" data-search-view="${view}">
    <label class="sr-only" for="${view}Search">搜索</label>
    <input id="${view}Search" name="q" type="search" maxlength="80" value="${h(state.queries[view])}" placeholder="${h(placeholder)}">
    <button class="button good" type="submit">搜索</button>
    ${state.queries[view] ? `<button class="button" type="button" data-clear-search="${view}">清除</button>` : ""}
  </form>`;
}

function pager(data) {
  const last = Math.max(1, Math.ceil(data.total / data.page_size));
  if (last === 1) return "";
  return `<div class="pager">
    <button class="button" data-page="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${data.page} / ${last} 页 · 共 ${data.total} 项</span>
    <button class="button" data-page="${data.page + 1}" ${data.page >= last ? "disabled" : ""}>下一页</button>
  </div>`;
}

function auditLines(rows) {
  if (!rows.length) return '<div class="empty"><b>还没有后台操作</b>登录之后的修改会记录在这里。</div>';
  return `<div class="audit-list">${rows.map((row) => `<div class="audit-line">
    <i></i><div><b>${h(row.action)}</b><span>${h(row.target)}</span></div><time>${when(row.created)}</time>
  </div>`).join("")}</div>`;
}

function showHealth(data) {
  state.overview = data;
  const health = $("#healthPill");
  health.className = `health-pill ${data.health === "ok" ? "ok" : "bad"}`;
  health.querySelector("span").textContent = data.health === "ok" ? "运行正常" : "需要检查";
  const badge = $("#feedbackBadge");
  badge.hidden = data.feedback_open === 0;
}

async function renderOverview() {
  const data = await api(scoped("/admin/api/overview"));
  showHealth(data);
  $("#content").innerHTML = `
    <section class="metric-grid">
      <article class="metric"><span class="metric-label">持续运行</span><strong class="metric-value">${duration(data.uptime_seconds)}</strong><span class="metric-note">应用进程在线</span></article>
      <article class="metric"><span class="metric-label">已识别群聊</span><strong class="metric-value">${data.groups}</strong><span class="metric-note">按隐私短标识管理</span></article>
      <article class="metric"><span class="metric-label">AI 角色 / 群授权</span><strong class="metric-value">${data.roles} / ${data.grants}</strong><span class="metric-note">密钥永不在网页展示</span></article>
      <article class="metric"><span class="metric-label">待处理反馈</span><strong class="metric-value">${data.feedback_open}</strong><span class="metric-note">仅总管理员可见</span></article>
      <article class="metric"><span class="metric-label">图库</span><strong class="metric-value">${data.gallery_count} 张</strong><span class="metric-note">${h(data.gallery_size)} · ${data.local_galleries} 个私库</span></article>
      <article class="metric"><span class="metric-label">学习回复</span><strong class="metric-value">${data.learning_pairs}</strong><span class="metric-note">当前有效配对</span></article>
      <article class="metric"><span class="metric-label">数据库占用</span><strong class="metric-value">${h(data.database_size)}</strong><span class="metric-note">SQLite / WAL 合计</span></article>
      <article class="metric"><span class="metric-label">网页登录</span><strong class="metric-value">${data.active_sessions}</strong><span class="metric-note">当前有效会话</span></article>
    </section>
    <section class="split-grid">
      <article class="panel">
        <div class="panel-head"><div><h2>核心服务</h2><p>危险开关会立即作用于机器人</p></div></div>
        <div class="panel-body">
          <div class="status-row"><div><strong>SQLite 数据库</strong><small>完整性检查每天执行，不随翻页重复扫描</small></div><span class="tag ${data.health === "ok" ? "good" : "bad"}">${!data.integrity_checked ? "等待检查" : data.health === "ok" ? "正常" : "异常"}</span></div>
          <div class="status-row"><div><strong>AI 角色聊天</strong><small>关闭后所有群和私聊角色暂停，配置命令仍可用</small></div><button class="switch ${data.ai_enabled ? "on" : ""}" id="aiGlobalSwitch" aria-label="切换全局 AI" aria-pressed="${data.ai_enabled}"></button></div>
          <div class="status-row"><div><strong>本地图库</strong><small>原图独立存储，公共库不设张数上限，不自动删图</small></div><span class="tag ${data.disk_warning ? "bad" : "good"}">${data.disk_warning ? "磁盘空间不足 · " : "磁盘剩余 · "}${h(data.disk_free)}</span></div>
        </div>
      </article>
      <article class="panel">
        <div class="panel-head"><div><h2>最近操作</h2><p>只记录后台修改，不记录聊天内容</p></div></div>
        <div class="panel-body">${auditLines(data.audit)}</div>
      </article>
    </section>`;
}

async function toggleAi() {
  const enabled = !state.overview.ai_enabled;
  const action = enabled ? "恢复全部 AI 角色" : "暂停全部 AI 角色";
  if (!await ask(`${action}？`, "暂停会让正在生成的回复失效，但不会删除角色和群授权。")) return;
  await api(scoped("/admin/api/ai-global"), { method: "POST", body: JSON.stringify({ enabled }) });
  toast(`${action}已生效`);
  await renderOverview();
}

function botStatusTag(bot) {
  if (!bot.enabled) return '<span class="tag">已停用</span>';
  if (bot.state === "connected") return '<span class="tag good">已连接</span>';
  if (bot.state === "listening") return '<span class="tag good">Webhook 监听中</span>';
  if (bot.state === "connecting") return '<span class="tag warn">正在连接</span>';
  if (bot.state === "error") return '<span class="tag bad">连接异常</span>';
  return '<span class="tag warn">等待应用</span>';
}

function botForm(bot = null) {
  const exists = Boolean(bot);
  const item = bot || {
    app_id: "", label: "", connection: "websocket", enabled: true,
    c2c_group_at_messages: true, at_messages: true,
  };
  return `<article class="data-card bot-card ${exists && !item.enabled ? "disabled" : ""}">
    <div class="data-card-head"><div><h2>${exists ? h(item.label) : "添加机器人"}</h2><p class="mono">${exists ? h(item.app_id) : "QQ 开放平台凭据"}</p></div>${exists ? botStatusTag(item) : '<span class="tag good">最多 20 个</span>'}</div>
    ${exists ? `<div class="bot-facts"><span>已识别群聊 <b>${item.groups}</b></span><span>凭据 <b>${item.secret_configured ? "已加密保存" : "未配置"}</b></span></div>` : ""}
    <form class="bot-form" data-existing="${exists ? "true" : "false"}">
      <div class="form-grid">
        <div class="form-field"><label>显示名称</label><input name="label" maxlength="40" value="${h(item.label)}" placeholder="例如：群 A 机器人"></div>
        <div class="form-field"><label>AppID</label><input name="app_id" maxlength="128" value="${h(item.app_id)}" ${exists ? "readonly" : "required"} autocomplete="off" placeholder="QQ 开放平台 AppID"></div>
        <div class="form-field full"><label>AppSecret</label><input name="secret" type="password" maxlength="512" ${exists ? "" : "required"} autocomplete="new-password" placeholder="${exists ? "留空保持现有密钥" : "只在保存时提交，页面不会再次显示"}"><small>${exists ? "如需轮换密钥，在此填写新的 AppSecret；留空不会覆盖。" : "保存后使用独立主密钥加密，网页 API 不会返回明文。"}</small></div>
        <div class="form-field"><label>连接方式</label><select name="connection"><option value="websocket" ${item.connection === "websocket" ? "selected" : ""}>WebSocket</option><option value="webhook" ${item.connection === "webhook" ? "selected" : ""}>Webhook</option></select></div>
        <div class="form-field bot-switches"><label>运行设置</label><div class="check-row"><label><input name="enabled" type="checkbox" ${item.enabled ? "checked" : ""}>启用</label><label><input name="c2c_group_at_messages" type="checkbox" ${item.c2c_group_at_messages ? "checked" : ""}>单聊/群 @ 事件</label><label><input name="at_messages" type="checkbox" ${item.at_messages ? "checked" : ""}>@ 消息事件</label></div></div>
      </div>
      <div class="card-actions"><button class="button good" type="submit">${exists ? "保存机器人" : "添加机器人"}</button>${exists ? `<button class="button danger" type="button" data-bot-delete="${h(item.app_id)}">删除凭据</button>` : ""}</div>
    </form>
  </article>`;
}

async function renderBots() {
  await loadBots();
  const status = state.botStatus;
  const warnings = `${status.restart_required ? '<div class="restart-banner"><b>有设置尚未应用</b><span>通常重新保存一次即可立即应用；只有热加载异常时才需要重启进程。</span></div>' : ""}${status.legacy_plaintext ? '<div class="restart-banner danger"><b>仍检测到旧版明文环境变量</b><span>确认后台中的机器人已经上线，再从 .env 删除 QQ_APP_ID、QQ_APP_SECRET 或 QQ_BOTS。</span></div>' : ""}`;
  const cards = status.items.map((item) => botForm(item)).join("");
  $("#content").innerHTML = `${warnings}<article class="panel bot-intro"><div class="panel-body"><b>多机器人共用一个后台</b><p>新增、停用和修改凭据会立即连接、断开或重连，不会中断其他机器人。上方“当前机器人”决定群设置、AI 角色、学习词库和反馈箱的范围；公共图库与蜡笔板用户进度跨机器人共享。</p><p class="secret-note">AppSecret 只会加密写入数据目录；请把 <code>data/secrets/qq-bots-master.key</code> 与数据库一起备份，并限制文件权限。</p></div></article><div class="bot-grid">${cards}${botForm()}</div>`;
}

async function saveBot(form) {
  const body = {
    app_id: form.elements.app_id.value.trim(),
    label: form.elements.label.value.trim(),
    secret: form.elements.secret.value,
    connection: form.elements.connection.value,
    enabled: form.elements.enabled.checked,
    c2c_group_at_messages: form.elements.c2c_group_at_messages.checked,
    at_messages: form.elements.at_messages.checked,
  };
  const result = await api("/admin/api/bots", { method: "POST", body: JSON.stringify(body) });
  state.bot = result.app_id;
  const active = result.state === "connected" || result.state === "listening";
  toast(!body.enabled ? "机器人已停用并断开连接"
    : result.created
      ? active ? "机器人已添加并生效" : "机器人已添加，正在连接"
      : active ? "机器人设置已立即生效" : "机器人设置已保存，正在连接");
  await renderBots();
  if (body.enabled && result.state === "connecting") {
    setTimeout(() => {
      if (state.view === "bots") renderBots().catch((error) => toast(error.message, true));
    }, 2500);
  }
}

async function deleteBot(appId) {
  if (!await ask(`删除机器人 ${appId} 的凭据？`, "机器人会立即断开；群设置和业务数据仍会保留。")) return;
  await api(`/admin/api/bots/${encodeURIComponent(appId)}/delete`, { method: "POST", body: "{}" });
  if (state.bot === appId) state.bot = "";
  toast("机器人凭据已删除并断开连接");
  await renderBots();
}

function commandChecks(group, commands) {
  const disabled = new Set(group.disabled);
  return commands.map((name) => `<label><input type="checkbox" name="disabled" value="${h(name)}" ${disabled.has(name) ? "checked" : ""}>/${h(name)}</label>`).join("");
}

function pluginSwitches(group) {
  const disabled = new Set(group.disabled);
  const plugins = [
    { command: "ff14", label: "FF14", note: "市场、战绩、天气等整套 FF14 命令" },
    { command: "tr", label: "嘟嘟脸", note: "角色、图鉴、蜡笔板和网页入口" },
    { command: "bili", label: "B站推送", note: "本群关注的 UP 主直播与动态通知" },
  ];
  return `<section class="plugin-switch-panel full">
    <div class="plugin-switch-head"><div><h3>插件总开关</h3><p>只对当前机器人在本群生效，点击立即切换，无需重启。</p></div><span>一键管理</span></div>
    <div class="plugin-switch-grid">${plugins.map((plugin) => {
      const enabled = !disabled.has(plugin.command);
      return `<button class="plugin-quick-toggle ${enabled ? "is-on" : "is-off"}" type="button" data-plugin-toggle="${plugin.command}" data-plugin-label="${plugin.label}" data-plugin-enabled="${enabled}" aria-pressed="${enabled}">
        <span><b>${plugin.label} 插件</b><small>${plugin.note}</small></span><em>${enabled ? "已开启" : "已关闭"}</em>
      </button>`;
    }).join("")}</div>
  </section>`;
}

async function renderGroups() {
  const query = new URLSearchParams({ page: state.page, q: state.queries.groups });
  const data = await api(scoped(`/admin/api/groups?${query}`));
  const toolbar = `<div class="toolbar search-toolbar">${searchBox("groups", "搜索群名、短标识或插件设置")}<span class="tag">${data.total} 个群</span></div>`;
  const cards = data.items.length ? `<div class="card-grid">${data.items.map((group) => `<article class="data-card group-card" data-scope="${h(group.scope)}">
    <div class="data-card-head"><div><h2>${group.name ? h(group.name) : `群 ${h(group.tag)}`}</h2><p class="mono">${group.name ? `群 ${h(group.tag)} · ${group.manual_name ? "后台命名" : "QQ 群名"}` : "隐私短标识 · 暂未取得群名"}</p></div><span class="tag ${group.gallery_mode === "local" ? "warn" : "good"}">${group.gallery_mode === "local" ? "本群图库" : "公共图库"}</span></div>
    <div class="data-lines">
      <div>AI 角色<br><b>${group.roles} 个</b></div><div>群聊学习<br><b>${group.learning.enabled ? "开启" : "关闭"} · ${group.learning.pairs} 组</b></div>
      <div>关键词<br><b>${group.custom_replies} 条</b></div>${(group.plugin_details || []).map((item) => `<div>${h(item.label)}<br><b>${h(item.value)}</b></div>`).join("")}
      <div>私库容量<br><b>${group.gallery_count} 张 · ${h(group.gallery_size)}</b></div><div>已关闭功能<br><b>${group.disabled.length} 项</b></div>
    </div>
    <form class="group-form">
      <div class="form-grid">
        ${pluginSwitches(group)}
        <div class="form-field full"><label>群显示名称</label><input name="display_name" maxlength="40" value="${h(group.manual_name)}" placeholder="${h(group.official_name || "例如：轻零、0神信徒、爱★喝冻奶茶")}"><small>${group.official_name ? `QQ 自动取得：${h(group.official_name)}。留空使用官方群名。` : group.name_error ? `${h(group.name_error)}，可以在这里手动填写。` : "机器人收到下一条群消息后会尝试从腾讯同步；未开放接口时可手动填写。"}</small></div>
        ${(group.plugin_fields || []).map((field) => `<div class="form-field"><label>${h(field.label)}</label><input data-plugin-field name="${h(field.name)}" maxlength="${h(field.max_length)}" value="${h(field.value)}" placeholder="${h(field.placeholder || "")}"></div>`).join("")}
        <div class="form-field"><label>工具箱每日额度</label><input name="quota" type="number" min="1" max="10000" value="${group.quota}"></div>
        <div class="form-field"><label>图库模式</label><select name="gallery_mode"><option value="public" ${group.gallery_mode === "public" ? "selected" : ""}>公共图库</option><option value="local" ${group.gallery_mode === "local" ? "selected" : ""}>本群图库（最多100张）</option></select></div>
        <div class="form-field"><label>学习库范围</label><select name="library_mode"><option value="public" ${group.learning.library_mode === "public" ? "selected" : ""}>公开学习库（默认）</option><option value="local" ${group.learning.library_mode === "local" ? "selected" : ""}>本群私有学习库</option></select><small>公开库会和其他公开群共享问答；私有库只在本群学习和回复。</small></div>
        <section class="learning-panel full ${group.learning.enabled ? "is-on" : "is-off"}" data-learning-panel>
          <div class="learning-head"><div><h3>群聊学习</h3><p>把相邻发言学成“问句 → 回答”，达到回复阈值后才参与回复。</p></div><label class="toggle-control"><input name="learning_enabled" type="checkbox" ${group.learning.enabled ? "checked" : ""}><span class="toggle-track"></span><b data-learning-status>${group.learning.enabled ? "已开启" : "已关闭"}</b></label></div>
          <div class="learning-settings" data-learning-settings>
            <label class="learning-setting"><span><b>回复阈值</b><small>同一回答至少学到几次才可使用；越高越谨慎。</small></span><input name="answer_threshold" type="number" min="1" max="10" value="${group.learning.answer_threshold}"></label>
            <label class="learning-setting"><span><b>复读阈值</b><small>至少两人发送相同消息，达到该条数后触发判断；0 关闭全部复读响应。</small></span><input name="repeat_threshold" type="number" min="0" max="10" value="${group.learning.repeat_threshold}"></label>
            <label class="learning-setting checkbox-setting"><span><b>学习图片</b><small>允许把合规 QQ 图片作为回答；每群最多 100 张、100 MiB。</small></span><input name="image_enabled" type="checkbox" ${group.learning.image_enabled ? "checked" : ""}></label>
            <label class="learning-setting checkbox-setting"><span><b>自动复读</b><small>达到复读阈值且未触发打断时，是否跟读原消息。</small></span><input name="repeat_enabled" type="checkbox" ${group.learning.repeat_enabled ? "checked" : ""}></label>
            <label class="learning-setting"><span><b>复读概率（%）</b><small>未触发打断时，跟读原消息的概率。</small></span><input name="repeat_probability" type="number" min="0" max="100" value="${group.learning.repeat_probability}"></label>
            <label class="learning-setting checkbox-setting"><span><b>打断复读</b><small>达到复读阈值时，允许发送“打断复读！”。</small></span><input name="interrupt_repeat_enabled" type="checkbox" ${group.learning.interrupt_repeat_enabled ? "checked" : ""}></label>
            <label class="learning-setting"><span><b>打断概率（%）</b><small>优先判断；未打断时再判断普通复读。</small></span><input name="interrupt_repeat_probability" type="number" min="0" max="100" value="${group.learning.interrupt_repeat_probability}"></label>
          </div>
          <p class="privacy-warning">${group.learning.library_mode === "public" ? "公开模式：本群学到的内容可能在其他公开群回复。" : "私有模式：学习内容不会跨群使用。"}</p>
        </section>
      </div>
      <details><summary>管理本群命令开关（勾选代表关闭）</summary><div class="command-grid">${commandChecks(group, data.managed_commands)}</div></details>
      <div class="card-actions"><button class="button good" type="submit">保存本群设置</button><button class="button" type="button" data-open-learning="${h(group.scope)}">查看本群学习词库（${group.learning.pairs}）</button></div>
    </form>
  </article>`).join("")}</div>` : empty(state.queries.groups ? "没有匹配的群" : "还没有可管理的群", state.queries.groups ? "换一个群名、短标识或小区再试。" : "机器人在群内收到管理命令、AI 授权或学习设置后，会显示在这里。");
  $("#content").innerHTML = toolbar + cards + pager(data);
}

async function saveGroup(form) {
  const card = form.closest(".group-card");
  const disabled = [...form.querySelectorAll('input[name="disabled"]:checked')].map((input) => input.value);
  const body = {
    display_name: form.elements.display_name.value,
    quota: Number(form.elements.quota.value),
    gallery_mode: form.elements.gallery_mode.value,
    disabled,
    learning: {
      enabled: form.elements.learning_enabled.checked,
      answer_threshold: Number(form.elements.answer_threshold.value),
      repeat_threshold: Number(form.elements.repeat_threshold.value),
      repeat_enabled: form.elements.repeat_enabled.checked,
      repeat_probability: Number(form.elements.repeat_probability.value),
      interrupt_repeat_enabled: form.elements.interrupt_repeat_enabled.checked,
      interrupt_repeat_probability: Number(form.elements.interrupt_repeat_probability.value),
      image_enabled: form.elements.image_enabled.checked,
      library_mode: form.elements.library_mode.value,
    },
  };
  for (const input of form.querySelectorAll("[data-plugin-field]")) body[input.name] = input.value;
  await api(scoped(`/admin/api/groups/${card.dataset.scope}`), { method: "POST", body: JSON.stringify(body) });
  toast("群设置已保存");
  await renderGroups();
}

async function toggleGroupPlugin(button) {
  const card = button.closest(".group-card");
  const command = button.dataset.pluginToggle;
  const label = button.dataset.pluginLabel || `/${command}`;
  const nextEnabled = button.dataset.pluginEnabled !== "true";
  button.disabled = true;
  try {
    await api(scoped(`/admin/api/groups/${card.dataset.scope}/plugin`), {
      method: "POST",
      body: JSON.stringify({ command, enabled: nextEnabled }),
    });
    toast(`${label} 插件已${nextEnabled ? "开启" : "关闭"}，立即生效`);
    await renderGroups();
  } finally {
    button.disabled = false;
  }
}

function collectText(grant) {
  if (grant.collect_min === 0 && grant.collect_max === 0) return "每条触发消息";
  if (grant.collect_min === grant.collect_max) return `收集 ${grant.collect_min} 条`;
  return `收集 ${grant.collect_min}～${grant.collect_max} 条`;
}

async function renderRoles() {
  const query = new URLSearchParams({ page: state.page, q: state.queries.roles });
  const data = await api(scoped(`/admin/api/roles?${query}`));
  const roleTable = data.items.length ? `<div class="panel table-wrap"><table><thead><tr><th>角色</th><th>模型</th><th>配置</th><th>每日总额度</th><th>加入群</th><th>操作</th></tr></thead><tbody>${data.items.map((role) => `<tr>
    <td><b>${h(role.name)}</b><small class="mono">${h(role.id)}</small></td><td>${h(role.model)}<small>${h(role.provider)} / ${h(role.region)}</small></td>
    <td><span class="tag ${role.configured ? "good" : "warn"}">${role.configured ? "密钥已配置" : "未完成"}</span></td><td>${role.daily} 次</td><td>${role.groups} 个</td><td><button class="button danger" data-role-delete="${h(role.id)}" data-role-name="${h(role.name)}">删除角色</button></td>
  </tr>`).join("")}</tbody></table></div>` : empty("没有 AI 角色", "用户私聊创建角色后会显示在这里，API Key 和人设不会展示。 ");
  const grants = data.grants.length ? `<article class="panel"><div class="panel-head"><div><h2>群授权</h2><p>共 ${data.grant_total} 项，每页 ${data.page_size} 项</p></div></div><div class="table-wrap"><table><thead><tr><th>角色 / 授权</th><th>群</th><th>触发</th><th>日限额</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.grants.map((grant) => `<tr>
    <td><b>${h(grant.name)}</b><small class="mono">${h(grant.id)}</small></td><td>${h(grant.group_name || grant.group_tag)}${grant.group_name ? `<small class="mono">${h(grant.group_tag)}</small>` : ""}</td><td>${grant.mention_only ? "需提到名字" : "无需点名"}<small>${collectText(grant)}</small></td>
    <td>${grant.cap} 次</td><td><span class="tag ${grant.paused ? "warn" : "good"}">${grant.paused ? "已暂停" : "运行中"}</span></td>
    <td><div class="card-actions"><button class="button ${grant.paused ? "good" : "warning"}" data-grant="${h(grant.id)}" data-grant-action="${grant.paused ? "resume" : "pause"}">${grant.paused ? "恢复" : "暂停"}</button><button class="button" data-grant="${h(grant.id)}" data-grant-action="clear">清空记忆</button><button class="button danger" data-grant="${h(grant.id)}" data-grant-action="remove">移出群</button></div></td>
  </tr>`).join("")}</tbody></table></div></article>` : "";
  $("#content").innerHTML = `<div class="toolbar search-toolbar">${searchBox("roles", "搜索角色名、角色编号、群名或授权编号")}<span class="tag">${data.total} 个角色 · ${data.grant_total} 项群授权</span></div><div class="toolbar"><span class="tag warn">可删除角色</span><span class="tag">不会显示所属群友、模型密钥或人设</span></div>${roleTable}${pager({ ...data, total: Math.max(data.total, data.grant_total) })}<div class="spacer"></div>${grants}`;
}

async function deleteRole(id, name) {
  if (!await ask(`永久删除角色“${name}”？`, "该角色的密钥、全部群授权和聊天记忆都会删除，无法从机器人中恢复；其他角色和今日用量不变。")) return;
  await api(scoped(`/admin/api/roles/${encodeURIComponent(id)}/delete`), { method: "POST", body: "{}" });
  toast(`角色“${name}”已删除`);
  await renderRoles();
}

async function renderLearning() {
  const query = new URLSearchParams({
    page: state.page, q: state.queries.learning,
    scope: state.filters.learningScope, status: state.filters.learningStatus,
    order: state.filters.learningOrder,
  });
  const data = await api(scoped(`/admin/api/learning?${query}`));
  const groups = data.groups.map((group) => `<option value="${h(group.scope)}" ${state.filters.learningScope === group.scope ? "selected" : ""}>${h(group.name || `群 ${group.tag}`)}${group.name ? ` · ${h(group.tag)}` : ""}</option>`).join("");
  const toolbar = `<div class="toolbar learning-toolbar">${searchBox("learning", "搜索问句、回答、群名或回复编号")}<select id="learningScope" aria-label="筛选群"><option value="">全部群</option>${groups}</select><select id="learningStatus" aria-label="筛选状态"><option value="all" ${state.filters.learningStatus === "all" ? "selected" : ""}>全部状态</option><option value="active" ${state.filters.learningStatus === "active" ? "selected" : ""}>可用</option><option value="disabled" ${state.filters.learningStatus === "disabled" ? "selected" : ""}>已禁用</option></select><select id="learningOrder" aria-label="排序方式"><option value="recent" ${state.filters.learningOrder === "recent" ? "selected" : ""}>最近学习</option><option value="count_desc" ${state.filters.learningOrder === "count_desc" ? "selected" : ""}>学习次数：多到少</option><option value="count_asc" ${state.filters.learningOrder === "count_asc" ? "selected" : ""}>学习次数：少到多</option></select><button class="button" id="learningFilter">筛选并排序</button><span class="tag">${data.total} 组问答</span></div>`;
  const intro = `<article class="panel learning-intro"><div class="panel-body"><b>这里显示机器人真正学到的“问句 → 回答”</b><p>可搜索并禁用不合适的回答，禁用后不会再被学习聊天选中；恢复后可再次使用。后台不额外保存或展示完整群聊历史。</p></div></article>`;
  const cards = data.items.length ? `<div class="learning-list">${data.items.map((item) => `<article class="data-card learning-card ${item.disabled ? "disabled" : ""}">
    <div class="data-card-head"><div><h3>${h(item.group_name || `群 ${item.group_tag}`)}</h3><p class="mono">回复 #${item.id}${item.group_name ? ` · 群 ${h(item.group_tag)}` : ""}</p></div><span class="tag ${item.disabled ? "bad" : "good"}">${item.disabled ? "已禁用" : "可用"}</span></div>
    <div class="learned-pair"><div><span>问</span><p>${h(item.prompt)}</p></div><div><span>答</span><p>${h(item.reply)}</p></div></div>
    <div class="learning-meta"><span>学习 ${item.count} 次</span><span>${item.kind === "image" ? "图片回答" : "文字回答"}</span><span>更新 ${when(item.updated)}</span></div>
    <div class="card-actions"><button class="button ${item.disabled ? "good" : "warning"}" data-learning-id="${item.id}" data-learning-action="${item.disabled ? "restore" : "disable"}">${item.disabled ? "恢复回答" : "禁用回答"}</button></div>
  </article>`).join("")}</div>` : empty("没有符合条件的学习内容", "换一个群、状态或关键词再试；尚未学习时也会显示为空。");
  $("#content").innerHTML = toolbar + intro + cards + pager(data);
}

async function learningAction(id, action) {
  await api(scoped(`/admin/api/learning/${id}/${action}`), { method: "POST", body: "{}" });
  toast(action === "disable" ? `学习回复 #${id} 已禁用` : `学习回复 #${id} 已恢复`);
  await renderLearning();
}

async function grantAction(id, action) {
  const labels = { pause: "暂停角色", resume: "恢复角色", clear: "清空聊天记忆", remove: "把角色移出该群" };
  if (["clear", "remove"].includes(action) && !await ask(`${labels[action]}？`, action === "remove" ? "角色本身不会删除，但需要角色主人重新授权才能回来。" : "旧聊天记忆会永久删除，无法从机器人中恢复。")) return;
  await api(scoped(`/admin/api/grants/${id}/${action}`), { method: "POST", body: "{}" });
  toast(`${labels[action]}已完成`);
  await renderRoles();
}

async function renderGallery() {
  const mode = state.filters.galleryMode;
  const category = state.filters.galleryCategory;
  const scope = mode === "local" ? state.filters.galleryScope : "";
  const query = new URLSearchParams({ page: state.page, mode, category, scope });
  const data = await api(`/admin/api/gallery?${query}`);
  const groupSelect = mode === "local" ? `<select id="galleryScope" aria-label="私有图库所属群"><option value="">全部私有群</option>${data.groups.map((item) => `<option value="${h(item.scope)}" ${scope === item.scope ? "selected" : ""}>${h(item.name || `群 ${item.tag}`)}（${h(item.tag)}）</option>`).join("")}</select>` : "";
  const toolbar = `<div class="toolbar gallery-toolbar"><select id="galleryMode" aria-label="图库范围"><option value="all" ${mode === "all" ? "selected" : ""}>全部图库</option><option value="public" ${mode === "public" ? "selected" : ""}>公共图库</option><option value="local" ${mode === "local" ? "selected" : ""}>所有群私有图库</option></select>${groupSelect}<select id="galleryCategory" aria-label="图片分类"><option value="">全部分类</option>${data.categories.map((item) => `<option value="${h(item)}" ${category === item ? "selected" : ""}>${h(item)}</option>`).join("")}</select><button class="button" id="galleryFilter">筛选</button><span class="tag">共 ${data.total} 张</span></div>`;
  const grid = data.items.length ? `<div class="gallery-grid">${data.items.map((item) => {
    const owner = item.mode === "public" ? "公共图库" : `私有 · ${item.group_name || `群 ${item.group_tag}`}（${item.group_tag}）`;
    return `<article class="image-card"><a href="/admin/api/gallery/${item.id}/image" target="_blank" rel="noopener"><img src="/admin/api/gallery/${item.id}/thumbnail" alt="${h(item.category)} 分类图片（点击查看原图或完整动画）" loading="lazy" decoding="async"></a><div class="image-meta"><b>${h(item.category)}</b><p>#${item.id} · ${h(item.size)}</p><p class="image-owner ${item.mode}">${h(owner)}</p><button class="button danger" data-image-delete="${item.id}">删除</button></div></article>`;
  }).join("")}</div>` : empty("没有符合条件的图片", "当前范围内没有这个分类；公共图库与群私有图库彼此隔离。 ");
  $("#content").innerHTML = toolbar + grid + pager(data);
}

async function deleteImage(id) {
  if (!await ask(`永久删除图片 #${id}？`, "删除后无法从机器人中恢复。")) return;
  await api(`/admin/api/gallery/${id}/delete`, { method: "POST", body: "{}" });
  toast(`图片 #${id} 已删除`);
  await renderGallery();
}

async function renderFeedback() {
  const closed = state.filters.feedbackState === "closed";
  const data = await api(scoped(`/admin/api/feedback?page=${state.page}&closed=${closed}`));
  const toolbar = `<div class="toolbar"><select id="feedbackState"><option value="open" ${!closed ? "selected" : ""}>待处理</option><option value="closed" ${closed ? "selected" : ""}>已处理</option></select><button class="button" id="feedbackFilter">筛选</button><span class="tag">共 ${data.total} 条</span></div>`;
  const cards = data.items.length ? `<div class="card-grid">${data.items.map((item) => `<article class="data-card feedback-card ${item.closed ? "closed" : ""}"><div class="data-card-head"><div><h3>反馈 #${item.id}</h3><p class="mono">${when(item.created)}</p></div><span class="tag ${item.closed ? "" : "warn"}">${item.closed ? "已处理" : "待处理"}</span></div><p class="feedback-body">${h(item.body)}</p><button class="button ${item.closed ? "good" : ""}" data-feedback="${item.id}" data-closed="${item.closed ? "false" : "true"}">${item.closed ? "重新打开" : "标记完成"}</button></article>`).join("")}</div>` : empty(closed ? "没有已处理反馈" : "反馈箱已清空", closed ? "处理完成的反馈会保留在这里。" : "当前没有等待处理的问题。 ");
  $("#content").innerHTML = toolbar + cards + pager(data);
}

async function setFeedback(id, closed) {
  await api(scoped(`/admin/api/feedback/${id}`), { method: "POST", body: JSON.stringify({ closed }) });
  toast(closed ? `反馈 #${id} 已完成` : `反馈 #${id} 已重新打开`);
  await renderFeedback();
}

async function renderAudit() {
  const data = await api(`/admin/api/audit?page=${state.page}`);
  $("#content").innerHTML = `<article class="panel"><div class="panel-head"><div><h2>后台安全记录</h2><p>不记录聊天内容、API Key、QQ 原始身份或登录码</p></div><span class="tag">共 ${data.total} 条</span></div><div class="panel-body">${auditLines(data.items)}</div></article>${pager(data)}`;
}

const renderers = { overview: renderOverview, bots: renderBots, groups: renderGroups, roles: renderRoles, learning: renderLearning, gallery: renderGallery, feedback: renderFeedback, audit: renderAudit };

async function render(view = state.view) {
  renderController?.abort();
  renderController = new AbortController();
  state.view = view;
  setHeader(view);
  loading();
  try {
    if (view !== "overview" && state.overview) showHealth(state.overview);
    await renderers[view]();
  }
  catch (error) {
    if (error.name === "AbortError") return;
    if (error.cause !== "auth") {
      $("#content").innerHTML = empty("暂时无法读取", error.message);
      toast(error.message, true);
    }
  }
}

$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const error = $("#loginError");
  button.disabled = true;
  error.textContent = "";
  try {
    const data = await api("/admin/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#loginUsername").value.trim(),
        password: $("#loginPassword").value,
      }),
    });
    state.csrf = data.csrf;
    showApp();
    await loadBots();
    await render("overview");
  } catch (failure) { error.textContent = failure.message; }
  finally { button.disabled = false; }
});

$("#logoutButton").addEventListener("click", async () => {
  try { await api("/admin/api/logout", { method: "POST", body: "{}" }); } catch (_) { /* session may already be gone */ }
  showLogin();
});
$("#refreshButton").addEventListener("click", async () => {
  try { await loadBots(); await render(); }
  catch (error) { toast(error.message, true); }
});
$("#botSelect").addEventListener("change", async (event) => {
  state.bot = event.target.value;
  state.page = 1;
  state.filters.learningScope = "";
  try { await render(); }
  catch (error) { toast(error.message, true); }
});
$$(".nav-item").forEach((button) => button.addEventListener("click", () => {
  state.page = 1;
  history.replaceState(null, "", `#${button.dataset.view}`);
  render(button.dataset.view);
}));

$("#content").addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  try {
    if (target.id === "aiGlobalSwitch") await toggleAi();
    else if (target.dataset.page) { state.page = Number(target.dataset.page); await render(); }
    else if (target.dataset.pluginToggle) await toggleGroupPlugin(target);
    else if (target.dataset.roleDelete) await deleteRole(target.dataset.roleDelete, target.dataset.roleName);
    else if (target.dataset.grantAction) await grantAction(target.dataset.grant, target.dataset.grantAction);
    else if (target.dataset.imageDelete) await deleteImage(target.dataset.imageDelete);
    else if (target.dataset.feedback) await setFeedback(target.dataset.feedback, target.dataset.closed === "true");
    else if (target.dataset.botDelete) await deleteBot(target.dataset.botDelete);
    else if (target.dataset.learningAction) await learningAction(target.dataset.learningId, target.dataset.learningAction);
    else if (target.dataset.openLearning) {
      state.filters.learningScope = target.dataset.openLearning;
      state.queries.learning = "";
      state.page = 1;
      history.replaceState(null, "", "#learning");
      await render("learning");
    }
    else if (target.dataset.clearSearch) {
      state.queries[target.dataset.clearSearch] = "";
      state.page = 1;
      await render();
    }
    else if (target.id === "learningFilter") {
      state.filters.learningScope = $("#learningScope").value;
      state.filters.learningStatus = $("#learningStatus").value;
      state.filters.learningOrder = $("#learningOrder").value;
      state.page = 1;
      await renderLearning();
    }
    else if (target.id === "galleryFilter") {
      const nextMode = $("#galleryMode").value;
      const nextScope = nextMode === "local" ? ($("#galleryScope")?.value || "") : "";
      const rangeChanged = nextMode !== state.filters.galleryMode || nextScope !== state.filters.galleryScope;
      state.filters.galleryMode = nextMode;
      state.filters.galleryScope = nextScope;
      state.filters.galleryCategory = rangeChanged ? "" : $("#galleryCategory").value;
      state.page = 1;
      await renderGallery();
    }
    else if (target.id === "feedbackFilter") {
      state.filters.feedbackState = $("#feedbackState").value;
      state.page = 1;
      await renderFeedback();
    }
  } catch (error) { toast(error.message, true); }
});

$("#content").addEventListener("submit", async (event) => {
  if (event.target.matches(".search-form")) {
    event.preventDefault();
    const view = event.target.dataset.searchView;
    state.queries[view] = event.target.elements.q.value.trim();
    state.page = 1;
    await render();
    return;
  }
  if (event.target.matches(".bot-form")) {
    event.preventDefault();
    const button = event.target.querySelector('button[type="submit"]');
    button.disabled = true;
    try { await saveBot(event.target); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
    return;
  }
  if (!event.target.matches(".group-form")) return;
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try { await saveGroup(event.target); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

$("#content").addEventListener("change", (event) => {
  if (event.target.matches('input[name="learning_enabled"]')) {
    const panel = event.target.closest("[data-learning-panel]");
    if (!panel) return;
    panel.classList.toggle("is-on", event.target.checked);
    panel.classList.toggle("is-off", !event.target.checked);
    panel.querySelector("[data-learning-status]").textContent = event.target.checked ? "已开启" : "已关闭";
    toast(event.target.checked ? "群聊学习将在保存后开启" : "群聊学习将在保存后关闭");
    return;
  }
  if (event.target.matches('select[name="library_mode"]')) {
    const form = event.target.closest(".group-form");
    const warning = form?.querySelector(".privacy-warning");
    if (warning) warning.textContent = event.target.value === "public"
      ? "公开模式：本群学到的内容可能在其他公开群回复。"
      : "私有模式：学习内容不会跨群使用。";
    toast("学习库范围将在保存后生效");
  }
});

(async function boot() {
  try {
    const session = await api("/admin/api/session");
    state.csrf = session.csrf;
    const requested = location.hash.slice(1);
    state.view = Object.hasOwn(titles, requested) ? requested : "overview";
    showApp();
    await loadBots();
    await render(state.view);
  } catch (_) { showLogin(); }
})();
