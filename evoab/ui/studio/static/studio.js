// EvoAB Studio: new run, running, history, run detail with replay, benchmarks, settings.
"use strict";

// -- helpers -----------------------------------------------------------------

const $ = (id) => document.getElementById(id);
function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") node.style.cssText = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) node.append(kid instanceof Node ? kid : String(kid));
  return node;
}
// replaceChildren that skips null/false and flattens arrays, like h().
function put(node, ...kids) {
  node.replaceChildren(...kids.flat(Infinity).filter((kid) => kid != null && kid !== false));
}
async function api(path, options = {}) {
  const init = { cache: "no-store", ...options };
  if (options.json !== undefined) {
    init.body = JSON.stringify(options.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(path, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}
let toastTimer;
function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast${error ? " error" : ""}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), error ? 6000 : 2600);
}
const fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString("zh-CN"));
function fmtTokens(n) {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e4) return `${(n / 1e3).toFixed(1)}k`;
  return fmtInt(n);
}
function fmtDuration(s) {
  if (s == null) return "—";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} 分 ${s % 60} 秒` : `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}
const pad = (v) => String(v).padStart(2, "0");
function fmtTime(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtClock(seconds) {
  if (seconds == null || !isFinite(seconds)) return "--:--";
  seconds = Math.max(0, Math.floor(seconds));
  return `${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`;
}
function ago(epoch) {
  if (!epoch) return "从未";
  const s = Date.now() / 1000 - epoch;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return fmtTime(epoch);
}

const RUN_STATUS = {
  success: "成功", failure: "失败", error: "出错", running: "运行中", starting: "启动中",
  failed: "启动失败", interrupted: "已中断",
};
const BENCH_STATUS = {
  ready: "已就绪", not_prepared: "未准备", preparing: "准备中", setup_failed: "准备失败",
  external: "需外部运行时", real: "真机",
};
const pill = (status, labels = RUN_STATUS) => h("span", { class: `pill ${status}` }, labels[status] || status);
const isLive = (run) => run && (run.status === "running" || run.status === "starting");

// -- state -------------------------------------------------------------------

const S = {
  config: null,
  providers: [],
  benchmarks: [],
  runs: [],
  totals: null,
  tasks: {}, // "integration/profile" -> task list
  models: {}, // provider id -> {models, capabilities, recommended}
  wizard: { step: 1, bench: null, task: null, suite: "all", query: "", method: "direct", effort: "low",
            provider: null, model: null, modelQuery: "" },
  history: { query: "", status: "all", bench: "all", model: "all", selected: new Set(), compare: false },
  replay: null,
};

async function loadRuns() {
  const data = await api("/api/runs");
  S.runs = data.runs;
  S.totals = data.totals;
  const live = S.runs.filter(isLive).length;
  $("runningBadge").hidden = !live;
  $("runningBadge").textContent = live;
}
const loadProviders = async () => (S.providers = await api("/api/providers"));
const loadBenchmarks = async () => (S.benchmarks = await api("/api/benchmarks"));
async function loadTasks(integration, profile, force = false) {
  const key = `${integration}/${profile}`;
  if (!S.tasks[key] || force) S.tasks[key] = await api(`/api/benchmarks/${integration}/${profile}/tasks`);
  return S.tasks[key];
}
async function loadModels(providerId, force = false) {
  if (!S.models[providerId] || force) S.models[providerId] = await api(`/api/providers/${encodeURIComponent(providerId)}/models`);
  return S.models[providerId];
}
const findRun = (key) =>
  S.runs.find((r) => r.key === key || r.id === key) ||
  (key.startsWith("launch/") && S.runs.find((r) => r.launch_id === key.slice(7)));

// -- routing -----------------------------------------------------------------

let current = { page: null, arg: null };
function route() {
  const hash = location.hash.slice(1) || "new";
  const [section, ...rest] = hash.split("/");
  const page = section === "launch" ? "run" : section;
  const arg = section === "launch" ? hash : rest.join("/");
  const same = current.page === page && current.arg === arg;
  current = { page, arg };
  for (const link of document.querySelectorAll(".nav a")) {
    const target = link.dataset.page;
    link.classList.toggle("active", target === page || (page === "run" && target === "history"));
  }
  if (page !== "run") closeReplay();
  const main = $("main");
  if (!same) main.scrollTop = 0;
  const pages = { new: pageNew, running: pageRunning, history: pageHistory, run: pageRun, benchmarks: pageBenchmarks, settings: pageSettings };
  (pages[page] || pageNew)(main, arg);
}
function pageHead(title, subtitle, ...actions) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", { text: title }), subtitle ? h("p", { text: subtitle }) : null),
    h("div", { class: "actions" }, ...actions));
}

// -- new run (wizard) --------------------------------------------------------

const STEPS = ["Benchmark", "任务", "方法", "API 与模型", "确认"];

function wizardReady(upTo) {
  const w = S.wizard;
  const checks = [!!w.bench, !!w.task, !!w.method, !!(w.provider && w.model)];
  return checks.slice(0, upTo).every(Boolean);
}
function selectedProvider() {
  return S.providers.find((p) => p.id === S.wizard.provider);
}
function selectedMethod() {
  return S.config.methods.find((m) => m.id === S.wizard.method);
}

function pageNew(main) {
  const w = S.wizard;
  const steps = h("div", { class: "steps" }, STEPS.map((name, i) => {
    const n = i + 1;
    return h("button", {
      class: `step-tab${w.step === n ? " active" : ""}${n < w.step && wizardReady(n) ? " done" : ""}`,
      disabled: !wizardReady(n - 1) && n !== 1,
      onclick: () => { w.step = n; route(); },
    }, h("span", { class: "n" }, n < w.step && wizardReady(n) ? "✓" : n), name);
  }));
  const body = h("div", { class: "step-body" });
  const renderers = [stepBench, stepTask, stepMethod, stepModel, stepConfirm];
  renderers[w.step - 1](body);
  const foot = h("div", { class: "step-foot" },
    h("button", { class: "btn", disabled: w.step === 1, onclick: () => { w.step -= 1; route(); } }, "上一步"),
    w.step < 5 ? h("button", { class: "btn primary", disabled: !wizardReady(w.step), onclick: () => { w.step += 1; route(); } }, "下一步") : h("span"));
  put(main, h("div", { class: "page wide" },
    pageHead("新建运行", "选择 Benchmark、任务、方法和模型后启动一次仿真评测"),
    h("div", { class: "wizard" },
      h("div", {}, steps, body, foot),
      wizardSummary())));
}

function wizardSummary() {
  const w = S.wizard;
  const provider = selectedProvider();
  const method = selectedMethod();
  const row = (k, v) => [h("dt", { text: k }), h("dd", {}, v || h("span", { class: "faint" }, "未选择"))];
  const launchBtn = h("button", { class: "btn primary", disabled: !wizardReady(4), onclick: launchRun }, "启动运行");
  return h("div", { class: "card card-pad summary" },
    h("h2", { text: "本次运行" }),
    h("dl", {},
      row("类型", "仿真"),
      row("环境", w.bench && `${w.bench.title} / ${w.bench.profile}`),
      row("任务", w.task && w.task.task),
      row("本体", w.task && w.task.embodiment),
      row("方法", method && `${method.agent} · ${method.label}`),
      row("推理强度", w.effort),
      row("API", provider && provider.label),
      row("模型", w.model)),
    launchBtn,
    h("p", { class: "small muted", style: "margin:10px 0 0" }, "启动后环境准备约 30 秒，运行进度在“运行中”查看。"));
}

function stepBench(body) {
  const w = S.wizard;
  const cards = [];
  const others = [];
  for (const bench of S.benchmarks.filter((b) => b.kind === "simulation")) {
    const blocked = [];
    for (const profile of bench.profiles) {
      if (!profile.launchable) { blocked.push(profile); continue; }
      const selected = w.bench && w.bench.integration === bench.integration && w.bench.profile === profile.profile;
      cards.push(h("div", {
        class: `choice${selected ? " selected" : ""}`,
        onclick: () => {
          if (!selected) { w.bench = { integration: bench.integration, profile: profile.profile, title: bench.title }; w.task = null; w.suite = "all"; }
          w.step = 2; route();
        },
      },
        pill(profile.status, BENCH_STATUS),
        h("div", { class: "title" }, bench.title, h("span", { class: "tag" }, profile.profile)),
        h("div", { class: "desc" }, bench.description),
        h("div", { class: "meta" }, `${profile.task_count} 个任务 · ${bench.simulator}`)));
    }
    if (blocked.length) others.push([bench, blocked]);
  }
  body.append(
    h("div", { class: "section-title" }, "可启动的环境"),
    cards.length ? h("div", { class: "choice-grid" }, cards)
      : h("div", { class: "card empty" }, h("b", {}, "还没有准备好的环境"), "在 Benchmark 页面一键准备后即可在这里选择。"),
    h("div", { class: "toolbar", style: "margin-top:26px" }, h("div", { class: "section-title", style: "margin:0" }, "其他环境"),
      h("span", { class: "muted small" }, "需要先准备，或暂不支持从网页启动"),
      h("span", { class: "spacer" }), h("a", { class: "btn small", href: "#benchmarks" }, "管理 Benchmark")),
    h("div", { class: "card table-wrap" }, h("table", { class: "table" }, h("tbody", {}, others.map(([bench, profiles]) => h("tr", {},
      h("td", { style: "width:160px;font-weight:550" }, bench.title),
      h("td", { style: "white-space:normal" }, h("div", { class: "chips" }, profiles.map((p) => h("span", { class: "dim" },
        h("b", {}, p.profile === "default" ? bench.integration : p.profile), BENCH_STATUS[p.status] + (p.status === "ready" ? " · 暂不支持网页启动" : ""))))),
      h("td", { class: "muted small", style: "white-space:normal" }, bench.simulator)))))));
}

async function stepTask(body) {
  const w = S.wizard;
  body.append(h("div", { class: "muted" }, "加载任务…"));
  const tasks = await loadTasks(w.bench.integration, w.bench.profile);
  const suites = {};
  for (const t of tasks) suites[t.suite] = (suites[t.suite] || 0) + 1;
  const list = h("div", { class: "list" });
  const renderList = () => {
    const q = w.query.trim().toLowerCase();
    const shown = tasks.filter((t) => (w.suite === "all" || t.suite === w.suite) &&
      (!q || t.task.toLowerCase().includes(q) || (t.instruction || "").toLowerCase().includes(q)));
    put(list, ...(shown.length ? shown.map((t) => h("div", {
      class: `list-item${w.task && w.task.task === t.task ? " selected" : ""}`,
      onclick: () => { w.task = t; w.step = 3; route(); },
    },
      h("div", { class: "t", title: t.task }, t.task),
      h("span", {}, t.difficulty ? h("span", { class: "tag" }, t.difficulty) : null, " ", h("span", { class: "tag" }, t.suite)),
      h("div", { class: "s", title: t.instruction }, [t.instruction || "（无说明）", `本体 ${t.embodiment}`, t.category,
        t.max_steps ? `步数上限 ${t.max_steps}` : null].filter(Boolean).join(" · ")),
    )) : [h("div", { class: "empty" }, "没有匹配的任务")]));
  };
  const chips = h("div", { class: "chips" },
    [["all", `全部 ${tasks.length}`], ...Object.entries(suites).map(([s, n]) => [s, `${s} ${n}`])].map(([value, label]) =>
      h("button", { class: `chip-btn${w.suite === value ? " active" : ""}`, onclick: () => { w.suite = value; route(); } }, label)));
  const search = h("input", { class: "input", type: "search", placeholder: "搜索任务名或说明…", value: w.query, style: "width:260px",
    oninput: (e) => { w.query = e.target.value; renderList(); } });
  put(body, h("div", { class: "toolbar" }, chips, h("span", { class: "spacer" }), search), list);
  renderList();
}

function stepMethod(body) {
  const w = S.wizard;
  body.append(
    h("div", { class: "section-title" }, "方法"),
    h("div", { class: "choice-grid", style: "margin-bottom:22px" }, S.config.methods.map((m) => h("div", {
      class: `choice${w.method === m.id ? " selected" : ""}`, onclick: () => { w.method = m.id; route(); },
    }, h("div", { class: "title" }, m.label, h("span", { class: "tag" }, m.agent)), h("div", { class: "desc" }, m.description)))),
    h("div", { class: "section-title" }, "推理强度"),
    h("div", { class: "segmented" }, S.config.reasoning_efforts.map((e) =>
      h("button", { class: w.effort === e ? "active" : "", onclick: () => { w.effort = e; route(); } }, e))),
    h("p", { class: "muted small" }, "更多方法（Hybrid VLA、Code as Policy、多智能体等）接入后会出现在这里。"));
}

async function stepModel(body) {
  const w = S.wizard;
  const configured = S.providers.filter((p) => p.key_configured);
  if (!configured.length) {
    body.append(h("div", { class: "card empty" }, h("b", {}, "还没有可用的 API"), "先在设置中添加提供商并填写密钥。 ",
      h("a", { class: "btn small", href: "#settings/providers" }, "去设置")));
    return;
  }
  if (!w.provider || !configured.some((p) => p.id === w.provider)) w.provider = configured[0].id;
  body.append(
    h("div", { class: "section-title" }, "API"),
    h("div", { class: "choice-grid", style: "margin-bottom:20px" }, S.providers.map((p) => h("div", {
      class: `choice${w.provider === p.id ? " selected" : ""}${p.key_configured ? "" : " disabled"}`,
      onclick: p.key_configured ? () => { if (w.provider !== p.id) { w.provider = p.id; w.model = null; } route(); } : null,
    }, h("div", { class: "title" }, p.label), h("div", { class: "desc mono" }, p.base_url),
      h("div", { class: "meta" }, p.key_configured ? `密钥 ${p.key_masked} · ${p.recommended.length} 个已验证模型` : "未配置密钥")))),
    h("div", { class: "section-title" }, "模型"));
  const holder = h("div", {}, h("div", { class: "muted" }, "加载模型列表…"));
  body.append(holder);
  let info;
  try {
    info = await loadModels(w.provider);
  } catch (error) {
    put(holder, h("div", { class: "notice error" }, `模型列表获取失败：${error.message}`));
    return;
  }
  const caps = info.capabilities || {};
  const list = h("div", { class: "list" });
  const renderList = () => {
    const q = w.modelQuery.trim().toLowerCase();
    const match = (m) => !q || m.toLowerCase().includes(q);
    const verified = info.models.filter((m) => caps[m] && caps[m].vision && caps[m].tools && match(m));
    const failed = info.models.filter((m) => caps[m] && !(caps[m].vision && caps[m].tools) && match(m));
    const unknown = info.models.filter((m) => !caps[m] && match(m));
    const item = (m) => {
      const c = caps[m];
      return h("div", { class: `list-item${w.model === m ? " selected" : ""}`, onclick: () => { w.model = m; w.step = 5; route(); } },
        h("div", { class: "t mono" }, m),
        c ? h("span", {}, h("span", { class: `tag ${c.vision ? "good" : "bad"}` }, c.vision ? "图像 ✓" : "图像 ✗"), " ",
          h("span", { class: `tag ${c.tools ? "good" : "bad"}` }, c.tools ? "工具 ✓" : "工具 ✗"))
          : h("span", { class: "tag" }, "未探测"),
        c && c.latency_s ? h("div", { class: "s" }, `探测耗时 ${c.latency_s}s${c.note ? ` · ${c.note}` : ""}`) : null);
    };
    const groups = [];
    if (verified.length) groups.push(h("div", { class: "list-group" }, `已验证：可看图像并调用工具（${verified.length}）`), ...verified.map(item));
    if (unknown.length) groups.push(h("div", { class: "list-group" }, `未探测（${unknown.length}）`), ...unknown.map(item));
    if (failed.length) groups.push(h("div", { class: "list-group" }, `探测未通过（${failed.length}）`), ...failed.map(item));
    put(list, ...(groups.length ? groups : [h("div", { class: "empty" }, "没有匹配的模型")]));
  };
  put(holder,
    h("div", { class: "toolbar" },
      h("input", { class: "input", type: "search", placeholder: "搜索模型…", value: w.modelQuery, style: "width:260px",
        oninput: (e) => { w.modelQuery = e.target.value; renderList(); } }),
      h("span", { class: "spacer" }),
      h("a", { class: "btn small", href: "#settings/models" }, "模型能力探测")),
    list);
  renderList();
}

function commandPreview() {
  const w = S.wizard;
  const provider = selectedProvider();
  const method = selectedMethod();
  const [profile, task] = w.bench.integration === "libero_pro" ? w.task.task.split("/") : [w.bench.profile, w.task.task];
  return [
    "evoab run", `--integration ${w.bench.integration}`, "--deploy local", `--runtime-profile ${profile}`,
    `--task ${task}`, `--tool-profile ${method.id}`,
    ...(method.id === "clean" ? ["--no-depth"] : []), "--no-sam3", `--agent ${method.agent}`,
    `--model ${w.model}`, `--reasoning-effort ${w.effort}`, `--provider-url ${provider.base_url}`,
    `--provider-profile ${provider.profile}`, "--codex-web-search disabled", "--monitor",
  ].join(" \\\n  ");
}

function stepConfirm(body) {
  const w = S.wizard;
  if (!wizardReady(4)) {
    body.append(h("div", { class: "notice info" }, "请先完成前面的步骤。"));
    return;
  }
  const provider = selectedProvider();
  const method = selectedMethod();
  const rows = [
    ["环境", `${w.bench.title} / ${w.bench.profile}（仿真）`], ["Benchmark", w.task.suite], ["任务", w.task.task],
    ["说明", w.task.instruction || "—"], ["本体", w.task.embodiment], ["步数上限", w.task.max_steps || "—"],
    ["方法", `${method.agent} · ${method.label}`], ["推理强度", w.effort], ["API", `${provider.label}（${provider.profile}）`],
    ["模型", w.model],
  ];
  body.append(
    h("div", { class: "card" }, h("table", { class: "table" }, h("tbody", {}, rows.map(([k, v]) =>
      h("tr", {}, h("td", { class: "muted", style: "width:120px" }, k), h("td", { style: "white-space:normal" }, v)))))),
    h("div", { class: "section-title", style: "margin-top:20px" }, "等价命令"),
    h("div", { class: "command" }, commandPreview()),
    h("p", { class: "muted small" }, "API 密钥由服务器注入环境变量 EVOAB_API_KEY，不会出现在页面或命令中。"),
    h("button", { class: "btn primary", style: "margin-top:8px", onclick: launchRun }, "启动运行"));
}

async function launchRun(event) {
  const w = S.wizard;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const { launch_id } = await api("/api/runs", {
      method: "POST",
      json: { integration: w.bench.integration, profile: w.bench.profile, task: w.task.task, provider_id: w.provider,
              model: w.model, reasoning_effort: w.effort, method: w.method },
    });
    toast("已启动，环境准备约需 30 秒");
    await loadRuns();
    location.hash = `#run/launch/${launch_id}`;
  } catch (error) {
    toast(`启动失败：${error.message}`, true);
    button.disabled = false;
  }
}

// -- running -----------------------------------------------------------------

function pageRunning(main) {
  const live = S.runs.filter(isLive);
  const cards = live.map((run) => {
    const lp = run.live || {};
    const budget = lp.max_steps || run.max_steps;
    const steps = lp.steps ?? run.environment_steps;
    const pct = budget && steps != null ? Math.min(100, (100 * steps) / budget) : 0;
    return h("div", { class: "card run-card" },
      h("a", { class: "thumb", href: `#${run.key}` },
        lp.frame_url ? h("img", { src: lp.frame_url, alt: "最新画面" }) : h("span", {}, run.status === "starting" ? "环境准备中…" : "等待画面"),
        pill(run.status)),
      h("div", { class: "body" },
        h("a", { class: "t", href: `#${run.key}`, title: run.task }, run.task),
        h("div", { class: "muted small" }, `${run.integration}/${run.profile} · ${run.benchmark || "—"} · ${run.method} · ${run.model}`),
        h("div", { class: "row small" }, h("span", { class: "muted" }, "环境步数"), h("span", { class: "spacer" }),
          h("span", {}, `${steps ?? "—"} / ${budget ?? "—"}`)),
        h("div", { class: "progress" }, h("i", { style: `width:${pct}%` })),
        h("div", { class: "kv" },
          h("div", {}, h("span", {}, "已运行"), fmtDuration(run.elapsed_s)),
          h("div", {}, h("span", {}, "模型请求"), fmtInt(run.usage.requests)),
          h("div", {}, h("span", {}, "tokens"), fmtTokens(run.usage.total_tokens)))),
      h("div", { class: "foot" },
        h("a", { class: "btn small", href: `#${run.key}` }, "查看详情"),
        h("span", { class: "spacer" }),
        run.launch_id ? h("button", { class: "btn small danger", onclick: (e) => stopRun(run, e.currentTarget) }, "停止") : null));
  });
  put(main, h("div", { class: "page wide" },
    pageHead("运行中", live.length ? `${live.length} 个运行正在进行，每 5 秒刷新` : "当前没有正在进行的运行",
      h("a", { class: "btn primary", href: "#new" }, "＋ 新建运行")),
    live.length ? h("div", { class: "run-cards" }, cards)
      : h("div", { class: "card empty" }, h("b", {}, "没有正在进行的运行"), "从“新建运行”启动一次评测，进度会显示在这里。")));
}

async function stopRun(run, button) {
  if (!confirm(`停止运行 ${run.task}？\n会向运行进程发送中断信号，已记录的数据会保留。`)) return;
  if (button) button.disabled = true;
  try {
    await api(`/api/runs/${run.launch_id}/stop`, { method: "POST" });
    toast("已发送停止信号");
  } catch (error) {
    toast(`停止失败：${error.message}`, true);
  }
  await refresh();
}

// -- history -----------------------------------------------------------------

function filteredRuns() {
  const f = S.history;
  const q = f.query.trim().toLowerCase();
  return S.runs.filter((r) => {
    if (f.status !== "all" && r.status !== f.status) return false;
    if (f.bench !== "all" && `${r.integration}/${r.profile}` !== f.bench) return false;
    if (f.model !== "all" && r.model !== f.model) return false;
    if (q && ![r.task, r.model, r.benchmark, r.provider, r.id].some((v) => (v || "").toLowerCase().includes(q))) return false;
    return true;
  });
}

function pageHistory(main) {
  const f = S.history;
  const tableHolder = h("div", {});
  const compareHolder = h("div", {});
  const uniq = (fn) => [...new Set(S.runs.map(fn).filter(Boolean))].sort();
  const select = (key, all, values, labels = {}) => h("select", { class: "select", onchange: (e) => { f[key] = e.target.value; renderTable(); } },
    h("option", { value: "all" }, all), values.map((v) => h("option", { value: v, selected: f[key] === v }, labels[v] || v)));
  const renderTable = () => {
    const runs = filteredRuns();
    put(tableHolder, runs.length ? h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "状态"), h("th", {}, "任务"), h("th", {}, "Benchmark"), h("th", {}, "方法"),
        h("th", {}, "模型"), h("th", { class: "num" }, "第几次"), h("th", { class: "num" }, "步数"), h("th", { class: "num" }, "用时"),
        h("th", { class: "num" }, "tokens"), h("th", {}, "开始时间"))),
      h("tbody", {}, runs.map((r) => h("tr", { class: "clickable", onclick: () => (location.hash = `#${r.key}`) },
        h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", checked: f.selected.has(r.key), onchange: (e) => {
          if (e.target.checked) f.selected.add(r.key); else f.selected.delete(r.key);
          renderCompareBar();
        } })),
        h("td", {}, pill(r.status)),
        h("td", { class: "task-cell", title: r.task }, r.task || "—"),
        h("td", {}, `${r.integration || "—"}/${r.profile || "—"}`, h("div", { class: "faint small" }, r.benchmark || "")),
        h("td", {}, r.method),
        h("td", {}, r.model || "—", h("div", { class: "faint small" }, r.provider || "")),
        h("td", { class: "num" }, r.attempt),
        h("td", { class: "num" }, r.environment_steps ?? "—"),
        h("td", { class: "num" }, fmtDuration(r.elapsed_s)),
        h("td", { class: "num" }, fmtTokens(r.usage.total_tokens)),
        h("td", {}, fmtTime(r.started_at)))))))
      : h("div", { class: "card empty" }, h("b", {}, S.runs.length ? "没有匹配的运行" : "还没有运行记录"), S.runs.length ? "调整筛选条件试试。" : "从“新建运行”开始第一次评测。"));
    renderCompareBar();
  };
  const renderCompareBar = () => {
    const keys = [...f.selected].filter((k) => findRun(k));
    put(compareHolder, );
    if (keys.length >= 1) {
      compareHolder.append(h("div", { class: "compare-bar" },
        h("span", {}, `已选 ${keys.length} 个运行`),
        h("span", { class: "row" },
          h("button", { class: "btn small", onclick: () => { f.selected.clear(); f.compare = false; renderTable(); } }, "清除"),
          h("button", { class: "btn small", disabled: keys.length < 2, onclick: () => { f.compare = true; renderCompareBar(); } }, "对比"))));
    }
    if (f.compare && keys.length >= 2) compareHolder.append(compareTable(keys.map(findRun)));
  };
  put(main, h("div", { class: "page wide" },
    pageHead("历史记录", `共 ${S.runs.length} 次运行 · 运行目录 ${S.config.runs_root}`),
    h("div", { class: "toolbar" },
      h("input", { class: "input", type: "search", placeholder: "搜索任务、模型、API…", value: f.query, style: "width:280px",
        oninput: (e) => { f.query = e.target.value; renderTable(); } }),
      select("status", "全部状态", ["success", "failure", "error", "interrupted", "running", "failed"], RUN_STATUS),
      select("bench", "全部环境", uniq((r) => r.integration && `${r.integration}/${r.profile}`)),
      select("model", "全部模型", uniq((r) => r.model))),
    tableHolder, compareHolder));
  renderTable();
  pageHistory.refresh = renderTable;
}

function compareTable(runs) {
  const rows = [
    ["状态", (r) => pill(r.status)], ["任务", (r) => r.task], ["环境", (r) => `${r.integration}/${r.profile}`],
    ["方法", (r) => r.method], ["模型", (r) => r.model], ["推理强度", (r) => r.reasoning_effort || "—"],
    ["第几次", (r) => r.attempt], ["环境步数", (r) => r.environment_steps ?? "—"], ["用时", (r) => fmtDuration(r.elapsed_s)],
    ["模型请求", (r) => fmtInt(r.usage.requests)], ["输入 tokens", (r) => fmtInt(r.usage.input_tokens)],
    ["缓存命中", (r) => fmtInt(r.usage.cached_input_tokens)], ["输出 tokens", (r) => fmtInt(r.usage.output_tokens)],
    ["总 tokens", (r) => fmtInt(r.usage.total_tokens)], ["开始时间", (r) => fmtTime(r.started_at)],
  ];
  return h("div", { class: "card compare table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, "对比"), runs.map((r) => h("th", {}, h("a", { href: `#${r.key}` }, `#${r.attempt} ${r.model}`))))),
    h("tbody", {}, rows.map(([label, fn]) => h("tr", {}, h("td", {}, label), runs.map((r) => h("td", {}, fn(r))))))));
}

// -- run detail and replay ---------------------------------------------------

function pageRun(main, key) {
  const run = findRun(key);
  if (!run) {
    put(main, h("div", { class: "page" }, h("div", { class: "card empty" }, h("b", {}, "找不到这次运行"),
      h("a", { class: "btn small", href: "#history" }, "返回历史记录"))));
    return;
  }
  if (run.id && location.hash !== `#${run.key}`) history.replaceState(null, "", `#${run.key}`);
  let page = main.querySelector(".page[data-run]");
  if (!page || page.dataset.run !== run.key) {
    page = h("div", { class: "page wide", "data-run": run.key },
      h("div", { id: "runTop" }), h("div", { id: "runReplay" }), h("div", { id: "runLog" }));
    put(main, page);
    current.arg = run.key;
  }
  renderRunTop(run);
  if (run.monitor_id) openReplay(run);
  else put($("runReplay"), h("div", { class: "card empty" }, h("b", {}, run.status === "starting" ? "环境准备中" : "没有回放数据"),
    run.status === "starting" ? "运行目录创建后会自动显示画面和工具调用。" : "这次运行没有生成运行目录。"));
  renderLaunchLog(run);
}

function renderRunTop(run) {
  const u = run.usage || {};
  const replay = S.replay && S.replay.runKey === run.key ? S.replay.state : null;
  const dims = [
    ["类型", run.simulated === false ? "真机" : "仿真"], ["环境", `${run.integration || "—"} / ${run.profile || "—"}`],
    ["Benchmark", run.benchmark], ["本体", run.embodiment], ["任务", run.task], ["方法", run.method],
    ["模型", run.model && `${run.provider ? `${run.provider} · ` : ""}${run.model}${run.reasoning_effort ? ` · ${run.reasoning_effort}` : ""}`],
    ["第几次", `第 ${run.attempt} 次`],
  ];
  const budget = (replay && replay.max_steps) || run.max_steps;
  const steps = replay && replay.steps != null ? replay.steps : run.environment_steps;
  const metric = (k, v, cls = "", sub = null) => h("div", { class: "card metric" }, h("div", { class: "k" }, k),
    h("div", { class: `v ${cls}`, title: String(v) }, v), sub ? h("div", { class: "sub" }, sub) : null);
  const message = run.error || run.agent_message;
  put($("runTop"),
    h("div", { class: "detail-head" },
      h("button", { class: "back", title: "返回", onclick: () => history.length > 1 ? history.back() : (location.hash = "#history") }, "←"),
      h("div", { style: "flex:1;min-width:0" }, h("h1", {}, run.task || "—"),
        h("p", {}, (replay && replay.instruction) || "")),
      h("div", { class: "row" }, pill(run.status),
        isLive(run) && run.launch_id ? h("button", { class: "btn small danger", onclick: (e) => stopRun(run, e.currentTarget) }, "停止") : null)),
    h("div", { class: "dims" }, dims.filter(([, v]) => v).map(([k, v]) => h("span", { class: "dim" }, h("b", {}, k), v))),
    h("div", { class: "metrics" },
      metric("结果", RUN_STATUS[run.status] || run.status, run.status),
      metric("用时", fmtDuration(run.elapsed_s)),
      metric("环境步数", `${steps ?? "—"}${budget ? ` / ${budget}` : ""}`),
      metric("工具调用", fmtInt(replay ? replay.total_calls : null)),
      metric("模型请求", fmtInt(u.requests)),
      metric("输入 tokens", fmtTokens(u.input_tokens), "", u.cached_input_tokens ? `缓存命中 ${fmtTokens(u.cached_input_tokens)}` : null),
      metric("输出 tokens", fmtTokens(u.output_tokens), "", u.reasoning_tokens ? `推理 ${fmtTokens(u.reasoning_tokens)}` : null),
      metric("总 tokens", fmtTokens(u.total_tokens))),
    message ? h("div", { class: `notice ${run.error ? "error" : "info"}` }, `${run.error ? "错误：" : "Agent 总结："}${message}`) : null);
}

async function renderLaunchLog(run) {
  const holder = $("runLog");
  if (!run.launch_id) { put(holder, ); return; }
  let log = "";
  try { ({ log } = await api(`/api/runs/${run.launch_id}/log`)); } catch { return; }
  if (!$("runLog")) return;
  const open = holder.querySelector("details")?.open ?? (!run.monitor_id);
  put(holder, h("details", { class: "card", style: "margin-top:14px", open },
    h("summary", { class: "card-pad", style: "cursor:pointer;font-weight:600" }, "启动日志"),
    h("div", { style: "padding:0 16px 16px" }, h("pre", { class: "log" }, log || "（空）"))));
}

// Replay: frames, tool calls, messages and model requests from the run monitor's
// read-only API (mounted at /replay), drawn in Studio's own layout.

function closeReplay() {
  if (!S.replay) return;
  if (S.replay.source) S.replay.source.close();
  clearInterval(S.replay.timer);
  S.replay = null;
}

function openReplay(run) {
  if (S.replay && S.replay.runKey === run.key) return;
  closeReplay();
  const R = (S.replay = { runKey: run.key, monitorId: run.monitor_id, state: null, camera: null, index: 0,
    follow: true, playing: false, timer: null, tab: "calls", openCall: null, source: null });
  put($("runReplay"), h("div", { class: "card empty" }, "加载回放…"));
  const apply = (state) => {
    if (S.replay !== R) return;
    R.state = state;
    drawReplay();
    const latest = findRun(R.runKey);
    if (latest) renderRunTop(latest);
  };
  if (isLive(run)) {
    R.source = new EventSource(`/replay/api/runs/${run.monitor_id}/events`);
    R.source.addEventListener("state", (e) => apply(JSON.parse(e.data)));
    R.source.onerror = () => {};
  } else {
    api(`/replay/api/runs/${run.monitor_id}`).then(apply).catch((error) => {
      put($("runReplay"), h("div", { class: "notice error" }, `回放加载失败：${error.message}`));
    });
  }
}

function replayFrames() {
  const R = S.replay;
  const frames = R.state.frames || [];
  const cameras = [...new Set(frames.map((f) => f.camera))];
  if (!R.camera || !cameras.includes(R.camera)) R.camera = cameras[0] || null;
  return { cameras, frames: frames.filter((f) => f.camera === R.camera) };
}

function drawReplay() {
  const R = S.replay;
  const st = R.state;
  const { cameras, frames } = replayFrames();
  if (R.follow || R.index >= frames.length) R.index = Math.max(0, frames.length - 1);
  const t0 = st.started_at || (frames[0] && frames[0].timestamp) || 0;
  const t1 = Math.max(st.finished_at || 0, frames.length ? frames[frames.length - 1].timestamp : 0, t0 + 1);
  const frame = frames[R.index];

  let root = $("runReplay").querySelector(".replay");
  if (!root) {
    root = h("div", { class: "replay" },
      h("div", { class: "card viewer-card" },
        h("div", { class: "viewer-top", id: "rpCams" }),
        h("div", { class: "stage", id: "rpStage" }),
        h("div", { class: "scrub" },
          h("div", { class: "ticks", id: "rpTicks" }),
          h("div", { class: "scrub-row" },
            h("button", { class: "btn small", id: "rpPlay", onclick: togglePlay }, "▶"),
            h("input", { type: "range", id: "rpSlider", min: 0, max: 0, value: 0, oninput: (e) => {
              R.index = Number(e.target.value); R.follow = R.index === Number(e.target.max); stopPlay(); drawReplay();
            } }),
            h("span", { class: "mono faint", id: "rpTime" })))),
      h("div", { class: "card side-card" }, h("div", { class: "tabs", id: "rpTabs" }), h("div", { class: "feed", id: "rpFeed" })));
    put($("runReplay"), root);
  }
  // cameras
  put($("rpCams"),
    h("span", { class: "small muted" }, "相机"),
    ...cameras.map((c) => h("button", { class: `chip-btn${c === R.camera ? " active" : ""}`, onclick: () => { R.camera = c; R.follow = true; drawReplay(); } }, c)),
    h("span", { class: "spacer" }),
    h("span", { class: "small faint" }, `${frames.length} 帧`),
    isLive(findRun(R.runKey)) ? h("button", { class: `chip-btn${R.follow ? " active" : ""}`, onclick: () => { R.follow = !R.follow; drawReplay(); } }, "跟随最新") : null);
  // stage
  const stage = $("rpStage");
  if (frame) {
    let img = stage.querySelector("img");
    const src = `/replay${frame.url}?width=960`;
    if (!img) { put(stage, img = h("img", { alt: "机器人观测" }), h("span", { class: "stamp" })); }
    if (img.getAttribute("src") !== src) img.src = src;
    stage.querySelector(".stamp").textContent = `${fmtClock(frame.timestamp - t0)} · ${frame.association || ""}`;
  } else {
    put(stage, h("div", { class: "empty" }, "还没有画面"));
  }
  // slider and ticks
  const slider = $("rpSlider");
  slider.max = Math.max(0, frames.length - 1);
  slider.value = R.index;
  $("rpTime").textContent = `${fmtClock(frame ? frame.timestamp - t0 : 0)} / ${fmtClock(t1 - t0)}`;
  const calls = (st.calls || []).filter((c) => c.timestamp);
  const span = t1 - t0;
  put($("rpTicks"), ...calls.map((c) => h("i", {
    class: `call${c.status === "failed" ? " failed" : ""}`, title: c.name, style: `left:${(100 * (c.timestamp - t0)) / span}%`,
  })));
  // side tabs
  const tabs = [["calls", "工具调用", st.total_calls], ["messages", "Agent 消息", (st.messages || []).length], ["models", "模型请求", (st.models || []).length]];
  put($("rpTabs"), ...tabs.map(([id, label, count]) => h("button", { class: R.tab === id ? "active" : "", onclick: () => { R.tab = id; drawReplay(); } },
    label, h("span", { class: "count" }, count ?? 0))));
  drawFeed(frame, t0, span);
}

function drawFeed(frame, t0, span) {
  const R = S.replay;
  const st = R.state;
  const feed = $("rpFeed");
  const keepScroll = feed.scrollTop;
  const now = frame ? frame.timestamp : Infinity;
  const jump = (t) => {
    const { frames } = replayFrames();
    const i = frames.findIndex((f) => f.timestamp >= t);
    R.index = i < 0 ? frames.length - 1 : i;
    R.follow = false; stopPlay(); drawReplay();
  };
  let items = [];
  if (R.tab === "calls") {
    const calls = [...(st.calls || [])].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    const currentId = calls.filter((c) => (c.timestamp || 0) <= now).pop()?.id;
    items = calls.map((c) => {
      const open = R.openCall === c.id;
      return h("div", { class: `event${c.id === currentId ? " current" : ""}${open ? " open" : ""}`, onclick: () => {
        R.openCall = open ? null : c.id; if (c.timestamp) jump(c.timestamp); else drawReplay();
      } },
        h("div", { class: "head" }, h("span", { class: "name" }, c.name || "tool"),
          h("span", { class: `pill ${c.status === "failed" ? "bad" : c.status === "completed" ? "ok" : "running"}` }, c.status),
          h("span", { class: "time" }, c.timestamp ? fmtClock(c.timestamp - t0) : "")),
        open ? [h("pre", {}, `参数\n${c.arguments || "{}"}`), c.output ? h("pre", {}, `输出\n${typeof c.output === "string" ? c.output : JSON.stringify(c.output, null, 2)}`) : null]
          : h("div", { class: "text" }, (c.arguments || "").replace(/\s+/g, " ").slice(0, 160)));
    });
    if (st.history_limited) items.unshift(h("div", { class: "event muted small" }, "记录较多，仅显示最近的调用。"));
  } else if (R.tab === "messages") {
    items = (st.messages || []).map((m) => h("div", { class: "event open", onclick: () => m.timestamp && jump(m.timestamp) },
      h("div", { class: "head" }, h("span", { class: "name" }, { AssistantText: "Agent", ReasoningSummary: "推理摘要", ErrorEvent: "错误" }[m.type] || m.type),
        h("span", { class: "time" }, m.timestamp ? fmtClock(m.timestamp - t0) : "")),
      h("div", { class: "text" }, m.text)));
  } else {
    const models = (st.models || []).filter((m) => m.started_at);
    const execs = (st.calls || []).filter((c) => c.started_at && c.finished_at);
    const bar = (list, cls) => h("div", { class: "lane-bar" }, list.map((m) => h("i", {
      class: cls(m), title: `${fmtClock(m.started_at - t0)} · ${((m.finished_at || m.started_at) - m.started_at).toFixed(1)}s`,
      style: `left:${(100 * (m.started_at - t0)) / span}%;width:${(100 * ((m.finished_at || Date.now() / 1000) - m.started_at)) / span}%`,
    })));
    const durations = models.filter((m) => m.finished_at).map((m) => m.finished_at - m.started_at);
    const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
    items = [h("div", { class: "lanes" },
      h("div", { class: "small muted" }, `模型请求 ${models.length} 次${avg ? ` · 平均 ${avg.toFixed(1)}s · 最长 ${Math.max(...durations).toFixed(1)}s` : ""}`),
      bar(models, (m) => (m.success === false ? "fail" : "")),
      execs.length ? [h("div", { class: "small muted" }, `工具执行 ${execs.length} 次`), bar(execs, () => "exec")] : null,
      h("div", { class: "small faint" }, "横轴为整次运行时间；请求时长包含传输、排队与重试。")),
      ...models.slice().reverse().slice(0, 200).map((m) => h("div", { class: "event", onclick: () => jump(m.started_at) },
        h("div", { class: "head" }, h("span", { class: "name" }, m.id),
          h("span", { class: `pill ${m.success === false ? "bad" : m.finished_at ? "ok" : "running"}` }, m.finished_at ? `${(m.finished_at - m.started_at).toFixed(1)}s` : "进行中"),
          h("span", { class: "time" }, fmtClock(m.started_at - t0)))))];
  }
  put(feed, ...(items.length ? items : [h("div", { class: "empty" }, "暂无记录")]));
  feed.scrollTop = keepScroll;
}

function togglePlay() {
  const R = S.replay;
  if (!R) return;
  if (R.playing) { stopPlay(); return; }
  const { frames } = replayFrames();
  if (R.index >= frames.length - 1) R.index = 0;
  R.playing = true;
  R.follow = false;
  $("rpPlay").textContent = "❚❚";
  R.timer = setInterval(() => {
    const total = replayFrames().frames.length;
    if (R.index >= total - 1) { stopPlay(); return; }
    R.index += 1;
    drawReplay();
  }, 200);
}
function stopPlay() {
  const R = S.replay;
  if (!R || !R.playing) return;
  R.playing = false;
  clearInterval(R.timer);
  if ($("rpPlay")) $("rpPlay").textContent = "▶";
}

// -- benchmarks --------------------------------------------------------------

function pageBenchmarks(main) {
  const groups = [["simulation", "仿真"], ["real", "真机"]];
  const profileRow = (bench, p) => {
    const canPrepare = bench.setup === "auto" && ["not_prepared", "setup_failed"].includes(p.status);
    const canRefresh = bench.setup === "auto" && p.status === "ready";
    const suites = Object.entries(p.suites || {}).map(([s, n]) => `${s} ${n}`).join(" · ");
    return [
      h("div", { class: "profile-row" },
        h("span", { class: "mono" }, p.profile),
        h("span", {}, pill(p.status, BENCH_STATUS)),
        h("span", { class: "suites", title: suites }, p.status === "ready" ? `${p.task_count} 个任务${suites ? ` · ${suites}` : ""}` :
          bench.setup === "external" ? bench.requirements : bench.setup === "real" ? "需要连接实体机器人与设备进程" :
          p.status === "preparing" ? "正在准备，日志见下方" : bench.requirements || "运行一键准备以安装运行时与资源"),
        h("span", { class: "acts" },
          p.status === "ready" ? h("button", { class: "btn small", onclick: () => openTasks(bench, p) }, "浏览任务") : null,
          p.launchable ? h("button", { class: "btn small", onclick: () => startWith(bench, p) }, "用它新建运行") : null,
          canPrepare || canRefresh ? h("button", { class: "btn small", onclick: (e) => prepare(bench, p, e.currentTarget, canRefresh) }, canRefresh ? "重新准备" : "一键准备") : null)),
      p.setup_log && p.status !== "ready" ? h("div", { class: "profile-row" }, h("div", { class: "profile-log" }, h("pre", { class: "log" }, p.setup_log))) : null,
    ];
  };
  put(main, h("div", { class: "page wide" },
    pageHead("Benchmark", "已接入的仿真环境与真机平台，以及每个运行时配置的准备状态",
      h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await loadBenchmarks(); route(); toast("已重新检查"); } }, "重新检查")),
    groups.map(([kind, label]) => h("div", { class: "bench-group" }, h("h2", {}, label),
      S.benchmarks.filter((b) => b.kind === kind).map((b) => h("div", { class: "card bench" },
        h("div", { class: "bench-head" },
          h("div", { class: "t" }, b.title, h("span", { class: "tag" }, b.integration)),
          h("div", {}, { auto: h("span", { class: "tag" }, "一键准备"), external: h("span", { class: "tag" }, "外部运行时"), real: h("span", { class: "tag" }, "真机") }[b.setup]),
          h("div", { class: "d" }, b.description),
          h("div", { class: "m" }, h("span", {}, `仿真器：${b.simulator}`), h("span", {}, `本体：${b.embodiment}`))),
        h("div", { class: "profiles" }, b.profiles.map((p) => profileRow(b, p)))))))));
}

function startWith(bench, p) {
  Object.assign(S.wizard, { bench: { integration: bench.integration, profile: p.profile, title: bench.title }, task: null, suite: "all", step: 2 });
  location.hash = "#new";
}

async function prepare(bench, p, button, again) {
  const what = `${bench.title} / ${p.profile}`;
  if (!confirm(`${again ? "重新" : ""}准备 ${what}？\n会在服务器上运行 evoab setup，下载依赖与资源，可能需要较长时间。`)) return;
  button.disabled = true;
  try {
    await api(`/api/benchmarks/${bench.integration}/${p.profile}/setup`, { method: "POST" });
    toast(`已开始准备 ${what}`);
  } catch (error) {
    toast(`无法开始：${error.message}`, true);
  }
  await loadBenchmarks();
  route();
}

async function openTasks(bench, p) {
  const close = () => { backdrop.remove(); drawer.remove(); };
  const backdrop = h("div", { class: "drawer-backdrop", onclick: close });
  const body = h("div", { class: "drawer-body" }, h("div", { class: "muted" }, "加载任务…"));
  const drawer = h("div", { class: "drawer" },
    h("div", { class: "drawer-head" }, h("h2", {}, `${bench.title} / ${p.profile}`), h("span", { class: "tag" }, `${p.task_count} 个任务`),
      h("span", { class: "spacer" }), h("button", { class: "btn small", onclick: close }, "关闭")), body);
  document.body.append(backdrop, drawer);
  const tasks = await loadTasks(bench.integration, p.profile, true);
  let query = "";
  const table = h("div", {});
  const draw = () => {
    const q = query.toLowerCase();
    const shown = tasks.filter((t) => !q || t.task.toLowerCase().includes(q) || (t.instruction || "").toLowerCase().includes(q) || t.suite.includes(q));
    put(table, h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, "任务"), h("th", {}, "Benchmark"), h("th", {}, "本体"), h("th", { class: "num" }, "步数上限"), h("th", {}, ""))),
      h("tbody", {}, shown.map((t) => h("tr", {},
        h("td", { style: "white-space:normal" }, h("div", { class: "mono" }, t.task), h("div", { class: "muted small" }, t.instruction)),
        h("td", {}, t.suite), h("td", {}, t.embodiment), h("td", { class: "num" }, t.max_steps ?? "—"),
        h("td", {}, p.launchable ? h("button", { class: "btn small", onclick: () => {
          Object.assign(S.wizard, { bench: { integration: bench.integration, profile: p.profile, title: bench.title }, task: t, step: 3 });
          close(); location.hash = "#new"; route();
        } }, "运行") : null)))))));
  };
  put(body, h("div", { class: "toolbar" }, h("input", { class: "input", type: "search", placeholder: "搜索…", style: "width:260px",
    oninput: (e) => { query = e.target.value; draw(); } })), table);
  draw();
}

// -- settings ----------------------------------------------------------------

const SETTINGS_TABS = [["providers", "提供商"], ["models", "模型能力"], ["usage", "用量"], ["general", "通用"]];
const PRESETS = [
  { label: "OpenAI 兼容（自定义）", base_url: "", profile: "omnirouter.chat_completions" },
  { label: "OpenRouter", base_url: "https://openrouter.ai/api/v1", profile: "omnirouter.chat_completions" },
  { label: "DeepSeek", base_url: "https://api.deepseek.com/v1", profile: "deepseek.chat_completions" },
  { label: "Moonshot / Kimi", base_url: "https://api.moonshot.cn/v1", profile: "moonshot.chat_completions" },
  { label: "阿里云百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", profile: "aliyun.chat_completions" },
  { label: "MiniMax", base_url: "https://api.minimaxi.com/v1", profile: "minimax.chat_completions" },
];

function pageSettings(main, arg) {
  const [tab, sub] = (arg || "providers").split("/");
  const body = h("div", { class: "settings-body" });
  put(main, h("div", { class: "settings" },
    h("div", { class: "settings-nav" }, h("h1", {}, "设置"),
      h("nav", { class: "nav" }, SETTINGS_TABS.map(([id, label]) => h("a", { href: `#settings/${id}`, class: tab === id ? "active" : "" }, label)))),
    body));
  ({ providers: settingsProviders, models: settingsModels, usage: settingsUsage, general: settingsGeneral }[tab] || settingsProviders)(body, sub);
}

function settingsProviders(body, selectedId) {
  let query = "";
  const creating = selectedId === "new";
  if (!creating && !S.providers.some((p) => p.id === selectedId)) selectedId = S.providers[0]?.id;
  const listBox = h("div", {});
  const drawList = () => {
    const q = query.toLowerCase();
    const shown = S.providers.filter((p) => !q || p.label.toLowerCase().includes(q) || p.base_url.toLowerCase().includes(q));
    const item = (p) => h("a", { class: `provider-item${p.id === selectedId ? " active" : ""}`, href: `#settings/providers/${p.id}` },
      h("span", { class: "avatar" }, (p.label || p.id).slice(0, 1).toUpperCase()),
      h("span", { style: "min-width:0" }, h("div", { class: "t" }, p.label), h("div", { class: "s" }, p.base_url.replace(/^https?:\/\//, ""))),
      h("span", { class: `dot ${p.connection ? (p.connection.ok ? "ok" : "bad") : ""}`, title: p.connection ? (p.connection.ok ? "已连接" : "连接失败") : "未验证" }));
    const configured = shown.filter((p) => p.key_configured);
    const missing = shown.filter((p) => !p.key_configured);
    put(listBox,
      configured.length ? h("div", { class: "group" }, "已配置") : null, ...configured.map(item),
      missing.length ? h("div", { class: "group" }, "缺少密钥") : null, ...missing.map(item),
      shown.length ? null : h("div", { class: "empty small" }, "没有提供商"));
  };
  const provider = creating ? null : S.providers.find((p) => p.id === selectedId);
  put(body, h("div", { class: "page wide" },
    pageHead("提供商", "模型 API 的地址与密钥。密钥只保存在服务器上，页面只显示脱敏后的形式。",
      h("a", { class: "btn primary", href: "#settings/providers/new" }, "＋ 添加提供商")),
    h("div", { class: "providers" },
      h("div", { class: "card provider-list" },
        h("input", { class: "input", type: "search", placeholder: "搜索提供商…", oninput: (e) => { query = e.target.value; drawList(); } }),
        listBox),
      provider || creating ? providerDetail(provider) : h("div", { class: "card empty" }, h("b", {}, "还没有提供商"), "点击“添加提供商”配置第一个模型 API。"))));
  drawList();
}

function providerDetail(provider) {
  const isNew = !provider;
  const form = { id: provider?.id || "", label: provider?.label || "", base_url: provider?.base_url || "",
    profile: provider?.profile || "omnirouter.chat_completions", api_key: "" };
  let editingKey = isNew || !provider.key_configured;
  let showKey = false;
  const holder = h("div", { class: "card provider-detail" });
  const input = (key, attrs = {}) => h("input", { class: "input", value: form[key], oninput: (e) => (form[key] = e.target.value), ...attrs });
  const statusPill = () => {
    if (isNew) return null;
    const c = provider.connection;
    if (!c) return h("span", { class: "pill warn" }, "未验证");
    return c.ok ? h("span", { class: "pill ok", title: `检查于 ${fmtTime(c.checked_at)}` }, `已连接 · ${c.model_count} 个模型`)
      : h("span", { class: "pill bad", title: c.error }, "连接失败");
  };
  const draw = () => {
    const keyField = editingKey
      ? h("div", { class: "input-row" },
        h("input", { class: "input mono", type: showKey ? "text" : "password", autocomplete: "off", value: form.api_key,
          placeholder: isNew || !provider.key_configured ? "sk-…" : "留空则保留当前密钥", oninput: (e) => (form.api_key = e.target.value) }),
        h("button", { class: "btn", type: "button", onclick: () => { showKey = !showKey; draw(); } }, showKey ? "隐藏" : "显示"),
        !isNew && provider.key_configured ? h("button", { class: "btn", type: "button", onclick: () => { editingKey = false; form.api_key = ""; draw(); } }, "取消") : null)
      : h("div", { class: "input-row" },
        h("input", { class: "input mono", value: provider.key_masked, disabled: true }),
        h("button", { class: "btn", type: "button", onclick: () => { editingKey = true; draw(); } }, "更换"));
    put(holder,
      h("div", { class: "head" },
        h("span", { class: "provider-item" }, h("span", { class: "avatar" }, (form.label || form.id || "+").slice(0, 1).toUpperCase())),
        h("h2", {}, isNew ? "添加提供商" : provider.label), statusPill()),
      isNew ? h("div", { class: "field" }, h("label", {}, "预设"),
        h("select", { class: "select", onchange: (e) => {
          const preset = PRESETS[Number(e.target.value)];
          form.base_url = preset.base_url; form.profile = preset.profile;
          if (!form.label && e.target.value !== "0") form.label = preset.label;
          if (!form.id && e.target.value !== "0") form.id = preset.label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
          draw();
        } }, PRESETS.map((p, i) => h("option", { value: i }, p.label))),
        h("span", { class: "hint" }, "选择常见服务自动填写地址与协议；其他 OpenAI 兼容服务选“自定义”。")) : null,
      h("div", { class: "field" }, h("label", {}, "名称"), input("label", { placeholder: "例如：本地代理" })),
      isNew ? h("div", { class: "field" }, h("label", {}, "ID"), input("id", { placeholder: "小写字母、数字或 -，例如 my-api", class: "input mono" }),
        h("span", { class: "hint" }, "运行记录中用于区分提供商，保存后不可修改。")) : null,
      h("div", { class: "field" }, h("label", {}, "API 地址"), input("base_url", { placeholder: "https://…/v1", class: "input mono" }),
        h("span", { class: "hint" }, "OpenAI 兼容地址，通常以 /v1 结尾。")),
      h("div", { class: "field" }, h("label", {}, "API 密钥"), keyField,
        h("span", { class: "hint" }, !isNew && provider.key_configured
          ? (provider.key_source === "file" ? "已配置的密钥（来自服务器上的密钥文件）。更换后改由 Studio 保存。" : "已配置的密钥。")
          : "密钥保存在服务器的配置目录（权限 600），不会进入仓库或返回浏览器。")),
      h("div", { class: "field" }, h("label", {}, "协议"),
        h("select", { class: "select", onchange: (e) => (form.profile = e.target.value) },
          S.config.wire_profiles.map((p) => h("option", { value: p, selected: p === form.profile }, p))),
        h("span", { class: "hint" }, "Agent 使用 Responses 协议；网关按此转换为服务端支持的格式。多数服务选 omnirouter.chat_completions。")),
      !isNew && provider.connection && !provider.connection.ok ? h("div", { class: "notice error" }, provider.connection.error) : null,
      h("div", { class: "form-foot" },
        h("button", { class: "btn primary", onclick: save }, "保存"),
        !isNew ? h("button", { class: "btn", onclick: test }, "测试连接") : null,
        h("span", { class: "spacer" }),
        !isNew ? h("button", { class: "btn danger", onclick: remove }, "删除") : h("a", { class: "btn", href: "#settings/providers" }, "取消")));
  };
  async function save(e) {
    e.currentTarget.disabled = true;
    try {
      const saved = await api(`/api/providers/${encodeURIComponent(form.id)}`, { method: "PUT", json: form });
      delete S.models[saved.id];
      await loadProviders();
      toast("已保存");
      if (isNew || form.api_key) await api(`/api/providers/${saved.id}/test`, { method: "POST" }).catch(() => null);
      await loadProviders();
      location.hash = `#settings/providers/${saved.id}`;
      route();
    } catch (error) {
      toast(`保存失败：${error.message}`, true);
      e.currentTarget.disabled = false;
    }
  }
  async function test(e) {
    e.currentTarget.disabled = true;
    e.currentTarget.textContent = "测试中…";
    const result = await api(`/api/providers/${provider.id}/test`, { method: "POST" }).catch((error) => ({ ok: false, error: error.message }));
    toast(result.ok ? `连接正常：${result.model_count} 个模型，${result.latency_s}s` : `连接失败：${result.error}`, !result.ok);
    delete S.models[provider.id];
    await loadProviders();
    route();
  }
  async function remove() {
    if (!confirm(`删除提供商 ${provider.label}？\n其定义与 Studio 保存的密钥会被删除；运行记录不受影响。`)) return;
    try {
      await api(`/api/providers/${provider.id}`, { method: "DELETE" });
      await loadProviders();
      location.hash = "#settings/providers";
    } catch (error) {
      toast(`删除失败：${error.message}`, true);
    }
  }
  draw();
  return holder;
}

async function settingsModels(body, providerId) {
  const usable = S.providers.filter((p) => p.key_configured);
  if (!usable.length) {
    put(body, h("div", { class: "page" }, pageHead("模型能力"), h("div", { class: "card empty" }, h("b", {}, "没有可用的提供商"), "先配置提供商和密钥。")));
    return;
  }
  const provider = usable.find((p) => p.id === providerId) || usable[0];
  let query = "";
  let only = "all";
  const tableBox = h("div", {}, h("div", { class: "muted" }, "加载模型列表…"));
  put(body, h("div", { class: "page wide" },
    pageHead("模型能力", "机器人 Agent 需要模型能看图像并稳定调用工具。探测会发送两张合成图片并要求一次结构化工具调用。"),
    h("div", { class: "toolbar" },
      h("select", { class: "select", onchange: (e) => (location.hash = `#settings/models/${e.target.value}`) },
        usable.map((p) => h("option", { value: p.id, selected: p.id === provider.id }, p.label))),
      h("input", { class: "input", type: "search", placeholder: "搜索模型…", style: "width:240px", oninput: (e) => { query = e.target.value; draw(); } }),
      h("select", { class: "select", onchange: (e) => { only = e.target.value; draw(); } },
        h("option", { value: "all" }, "全部"), h("option", { value: "ok" }, "已验证"), h("option", { value: "bad" }, "未通过"), h("option", { value: "none" }, "未探测")),
      h("span", { class: "spacer" }),
      h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await loadModels(provider.id, true); draw(); e.currentTarget.disabled = false; } }, "刷新列表")),
    tableBox));
  let info;
  try { info = await loadModels(provider.id); } catch (error) {
    put(tableBox, h("div", { class: "notice error" }, `模型列表获取失败：${error.message}`));
    return;
  }
  const probing = new Set();
  const draw = () => {
    info = S.models[provider.id];
    const caps = info.capabilities || {};
    const q = query.toLowerCase();
    const state = (m) => (!caps[m] ? "none" : caps[m].vision && caps[m].tools ? "ok" : "bad");
    const shown = info.models.filter((m) => (!q || m.toLowerCase().includes(q)) && (only === "all" || state(m) === only));
    const verified = info.models.filter((m) => state(m) === "ok").length;
    put(tableBox,
      h("p", { class: "muted small" }, `${info.models.length} 个模型 · ${verified} 个已验证 · ${Object.keys(caps).length} 个已探测`),
      h("div", { class: "card table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "模型"), h("th", {}, "图像"), h("th", {}, "工具调用"), h("th", { class: "num" }, "耗时"), h("th", {}, "检测时间"), h("th", {}, "说明"), h("th", {}, ""))),
        h("tbody", {}, shown.map((m) => {
          const c = caps[m];
          const yes = (v) => h("span", { class: `tag ${v ? "good" : "bad"}` }, v ? "✓ 支持" : "✗ 未通过");
          return h("tr", {},
            h("td", { class: "mono" }, m),
            h("td", {}, c ? yes(c.vision) : h("span", { class: "faint" }, "—")),
            h("td", {}, c ? yes(c.tools) : h("span", { class: "faint" }, "—")),
            h("td", { class: "num" }, c && c.latency_s != null ? `${c.latency_s}s` : "—"),
            h("td", {}, c ? ago(c.checked_at) : "—"),
            h("td", { class: "muted small", style: "white-space:normal;max-width:320px" }, c ? c.note : ""),
            h("td", {}, h("button", { class: "btn small", disabled: probing.has(m), onclick: async () => {
              probing.add(m); draw();
              try {
                const result = await api(`/api/providers/${provider.id}/probe`, { method: "POST", json: { model: m } });
                S.models[provider.id].capabilities[m] = result;
                toast(`${m}：${result.vision && result.tools ? "可用于机器人 Agent" : "未通过"}`, !(result.vision && result.tools));
              } catch (error) { toast(`探测失败：${error.message}`, true); }
              probing.delete(m); draw(); loadProviders();
            } }, probing.has(m) ? "探测中…" : c ? "重新探测" : "探测")));
        })))));
  };
  draw();
}

function quotaRow(provider) {
  const value = h("span", { class: "hint", role: "status" });
  const refresh = h("button", { class: "btn small", onclick: async () => {
    refresh.disabled = true;
    try {
      const q = await api(`/api/providers/${encodeURIComponent(provider.id)}/quota`);
      value.textContent = q.unlimited ? "此密钥未设额度上限" : `剩余 ${q.remaining.toLocaleString()} 个供应商额度单位`;
    } catch (error) {
      value.textContent = error.message;
    } finally {
      refresh.disabled = false;
    }
  } }, "查询密钥额度");
  return h("div", { class: "toolbar" }, h("strong", {}, provider.label), refresh, value);
}

function settingsUsage(body) {
  const t = S.totals || { overall: {}, by_provider: [], by_model: [], by_day: [] };
  const o = t.overall;
  const metric = (k, v, sub) => h("div", { class: "card metric" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), sub ? h("div", { class: "sub" }, sub) : null);
  const days = t.by_day.slice(-30);
  const peak = Math.max(1, ...days.map((d) => d.total_tokens || 0));
  const table = (rows, label) => h("div", { class: "card table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, label), h("th", { class: "num" }, "运行"), h("th", { class: "num" }, "成功"), h("th", { class: "num" }, "请求"),
      h("th", { class: "num" }, "输入"), h("th", { class: "num" }, "缓存命中"), h("th", { class: "num" }, "输出"), h("th", { class: "num" }, "总计"))),
    h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, r.key), h("td", { class: "num" }, fmtInt(r.runs)), h("td", { class: "num" }, fmtInt(r.success)),
      h("td", { class: "num" }, fmtInt(r.requests)), h("td", { class: "num" }, fmtInt(r.input_tokens)), h("td", { class: "num" }, fmtInt(r.cached_input_tokens)),
      h("td", { class: "num" }, fmtInt(r.output_tokens)), h("td", { class: "num" }, fmtInt(r.total_tokens)))))));
  const cacheRate = o.input_tokens ? `缓存命中 ${((100 * (o.cached_input_tokens || 0)) / o.input_tokens).toFixed(0)}%` : null;
  put(body, h("div", { class: "page wide" },
    pageHead("用量", "按 API、模型与日期汇总的 token 用量，来自每次运行记录的模型响应"),
    ...S.providers.filter(p => p.key_configured && p.quota_supported).map(quotaRow),
    h("div", { class: "metrics" },
      metric("运行次数", fmtInt(o.runs), `成功 ${fmtInt(o.success)}`), metric("模型请求", fmtInt(o.requests)),
      metric("输入 tokens", fmtTokens(o.input_tokens), cacheRate), metric("输出 tokens", fmtTokens(o.output_tokens), o.reasoning_tokens ? `推理 ${fmtTokens(o.reasoning_tokens)}` : null),
      metric("总 tokens", fmtTokens(o.total_tokens))),
    h("div", { class: "card card-pad", style: "margin-bottom:16px" }, h("h2", {}, "每日 tokens"),
      days.length ? h("div", { class: "usage-bars" }, days.map((d) => h("div", { class: "bar", title: `${d.key}：${fmtInt(d.total_tokens)} tokens，${d.runs} 次运行` },
        h("i", { style: `height:${(100 * (d.total_tokens || 0)) / peak}%` }), h("span", {}, d.key.slice(5))))) : h("div", { class: "empty" }, "暂无数据")),
    h("div", { class: "section-title" }, "按 API"), table(t.by_provider, "API"),
    h("div", { class: "section-title", style: "margin-top:18px" }, "按模型"), table(t.by_model, "API · 模型")));
}

function settingsGeneral(body) {
  const s = { ...S.config.settings };
  const usable = S.providers.filter((p) => p.key_configured);
  const field = (label, control, hint) => h("div", { class: "field" }, h("label", {}, label), control, hint ? h("span", { class: "hint" }, hint) : null);
  const select = (key, options) => h("select", { class: "select", onchange: (e) => (s[key] = e.target.value || null) },
    h("option", { value: "" }, "（不指定）"), options.map(([v, l]) => h("option", { value: v, selected: s[key] === v }, l)));
  put(body, h("div", { class: "page" },
    pageHead("通用", "新建运行时的默认选择，以及服务器上的目录"),
    h("div", { class: "card card-pad", style: "max-width:640px" },
      field("默认 API", select("default_provider", usable.map((p) => [p.id, p.label]))),
      field("默认模型", h("input", { class: "input mono", value: s.default_model || "", placeholder: "例如 gpt-6-astra", oninput: (e) => (s.default_model = e.target.value || null) })),
      field("默认方法", select("default_method", S.config.methods.map((m) => [m.id, m.label]))),
      field("默认推理强度", select("default_effort", S.config.reasoning_efforts.map((e) => [e, e]))),
      field("运行时 wheel", h("input", { class: "input mono", value: s.runtime_wheel || "", placeholder: "~/dist/evoab_runtime-0.2.0-py3-none-any.whl",
        oninput: (e) => (s.runtime_wheel = e.target.value || null) }), "Benchmark 一键准备时安装到隔离运行时的 evoab-runtime wheel（服务器路径）；由 scripts/release/prepare.py 生成。"),
      h("div", { class: "form-foot", style: "display:flex;gap:8px" }, h("button", { class: "btn primary", onclick: async () => {
        try {
          S.config.settings = await api("/api/settings", { method: "PUT", json: s });
          applyDefaults(true);
          toast("已保存");
        } catch (error) { toast(`保存失败：${error.message}`, true); }
      } }, "保存"))),
    h("div", { class: "card card-pad", style: "max-width:640px;margin-top:14px" }, h("h2", {}, "目录"),
      field("运行目录", h("div", { class: "command" }, S.config.runs_root), "每次运行的记录、画面、轨迹与用量都保存在这里。"),
      field("配置目录", h("div", { class: "command" }, S.config.config_dir), "providers.json、keys.env（密钥，权限 600）、provider_state.json、studio.json。"))));
}

function applyDefaults(force = false) {
  const s = S.config.settings || {};
  const w = S.wizard;
  if (s.default_method && (force || !w.touched)) w.method = s.default_method;
  if (s.default_effort && (force || !w.touched)) w.effort = s.default_effort;
  if (s.default_provider && (force || !w.provider)) w.provider = s.default_provider;
  if (s.default_model && (force || !w.model)) w.model = s.default_model;
}

// -- startup -----------------------------------------------------------------

async function refresh() {
  try { await loadRuns(); } catch { return; }
  const page = current.page;
  if (page === "running") route();
  else if (page === "history" && pageHistory.refresh) pageHistory.refresh();
  else if (page === "run") {
    const run = findRun(current.arg);
    if (run) {
      renderRunTop(run);
      if (run.monitor_id && (!S.replay || S.replay.runKey !== run.key)) route();
      else if (!run.monitor_id) renderLaunchLog(run);
      if (S.replay && !isLive(run) && S.replay.source) {
        // The run finished: load the final state once and stop streaming.
        S.replay.source.close();
        S.replay.source = null;
        api(`/replay/api/runs/${run.monitor_id}`).then((state) => { if (S.replay) { S.replay.state = state; drawReplay(); } }).catch(() => {});
      }
    }
  } else if (page === "benchmarks" && S.benchmarks.some((b) => b.profiles.some((p) => p.status === "preparing"))) {
    await loadBenchmarks();
    route();
  }
}

async function init() {
  try {
    [S.config] = await Promise.all([api("/api/config"), loadProviders(), loadBenchmarks(), loadRuns()]);
  } catch (error) {
    put($("main"), h("div", { class: "page" }, h("div", { class: "notice error" }, `无法连接 Studio 服务：${error.message}`)));
    return;
  }
  $("hostInfo").textContent = S.config.runs_root;
  applyDefaults();
  window.addEventListener("hashchange", route);
  route();
  setInterval(() => { if (!document.hidden) refresh(); }, 5000);
}
init();
