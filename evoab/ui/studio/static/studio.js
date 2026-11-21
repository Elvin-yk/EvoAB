// EvoAB Studio: new run, history, run detail with replay, benchmarks, settings.
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
  ready: "已就绪", not_prepared: "未安装", preparing: "安装中", setup_failed: "安装失败",
  external: "需手动配置", real: "真机",
};
// LIBERO-PRO suite names are "<base>[_<perturbation>]"; show them as base suite × perturbation.
const LIBERO_BASE = { libero_10: "LIBERO-10（长程）", libero_90: "LIBERO-90", libero_goal: "LIBERO-Goal",
  libero_object: "LIBERO-Object", libero_spatial: "LIBERO-Spatial" };
const PERTURBATIONS = [["", "原始"], ["lan", "语言改写"], ["object", "物体属性"], ["swap", "位置交换"],
  ["task", "任务变化"], ["env", "场景环境"]];
function suiteParts(suite) {
  const m = /^(libero_(?:10|90|goal|object|spatial))(?:_(lan|object|swap|task|env))?$/.exec(suite || "");
  return m ? { base: m[1], variant: m[2] || "" } : null;
}
function suiteLabel(suite) {
  const p = suiteParts(suite);
  if (!p) return suite || "—";
  return `${LIBERO_BASE[p.base]} · ${PERTURBATIONS.find(([k]) => k === p.variant)[1]}`;
}
function methodLabel(value) {
  return String(value || "").split(" · ").map((part) => ({
    "Direct Policy": "直接策略", "Agentic Policy": "智能体策略", direct: "直接控制", hybrid: "混合控制",
    native: "原生执行器", codex: "Codex",
  })[part] || part).join(" · ");
}

function effortLabel(value) {
  return ({ none: "关闭", minimal: "最低", low: "低", medium: "中", high: "高", xhigh: "很高",
    max: "最高" })[value] || value;
}

const taskName = (task) => String(task || "").split("/").pop();
const runTask = (run) => run.instruction || taskName(run.task) || "—";

function runBenchmark(run) {
  const custom = run.integration === "custom";
  const bench = S.benchmarks.find((b) => b.integration === run.integration && (!custom || b.name === run.profile));
  return bench?.title || (custom && run.profile) || run.integration || "—";
}

const runTitle = (run) => `#${run.short_id || "—"}`;
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
  wizard: { step: 0, kind: null, bench: null, task: null, suite: "all", query: "", method: "direct", effort: "low",
            provider: null, model: null, modelQuery: "" },
  history: { query: "", status: "all", bench: "all", model: "all", selected: new Set(), compare: false },
  replay: null,
};

async function loadRuns() {
  const data = await api("/api/runs");
  S.runs = data.runs;
  S.totals = data.totals;
}
async function loadBenchmarks() {
  const integrations = await api("/api/benchmarks");
  const catalog = integrations.filter((b) => b.kind === "simulation" && (PROJECT_SOURCES[b.integration] || b.integration === "custom"));
  catalog.push({
    integration: "libero", kind: "simulation", title: "LIBERO", simulator: "MuJoCo（robosuite）",
    embodiment: "Franka Panda", profiles: [], description: "官方终身机器人学习评测集，测试跨任务的知识迁移。",
  }, {
    integration: "metaworld", kind: "simulation", title: "MetaWorld MT50", simulator: "MuJoCo",
    embodiment: "Sawyer", profiles: [], description: "50 个单臂桌面操作任务。",
  });
  S.benchmarks = catalog;
  return catalog;
}
async function loadTasks(bench, profile, force = false) {
  const { integration } = bench;
  const key = `${integration}/${profile}`;
  if (!S.tasks[key] || force) S.tasks[key] = await api(`/api/benchmarks/${integration}/${profile}/tasks`);
  return S.tasks[key];
}
const findRun = (key) =>
  S.runs.find((r) => r.key === key || r.id === key) ||
  (key.startsWith("launch/") && S.runs.find((r) => r.launch_id === key.slice(7)));

// -- routing -----------------------------------------------------------------

let current = { page: null, arg: null };
function route() {
  const hash = location.hash.slice(1) || "new";
  const [section, ...rest] = hash.split("/");
  if (section === "running") { location.replace("#history"); return; }
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
  if (page === "benchmarks") { location.replace("#settings/benchmarks"); return; }
  const pages = { new: pageNew, history: pageHistory, run: pageRun, settings: pageSettings };
  (pages[page] || pageNew)(main, arg);
}
function pageHead(title, subtitle, ...actions) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", { text: title }), subtitle ? h("p", { text: subtitle }) : null),
    h("div", { class: "actions" }, ...actions));
}

// -- new run (wizard) --------------------------------------------------------

const KINDS = { simulation: "仿真", real: "真机" };
const STEPS = ["类型", "评测集", "任务", "方法", "模型", "确认"];

function wizardReady(upTo) {
  const w = S.wizard;
  const checks = [!!w.kind, !!w.bench, !!w.task, !!w.method, !!w.model];
  return checks.slice(0, upTo + 1).every(Boolean);
}
function selectedMethod() {
  return S.config.methods.find((m) => m.id === S.wizard.method);
}

function pageNew(main) {
  const w = S.wizard;
  const steps = h("nav", { class: "steps", "aria-label": "发起评测步骤" }, STEPS.map((name, i) => {
    const done = i < w.step;
    return h("button", {
      class: `step-tab${done ? " done" : ""}`,
      "aria-current": i === w.step ? "step" : null,
      "aria-label": `第 ${i + 1} 步，${name}${done ? "，已完成" : ""}`,
      disabled: !wizardReady(i - 1),
      onclick: () => { w.step = i; route(); },
    }, h("span", { class: "n", "aria-hidden": "true" }, done ? "✓" : i + 1), name);
  }));
  const body = h("div", { class: "step-body" });
  const renderers = [stepKind, stepBench, stepTask, stepMethod, stepModel, stepConfirm];
  renderers[w.step](body);
  const foot = w.step > 0 && h("div", { class: "step-foot" },
    h("button", { class: "btn", onclick: () => { w.step -= 1; route(); } }, "上一步"),
    w.step < 5 && h("button", { class: "btn primary", disabled: !wizardReady(w.step), onclick: () => { w.step += 1; route(); } }, "下一步"));
  put(main, h("div", { class: "page wide" },
    h("div", { class: "wizard" }, steps, body, foot)));
}

function stepKind(body) {
  const w = S.wizard;
  body.append(h("h1", { class: "wizard-title" }, "选择运行类型"),
    h("div", { class: "mode-grid" }, Object.entries(KINDS).map(([kind, label]) =>
      h("button", { class: `choice mode-choice${kind === w.kind ? " selected" : ""}`,
        onclick: () => {
          if (kind !== w.kind) Object.assign(w, { kind, bench: null, task: null, suite: "all", query: "" });
          w.step = 1; route();
        } },
        h("span", { class: "title" }, label),
        h("span", { class: "desc" }, kind === "simulation" ? "在模拟器中运行任务" : "在实体机器人上运行任务"),
        h("span", { class: "choice-arrow", "aria-hidden": "true" }, "→")))));
}

// The catalog and run picker share the same benchmark identity and hardware fields.
function benchmarkIdentity(bench, titleTag) {
  return [
    h(titleTag, { class: "bench-title" }, bench.title),
    h("span", { class: "bench-specs" },
      [ ["仿真器", bench.simulator], ["本体", bench.embodiment] ]
        .filter(([, value]) => value && value !== "—")
        .map(([label, value]) => h("span", {}, `${label}：${value}`))),
  ];
}

function benchmarkName(bench, profile) {
  return profile === "default" ? bench.title : `${bench.title} / ${profile}`;
}

function stepBench(body) {
  const w = S.wizard;
  const cards = [];
  for (const bench of S.benchmarks.filter((b) => b.kind === w.kind)) {
    for (const p of bench.profiles.filter((profile) => profile.launchable)) {
      const selected = w.bench?.integration === bench.integration && w.bench.profile === p.profile;
      cards.push(h("button", { class: `choice bench-choice${selected ? " selected" : ""}`,
        onclick: () => {
          if (!selected) selectBenchmark(bench, p);
          w.step = 2; route();
        } },
        benchmarkIdentity(bench, "span"),
        h("span", { class: "meta" }, [p.profile !== "default" && p.profile, `${p.task_count} 个评测条目`].filter(Boolean).join(" · "))));
    }
  }
  body.append(
    h("div", { class: "toolbar benchmark-heading" },
      h("h1", { class: "wizard-title" }, `可启动的${KINDS[w.kind]}环境`),
      h("a", { class: "btn small", href: "#settings/benchmarks" }, "评测集管理")),
    cards.length ? h("div", { class: "choice-grid" }, cards)
      : h("p", { class: "card empty" }, `目前没有可从网页启动的${KINDS[w.kind]}环境。`));
}

// Base suites as rows, perturbations as columns; each cell selects one suite.
function suiteMatrix(suites, total) {
  const w = S.wizard;
  const bases = [...new Set(Object.keys(suites).map((s) => suiteParts(s).base))].sort();
  const columns = PERTURBATIONS.filter(([k]) => Object.keys(suites).some((s) => suiteParts(s).variant === k));
  const cell = (suite, label) => suites[suite]
    ? h("button", { class: `matrix-cell${w.suite === suite ? " active" : ""}`, title: suite,
        onclick: () => { w.suite = suite; route(); } }, label ?? suites[suite])
    : h("span", { class: "matrix-empty" }, "—");
  return h("div", { class: "card suite-matrix" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, h("button", { class: `matrix-cell${w.suite === "all" ? " active" : ""}`,
      onclick: () => { w.suite = "all"; route(); } }, `全部 ${total}`)), columns.map(([k, label]) => h("th", {}, label, h("small", {}, k ? `_${k}` : ""))))),
    h("tbody", {}, bases.map((base) => h("tr", {}, h("th", {}, LIBERO_BASE[base]),
      columns.map(([k]) => h("td", {}, cell(k ? `${base}_${k}` : base))))))),
    h("p", { class: "small muted" }, "行是基础套件，列是扰动类型；单元格里的数字是可用任务数。"));
}

async function stepTask(body) {
  const w = S.wizard;
  body.append(h("div", { class: "muted" }, "加载任务…"));
  const tasks = await loadTasks(w.bench, w.bench.profile);
  const suites = {};
  for (const t of tasks) suites[t.suite] = (suites[t.suite] || 0) + 1;
  const list = h("div", { class: "list" });
  const renderList = () => {
    const q = w.query.trim().toLowerCase();
    const shown = tasks.filter((t) => (w.suite === "all" || t.suite === w.suite) &&
      (!q || taskName(t.task).toLowerCase().includes(q) || t.task.toLowerCase().includes(q) || (t.instruction || "").toLowerCase().includes(q)));
    put(list, ...(shown.length ? shown.map((t) => h("div", {
      class: `list-item${w.task && w.task.task === t.task ? " selected" : ""}`,
      onclick: () => { w.task = t; w.step = 3; route(); },
    },
      h("div", { class: "t", title: t.task }, taskName(t.task)),
      h("span", {}, t.difficulty ? h("span", { class: "tag" }, t.difficulty) : null, " ", h("span", { class: "tag" }, suiteLabel(t.suite))),
      h("div", { class: "s", title: t.instruction }, [t.instruction || "（无说明）", `本体 ${t.embodiment}`, t.category,
        t.max_steps ? `步数上限 ${t.max_steps}` : null].filter(Boolean).join(" · ")),
    )) : [h("div", { class: "empty" }, "没有匹配的任务")]));
  };
  const search = h("input", { class: "input", type: "search", placeholder: "搜索任务名或说明…", value: w.query, style: "width:260px",
    oninput: (e) => { w.query = e.target.value; renderList(); } });
  const picker = Object.keys(suites).every(suiteParts) ? suiteMatrix(suites, tasks.length) : h("div", { class: "chips" },
    [["all", `全部 ${tasks.length}`], ...Object.entries(suites).map(([s, n]) => [s, `${s} ${n}`])].map(([value, label]) =>
      h("button", { class: `chip-btn${w.suite === value ? " active" : ""}`, onclick: () => { w.suite = value; route(); } }, label)));
  put(body, picker, h("div", { class: "toolbar" }, h("b", {}, w.suite === "all" ? "全部任务" : suiteLabel(w.suite)),
    h("span", { class: "spacer" }), search), list);
  renderList();
}

function stepMethod(body) {
  const w = S.wizard;
  const groups = [["direct", "智能体控制"], ["hybrid", "智能体与策略协作"]];
  for (const [profile, label] of groups) {
    const methods = S.config.methods.filter((m) => m.action_profile === profile);
    body.append(h("section", { class: "method-group" },
      h("h2", { class: "section-title" }, label),
      methods.length ? h("div", { class: "choice-grid" }, methods.map((m) => h("button", {
        class: `choice${w.method === m.id ? " selected" : ""}`, "aria-pressed": String(w.method === m.id),
        onclick: () => { w.method = m.id; route(); },
      }, h("span", { class: "title" }, methodLabel(m.label)), h("span", { class: "desc" }, m.description))))
        : h("p", { class: "muted small" }, "当前评测集暂无可用方法。")));
  }
  if (selectedMethod()?.interaction === "direct_policy") body.append(directOptions());
}

const DIRECT_DEFAULTS = { representation: "abs_eef", horizon: "3:15", history: "15:7", blocks: ["gripper", "camera"] };
function directOptions() {
  const w = S.wizard;
  w.direct = w.direct || { ...DIRECT_DEFAULTS, blocks: [...DIRECT_DEFAULTS.blocks] };
  const d = w.direct;
  const seg = (label, key, options) => h("div", { class: "field" }, h("div", { class: "section-title" }, label),
    h("div", { class: "segmented" }, options.map(([value, text]) => h("button", { class: d[key] === value ? "active" : "",
      "aria-pressed": String(d[key] === value), onclick: () => { d[key] = value; route(); } }, text))));
  const block = (name, text, hint) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: d.blocks.includes(name),
      onchange: (e) => { d.blocks = e.target.checked ? [...new Set([...d.blocks, name])] : d.blocks.filter((b) => b !== name); } }),
    h("span", {}, h("b", {}, text), h("span", { class: "muted small" }, ` ${hint}`)));
  return h("section", { class: "method-group" },
    h("h2", { class: "section-title" }, "直接策略设置"),
    h("div", { class: "card direct-options" },
      seg("动作表示", "representation", [["abs_eef", "绝对末端位姿 (8D)"], ["delta_eef", "逐步末端增量 (7D)"]]),
      seg("每次输出的行数", "horizon", [["3:15", "3–15"], ["1:5", "1–5"], ["16:32", "16–32"], ["15:15", "固定 15"]]),
      seg("观测历史", "history", [["15:7", "t−14, t−7, t"], ["1:1", "仅当前"], ["30:15", "t−29, t−14, t"]]),
      h("div", { class: "field" }, h("div", { class: "section-title" }, "提示词块"),
        h("p", { class: "muted small" }, "本体与坐标系、执行时序、输出格式始终包含；几何信息由环境实测生成，可选。"),
        block("gripper", "夹爪几何", "抓取点与夹爪各轴在世界坐标中的方向、开口宽度"),
        block("camera", "相机几何", "每个相机的位置、朝向与归一化内参（来自环境标定）"))));
}
function directPayload() {
  const d = S.wizard.direct || DIRECT_DEFAULTS;
  return { representation: d.representation, horizon: d.horizon, history: d.history,
           prompt_blocks: ["robot", "timing", ...d.blocks].join(",") };
}

async function stepModel(body) {
  const w = S.wizard;
  const holder = h("div", {}, h("div", { class: "muted" }, "读取模型预设…"));
  body.append(holder);
  let m;
  try { m = await loadModelSettings(); } catch (error) { put(holder, h("div", { class: "notice error" }, `读取失败：${error.message}`)); return; }
  const manage = h("a", { class: "btn small", href: "#settings/models" }, "管理模型");
  if (!m.presets.length) {
    put(holder, h("div", { class: "card empty" }, h("b", {}, "还没有模型预设"), "先在设置中添加提供商并新建预设。", manage));
    return;
  }
  if (!m.presets.some((p) => p.name === w.model)) w.model = null;
  const label = (name) => m.providers.find((p) => p.name === name)?.label || name;
  const groups = {};
  for (const p of m.presets) (groups[p.resolved_provider || p.provider] ||= []).push(p);
  put(holder,
    h("div", { class: "toolbar" }, h("span", { class: "section-title", style: "margin:0" }, "模型预设"), h("span", { class: "spacer" }), manage),
    h("div", { class: "list" }, Object.entries(groups).map(([provider, items]) => [
      h("div", { class: "list-group" }, label(provider)),
      items.map((p) => h("div", { class: `list-item${w.model === p.name ? " selected" : ""}`,
        onclick: () => { w.model = p.name; if (p.reasoning_effort && S.config.reasoning_efforts.includes(p.reasoning_effort)) w.effort = p.reasoning_effort; route(); } },
        h("div", { class: "t mono" }, p.name),
        h("span", { class: "muted small" }, `${p.model} · 最多输出 ${fmtInt(p.max_tokens)} · ${fmtTokens(p.context_window_tokens)} 上下文${p.reasoning_effort ? ` · 预设推理 ${effortLabel(p.reasoning_effort)}` : ""}`)))])),
    h("div", { class: "model-effort" },
      h("div", { class: "section-title", id: "effort-label" }, "推理强度"),
      h("div", { class: "segmented", role: "group", "aria-labelledby": "effort-label" },
        S.config.reasoning_efforts.map((e) => h("button", { class: w.effort === e ? "active" : "",
          "aria-pressed": String(w.effort === e), onclick: () => { w.effort = e; route(); } }, effortLabel(e))))));
}

function commandPreview() {
  const w = S.wizard;
  const method = selectedMethod();
  const [profile, task] = w.bench.integration === "libero_pro" ? w.task.task.split("/") : [w.bench.profile, w.task.task];
  const parts = [
    "evoab run", `--integration ${w.bench.integration}`, "--deploy local", `--runtime-profile ${profile}`,
    `--task ${task}`, `--tool-profile ${method.tool_profile}`, "--no-sam3", `--agent ${method.agent}`,
    `--model ${w.model}`, `--reasoning-effort ${w.effort}`, `--interaction-mode ${method.interaction}`,
  ];
  if (method.interaction === "direct_policy") {
    const d = directPayload();
    parts.push(`--direct-representation ${d.representation}`, `--direct-horizon ${d.horizon}`, `--direct-history ${d.history}`,
      `--direct-prompt-blocks ${d.prompt_blocks}`);
  }
  return [...parts, "--monitor"].join(" \\\n  ");
}

function stepConfirm(body) {
  const w = S.wizard;
  if (!wizardReady(4)) {
    body.append(h("div", { class: "notice info" }, "请先完成前面的步骤。"));
    return;
  }
  const method = selectedMethod();
  const rows = [
    ["环境", `${benchmarkName(w.bench, w.bench.profile)}（${KINDS[w.kind]}）`], ["套件", `${suiteLabel(w.task.suite)}（${w.task.suite}）`], ["任务", taskName(w.task.task)],
    ["说明", w.task.instruction || "—"], ["本体", w.task.embodiment], ["步数上限", w.task.max_steps || "—"],
    ["方法", methodLabel(method.label)], ["推理强度", effortLabel(w.effort)],
    ["模型预设", w.model],
    ...(method.interaction === "direct_policy" ? [["直接策略设置", Object.entries(directPayload()).map(([k, v]) => `${k}=${v}`).join("  ")]] : []),
  ];
  body.append(
    h("div", { class: "card" }, h("table", { class: "table" }, h("tbody", {}, rows.map(([k, v]) =>
      h("tr", {}, h("td", { class: "muted", style: "width:120px" }, k), h("td", { style: "white-space:normal" }, v)))))),
    h("div", { class: "section-title", style: "margin-top:20px" }, "等价命令"),
    h("div", { class: "command" }, commandPreview()),
    h("p", { class: "muted small" }, "API 密钥保存在服务器上，不会出现在页面或命令中。"),
    h("button", { class: "btn primary", style: "margin-top:8px", onclick: launchRun }, "启动运行"));
}

async function launchRun(event) {
  const w = S.wizard;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/runs", {
      method: "POST",
      json: { integration: w.bench.integration, profile: w.bench.profile, task: w.task.task,
              model: w.model, reasoning_effort: w.effort, method: w.method,
              ...(selectedMethod()?.interaction === "direct_policy" ? { direct: directPayload() } : {}) },
    });
  } catch (error) {
    toast(`启动失败：${error.message}`, true);
    button.disabled = false;
    return;
  }
  Object.assign(S.history, { query: "", status: "all", bench: "all", model: "all" });
  toast("已提交评测");
  location.hash = "#history";
  await refresh();
}

async function stopRun(run, button) {
  if (!confirm(`停止运行 ${runTitle(run)}（${taskName(run.task)}）？\n会向运行进程发送中断信号，已记录的数据会保留。`)) return;
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
    if (q && ![r.task, r.instruction, runBenchmark(r), r.model, r.benchmark, r.provider, r.id].some((v) => (v || "").toLowerCase().includes(q))) return false;
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
  const heading = pageHead("运行记录");
  const count = h("p", {});
  heading.firstElementChild.append(count);
  const renderTable = () => {
    count.textContent = `共 ${S.runs.length} 次运行 · 运行目录 ${S.config.runs_root}`;
    const runs = filteredRuns();
    put(tableHolder, runs.length ? h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "运行 ID"), h("th", {}, "状态"), h("th", {}, "任务"), h("th", {}, "评测集"), h("th", {}, "方法"),
        h("th", {}, "模型"), h("th", { class: "num" }, "第几次"), h("th", { class: "num" }, "步数"), h("th", { class: "num" }, "用时"),
        h("th", { class: "num" }, "词元"), h("th", {}, "开始时间"))),
      h("tbody", {}, runs.map((r) => h("tr", { class: "clickable", onclick: () => (location.hash = `#${r.key}`) },
        h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", checked: f.selected.has(r.key), onchange: (e) => {
          if (e.target.checked) f.selected.add(r.key); else f.selected.delete(r.key);
          renderCompareBar();
        } })),
        h("td", { class: "mono" }, runTitle(r)),
        h("td", {}, pill(r.status)),
        h("td", { class: "task-cell", title: runTask(r) }, runTask(r)),
        h("td", {}, runBenchmark(r), r.benchmark && h("div", { class: "faint small" }, suiteLabel(r.benchmark))),
        h("td", {}, methodLabel(r.method)),
        h("td", {}, r.model || "—", h("div", { class: "faint small" }, r.provider || "")),
        h("td", { class: "num" }, r.attempt ?? "—"),
        h("td", { class: "num" }, r.live?.steps ?? r.environment_steps ?? "—"),
        h("td", { class: "num" }, fmtDuration(r.elapsed_s)),
        h("td", { class: "num" }, fmtTokens(r.usage.total_tokens)),
        h("td", {}, fmtTime(r.started_at)))))))
      : h("div", { class: "card empty" }, h("b", {}, S.runs.length ? "没有匹配的运行" : "还没有运行记录"), S.runs.length ? "调整筛选条件试试。" : "在“发起评测”里开始第一次运行。"));
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
    heading,
    h("div", { class: "toolbar" },
      h("input", { class: "input", type: "search", placeholder: "搜索任务、模型、API…", value: f.query, style: "width:280px",
        oninput: (e) => { f.query = e.target.value; renderTable(); } }),
      select("status", "全部状态", ["success", "failure", "error", "interrupted", "starting", "running", "failed"], RUN_STATUS),
      select("bench", "全部环境", uniq((r) => r.integration && `${r.integration}/${r.profile}`)),
      select("model", "全部模型", uniq((r) => r.model))),
    tableHolder, compareHolder));
  renderTable();
  pageHistory.refresh = renderTable;
}

function compareTable(runs) {
  const rows = [
    ["状态", (r) => pill(r.status)], ["任务", runTask], ["评测集", runBenchmark], ["套件", (r) => suiteLabel(r.benchmark)],
    ["方法", (r) => methodLabel(r.method)], ["模型", (r) => r.model], ["推理强度", (r) => effortLabel(r.reasoning_effort) || "—"],
    ["运行 ID", (r) => runTitle(r)], ["第几次", (r) => r.attempt ?? "—"], ["环境步数", (r) => r.environment_steps ?? "—"], ["用时", (r) => fmtDuration(r.elapsed_s)],
    ["模型请求", (r) => fmtInt(r.usage.requests)], ["输入词元", (r) => fmtInt(r.usage.input_tokens)],
    ["缓存命中", (r) => fmtInt(r.usage.cached_input_tokens)], ["输出词元", (r) => fmtInt(r.usage.output_tokens)],
    ["总词元", (r) => fmtInt(r.usage.total_tokens)], ["开始时间", (r) => fmtTime(r.started_at)],
  ];
  return h("div", { class: "card compare table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, h("th", {}, "对比"), runs.map((r) => h("th", {}, h("a", { href: `#${r.key}` }, `${runTitle(r)} ${r.model}`))))),
    h("tbody", {}, rows.map(([label, fn]) => h("tr", {}, h("td", {}, label), runs.map((r) => h("td", {}, fn(r))))))));
}

// -- run detail: see run.js -----------------------------------------------

async function renderLaunchLog(run) {
  const holder = $("rvLog");
  if (!holder) return;
  if (!run.launch_id) { put(holder); return; }
  let log = "";
  try { ({ log } = await api(`/api/runs/${run.launch_id}/log`)); } catch { return; }
  if (!$("rvLog")) return;
  const open = holder.querySelector("details")?.open ?? (!run.monitor_id);
  put(holder, h("details", { class: "card", style: "margin-top:14px", open },
    h("summary", { class: "card-pad", style: "cursor:pointer;font-weight:600" }, "启动日志"),
    h("div", { style: "padding:0 16px 16px" }, h("pre", { class: "log" }, log || "（空）"))));
}

const PROJECT_SOURCES = {
  robodojo: { repo: "RoboDojo-Benchmark/RoboDojo", paper: "2607.04434" },
  libero: { repo: "Lifelong-Robot-Learning/LIBERO", paper: "2306.03310" },
  libero_pro: { repo: "Zxy-MLlab/LIBERO-PRO", paper: "2510.03827", guide: "libero-extensions.md#libero-pro" },
  metaworld: { repo: "Farama-Foundation/Metaworld", paper: "1910.10897" },
  libero_plus: { repo: "sylvestf/LIBERO-plus", paper: "2510.13626", guide: "libero-extensions.md#prepare-a-native-runtime" },
  robotwin: { repo: "RoboTwin-Platform/RoboTwin", paper: "2506.18088", guide: "robotwin.md#prepare-and-run" },
  calvin: { repo: "mees/calvin", paper: "2112.03227", guide: "calvin.md#prepare-a-native-service" },
};

function projectLinks(bench) {
  const source = PROJECT_SOURCES[bench.integration];
  if (!source) return null;
  const link = (url, label) => h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label);
  return h("div", { class: "project-links" },
    link(`https://github.com/${source.repo}`, "GitHub 代码"),
    source.paper ? link(`https://arxiv.org/abs/${source.paper}`, "arXiv 论文") :
      h("span", {}, "暂未查到独立 arXiv 论文"));
}

function selectBenchmark(bench, p) {
  Object.assign(S.wizard, { kind: bench.kind, bench: { integration: bench.integration, profile: p.profile, title: bench.title }, task: null, suite: "all", query: "" });
}

function startWith(bench, p, task = null) {
  selectBenchmark(bench, p);
  Object.assign(S.wizard, { task, step: task ? 3 : 2 });
  location.hash = "#new";
}

async function prepare(bench, p, button) {
  const what = benchmarkName(bench, p.profile);
  if (!confirm(`在 Studio 服务器上安装 ${what} 环境？\n将下载仿真器依赖和任务资源。`)) return;
  button.disabled = true;
  try {
    await api(`/api/benchmarks/${bench.integration}/${p.profile}/setup`, { method: "POST" });
    toast(`已开始安装 ${what}`);
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
  const title = benchmarkName(bench, p.profile);
  const drawer = h("div", { class: "drawer", role: "dialog", "aria-label": title },
    h("div", { class: "drawer-head" }, h("h2", {}, title), h("span", { class: "tag" }, `${p.task_count} 个评测条目`),
      h("span", { class: "spacer" }), h("button", { class: "btn small", onclick: close }, "关闭")), body);
  document.body.append(backdrop, drawer);
  let tasks;
  try {
    tasks = await loadTasks(bench, p.profile, true);
  } catch (error) {
    put(body, h("div", { class: "notice error" }, `任务加载失败：${error.message}`));
    return;
  }
  const groups = new Map();
  for (const task of tasks) {
    if (!groups.has(task.suite)) groups.set(task.suite, []);
    groups.get(task.suite).push(task);
  }
  const list = h("div", {});
  let selectedTask = null;
  const selection = h("div", { class: "task-selection", hidden: true });
  const selectedName = h("span", { "aria-live": "polite" });
  if (p.launchable) {
    selection.append(selectedName, h("button", { class: "btn primary", onclick: () => {
      close(); startWith(bench, p, selectedTask);
    } }, "发起评测"));
    drawer.append(selection);
  }
  const search = h("input", { class: "input", type: "search", placeholder: "搜索任务或任务组", "aria-label": "搜索任务或任务组",
    oninput: () => render() });
  function suiteName(suite) {
    if (bench.integration !== "libero_pro") return suite;
    const match = /^libero_(10|goal|object|spatial)(?:_(env|lan|object|swap|task))?$/.exec(suite);
    if (!match) return suite;
    const family = { "10": "长程", goal: "目标", object: "物体", spatial: "空间" }[match[1]];
    const variant = { env: "环境变化", lan: "指令改写", object: "物体变化", swap: "位置变化", task: "任务变化" }[match[2]] || "原始任务";
    return `${family} · ${variant}`;
  }
  function taskRow(task) {
    const name = task.instruction || task.task;
    const text = h("span", { title: task.task }, name);
    const row = h("div", { class: "suite-task" });
    if (p.launchable) {
      const radio = h("input", { type: "radio", name: "drawer-task", checked: selectedTask?.task === task.task,
        onchange: () => {
          selectedTask = task;
          selectedName.textContent = `${suiteName(task.suite)} · ${name}`;
          selection.hidden = false;
        } });
      row.append(h("label", {}, radio, text));
    } else row.append(text);
    if (task.demo_url) {
      const demo = h("details", { class: "expert-demo" }, h("summary", {}, "专家演示"));
      demo.addEventListener("toggle", () => {
        if (!demo.open) {
          demo.querySelector("video")?.pause();
          return;
        }
        if (demo.querySelector("video")) return;
        const video = h("video", { src: task.demo_url, controls: true, playsinline: true,
          preload: "metadata", "aria-label": `${name} · 专家演示`,
          onerror: () => { video.replaceWith(h("span", { role: "alert" }, "视频加载失败，请重新打开任务列表。")); } });
        demo.append(video);
      });
      row.append(demo);
    }
    return row;
  }
  function render() {
    const query = search.value.trim().toLowerCase();
    const sections = [];
    for (const [suite, entries] of groups) {
      const label = suiteName(suite);
      const matches = (label.toLowerCase().includes(query) || suite.toLowerCase().includes(query)) ? entries :
        entries.filter((t) => `${t.task} ${t.instruction}`.toLowerCase().includes(query));
      if (!matches.length) continue;
      sections.push(h("details", { class: "task-suite", open: Boolean(query) },
        h("summary", { title: suite }, label, h("span", { class: "muted small" }, `（${matches.length}）`)),
        matches.map(taskRow)));
    }
    put(list, ...(sections.length ? sections : [h("div", { class: "empty" }, "没有匹配的任务")]));
  }
  put(body, h("div", { class: "toolbar" }, search), list);
  render();
}

// -- settings ----------------------------------------------------------------
//
// 概览: recorded model calls and tokens. 模型: model presets and the providers
// they call. 评测集: benchmark runtimes, including user-defined ones.

const SETTINGS_TABS = [["overview", "概览"], ["models", "模型"], ["benchmarks", "评测集"]];
const API_LABELS = { chat_completions: "Chat Completions", responses: "Responses", anthropic_messages: "Anthropic Messages" };
const REASONING_FORMATS = [["", "无"], ["thinking_type", "thinking.type"], ["enable_thinking", "enable_thinking"], ["reasoning_split", "reasoning_split"]];

function pageSettings(main, arg) {
  const [tab] = (arg || "overview").split("/");
  const body = h("div", { class: "settings-body" });
  put(main, h("div", { class: "settings" },
    h("div", { class: "settings-nav" }, h("h1", {}, "设置"),
      h("nav", { class: "nav" }, SETTINGS_TABS.map(([id, label]) => h("a", { href: `#settings/${id}`, class: tab === id ? "active" : "" }, label)))),
    body));
  ({ overview: settingsOverview, models: settingsModels, benchmarks: settingsBenchmarks }[tab] || settingsOverview)(body);
}

function dialog(title, ...content) {
  const close = () => { backdrop.remove(); box.remove(); };
  const backdrop = h("div", { class: "drawer-backdrop", onclick: close });
  const box = h("div", { class: "modal", role: "dialog", "aria-label": title },
    h("div", { class: "modal-head" }, h("h2", {}, title), h("span", { class: "spacer" }), h("button", { class: "btn small ghost", onclick: close, "aria-label": "关闭" }, "×")),
    h("div", { class: "modal-body" }, ...content));
  document.body.append(backdrop, box);
  return { close, box };
}
const row = (label, control, hint) => h("label", { class: "form-row" }, h("span", { class: "form-label" }, label, hint ? h("small", {}, hint) : null), control);

// -- overview ----------------------------------------------------------------

async function settingsOverview(body) {
  const holder = h("div", {}, h("div", { class: "muted" }, "读取用量…"));
  put(body, h("div", { class: "page wide" }, pageHead("概览", "所有模型调用的 token 与请求统计（含失败与重试），以及按运行汇总的结果。"), holder));
  let u;
  try { u = await api("/api/usage"); } catch (error) { put(holder, h("div", { class: "notice error" }, error.message)); return; }
  const days = (u.days || []).slice(-30);
  const max = Math.max(1, ...days.map((d) => d.total_tokens));
  const W = 720, H = 150, bw = W / 30;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 18}`);
  svg.setAttribute("class", "usage-chart");
  const today = new Date();
  for (let i = 0; i < 30; i++) {
    const date = new Date(today.getTime() - (29 - i) * 86400000).toISOString().slice(0, 10);
    const d = days.find((x) => x.date === date);
    if (!d) continue;
    const cached = d.cache_read_tokens || 0, miss = Math.max(0, d.input_tokens - cached), out = d.output_tokens;
    let y = H;
    for (const [value, cls] of [[miss, "miss"], [cached, "cached"], [out, "out"]]) {
      const hgt = (H * value) / max;
      const r = document.createElementNS(ns, "rect");
      Object.entries({ x: i * bw + 2, y: y - hgt, width: bw - 4, height: hgt, class: cls }).forEach(([k, v]) => r.setAttribute(k, v));
      const t = document.createElementNS(ns, "title"); t.textContent = `${date} · ${fmtTokens(d.total_tokens)} 词元 · ${d.requests} 次请求`;
      r.append(t); svg.append(r); y -= hgt;
    }
  }
  for (const [i, label] of [[0, new Date(today - 29 * 86400000).toISOString().slice(5, 10)], [29, today.toISOString().slice(5, 10)]]) {
    const t = document.createElementNS(ns, "text");
    Object.entries({ x: i * bw + (i ? bw : 0), y: H + 14, "text-anchor": i ? "end" : "start", class: "lbl" }).forEach(([k, v]) => t.setAttribute(k, v));
    t.textContent = label; svg.append(t);
  }
  const totals = S.totals || {};
  put(holder,
    h("div", { class: "card usage-card" },
      h("div", { class: "usage-head" }, h("div", {}, h("div", { class: "muted small" }, "近 30 天词元"), h("div", { class: "usage-big" }, fmtTokens(u.total_tokens_30d || 0))),
        h("div", { class: "usage-facts" },
          h("span", {}, h("b", {}, fmtInt(u.requests_30d || 0)), " 次请求"),
          h("span", {}, h("b", {}, fmtInt(u.failed_requests_30d || 0)), " 次失败"),
          h("span", {}, h("b", {}, `${Math.round((u.cache_read_rate_30d || 0) * 100)}%`), " 缓存命中"),
          h("span", {}, h("b", {}, fmtInt(u.active_days_30d || 0)), " 个活跃日"))),
      svg,
      h("div", { class: "usage-legend" }, h("span", { class: "k miss" }, "未缓存输入"), h("span", { class: "k cached" }, "缓存输入"), h("span", { class: "k out" }, "输出"))),
    h("div", { class: "card" }, h("div", { class: "section-title" }, "按模型（近 30 天）"),
      (u.providers_30d || []).length ? h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["提供商", "模型", "请求", "输入", "缓存", "输出", "失败"].map((x) => h("th", {}, x)))),
        h("tbody", {}, u.providers_30d.map((r) => h("tr", {}, h("td", {}, r.provider), h("td", { class: "mono" }, r.model), h("td", {}, fmtInt(r.requests)),
          h("td", {}, fmtTokens(r.input_tokens)), h("td", {}, fmtTokens(r.cache_read_tokens)), h("td", {}, fmtTokens(r.output_tokens)), h("td", {}, fmtInt(r.failed_requests))))))
        : h("div", { class: "empty small" }, "还没有模型调用记录。")),
    h("div", { class: "card" }, h("div", { class: "section-title" }, "按运行"),
      (totals.by_model || []).length ? h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["模型", "运行", "成功", "请求", "输入", "输出"].map((x) => h("th", {}, x)))),
        h("tbody", {}, totals.by_model.map((r) => h("tr", {}, h("td", { class: "mono" }, r.key), h("td", {}, fmtInt(r.runs)), h("td", {}, fmtInt(r.success)),
          h("td", {}, fmtInt(r.requests)), h("td", {}, fmtTokens(r.input_tokens)), h("td", {}, fmtTokens(r.output_tokens)))))) : h("div", { class: "empty small" }, "还没有运行。")));
}

// -- models ------------------------------------------------------------------

async function loadModelSettings() { return (S.modelSettings = await api("/api/models")); }

async function settingsModels(body) {
  const holder = h("div", {}, h("div", { class: "muted" }, "读取模型设置…"));
  put(body, h("div", { class: "page wide" }, pageHead("模型", "发起评测时选择一个模型预设；预设决定调用哪个提供商的哪个模型，以及推理强度、输出上限等参数。",
    h("button", { class: "btn", onclick: () => providersDialog() }, "管理提供商"),
    h("button", { class: "btn primary", onclick: () => presetDialog(null) }, "＋ 新建预设")), holder));
  let m;
  try { m = await loadModelSettings(); } catch (error) { put(holder, h("div", { class: "notice error" }, error.message)); return; }
  const label = (name) => m.providers.find((p) => p.name === name)?.label || name;
  put(holder, m.presets.length
    ? h("div", { class: "preset-list" }, m.presets.map((p) => h("div", { class: "card preset" },
        h("span", { class: "avatar" }, (p.name || "?").slice(0, 1).toUpperCase()),
        h("div", { class: "preset-main" }, h("b", { class: "mono" }, p.name),
          h("div", { class: "muted small" }, `${label(p.resolved_provider || p.provider)} · ${p.model} · ${fmtTokens(p.context_window_tokens)} 上下文 · 推理 ${effortLabel(p.reasoning_effort) || "默认"} · 最多输出 ${fmtInt(p.max_tokens)}`)),
        h("button", { class: "btn small", onclick: () => presetDialog(p) }, "配置"))))
    : h("div", { class: "card empty" }, h("b", {}, "还没有模型预设"), "先在“管理提供商”中添加 API，再新建预设。"));
}

async function presetDialog(preset) {
  const m = S.modelSettings || await loadModelSettings();
  const providers = m.providers.filter((p) => p.configured);
  if (!providers.length) { toast("请先添加提供商", true); providersDialog(); return; }
  const v = { name: preset?.name || "", provider: preset?.provider && preset.provider !== "auto" ? preset.provider : (preset?.resolved_provider || providers[0].name),
    model: preset?.model || "", reasoning_effort: preset?.reasoning_effort || "", max_tokens: preset?.max_tokens || 8192,
    temperature: preset?.temperature ?? 0.1, context_window_tokens: preset?.context_window_tokens || 200000 };
  const models = h("datalist", { id: "presetModels" });
  const loadList = async () => {
    put(models);
    try {
      const r = await api(`/api/models/providers/${encodeURIComponent(v.provider)}/models`);
      put(models, (r.models || []).map((x) => h("option", { value: typeof x === "string" ? x : x.id })));
    } catch { /* the list is advisory */ }
  };
  const input = (key, attrs = {}) => h("input", { class: "input", value: v[key], oninput: (e) => { v[key] = e.target.value; }, ...attrs });
  const efforts = preset?.reasoning_effort_values?.length ? preset.reasoning_effort_values : ["", "none", "minimal", "low", "medium", "high", "xhigh"];
  const error = h("div", { class: "notice error", hidden: true });
  const save = async () => {
    const payload = { name: v.name.trim(), provider: v.provider, model: v.model.trim(), reasoning_effort: v.reasoning_effort,
      max_tokens: Number(v.max_tokens), temperature: Number(v.temperature), context_window_tokens: Number(v.context_window_tokens) };
    try {
      await api(preset ? `/api/models/presets/${encodeURIComponent(preset.name)}` : "/api/models/presets", { method: preset ? "PUT" : "POST", json: payload });
      d.close(); toast("已保存"); route();
    } catch (e) { error.textContent = e.message; error.hidden = false; }
  };
  const remove = async () => {
    if (!confirm(`删除预设 ${preset.name}？`)) return;
    try { await api(`/api/models/presets/${encodeURIComponent(preset.name)}`, { method: "DELETE" }); d.close(); toast("已删除"); route(); }
    catch (e) { error.textContent = e.message; error.hidden = false; }
  };
  const d = dialog(preset ? "配置预设" : "新建预设",
    row("预设名称", input("name", { placeholder: "例如 gpt-6-astra" })),
    row("提供商", h("select", { class: "input", onchange: (e) => { v.provider = e.target.value; loadList(); } },
      providers.map((p) => h("option", { value: p.name, selected: p.name === v.provider }, p.label || p.name)))),
    row("模型", h("div", {}, input("model", { list: "presetModels", placeholder: "模型 ID" }), models)),
    row("推理强度", h("select", { class: "input", onchange: (e) => { v.reasoning_effort = e.target.value; } },
      efforts.map((e) => h("option", { value: e, selected: e === (v.reasoning_effort || "") }, effortLabel(e) || "默认")))),
    h("details", { class: "sys" }, h("summary", {}, "高级选项"),
      row("最大输出词元", input("max_tokens", { type: "number", min: 1 })),
      row("温度", input("temperature", { type: "number", step: "0.05", min: 0, max: 2 })),
      row("上下文窗口", input("context_window_tokens", { type: "number", min: 1 }))),
    error,
    h("div", { class: "modal-foot" }, preset ? h("button", { class: "btn danger", onclick: remove }, "删除") : null, h("span", { class: "spacer" }),
      h("button", { class: "btn primary", onclick: save }, "保存")));
  loadList();
}

async function providersDialog() {
  const m = await loadModelSettings();
  const configured = m.providers.filter((p) => p.configured);
  const builtins = m.providers.filter((p) => !p.configured && !p.is_custom && !p.oauth && p.name !== "custom");
  const d = dialog("管理提供商",
    configured.length ? configured.map((p) => h("div", { class: "provider-row" },
      h("span", { class: "avatar" }, (p.label || p.name).slice(0, 1).toUpperCase()),
      h("div", { class: "preset-main" }, h("b", {}, p.label || p.name), h("div", { class: "muted small mono" }, p.api_base || p.default_api_base || "")),
      h("button", { class: "btn small", onclick: () => { d.close(); providerDialog(p); } }, "配置")))
      : h("div", { class: "empty small" }, "还没有提供商。"),
    h("div", { class: "modal-foot" },
      builtins.length ? h("select", { class: "input", style: "max-width:220px", onchange: (e) => { const p = builtins.find((x) => x.name === e.target.value); if (p) { d.close(); providerDialog(p); } } },
        h("option", { value: "" }, "添加内置提供商…"), builtins.map((p) => h("option", { value: p.name }, p.label || p.name))) : null,
      h("span", { class: "spacer" }),
      h("button", { class: "btn primary", onclick: () => { d.close(); providerDialog(null); } }, "＋ 添加自定义提供商")));
}

function providerDialog(p) {
  const custom = !p || p.is_custom;
  const providerApi = p?.api || {};
  const v = { name: p?.label || "", api_base: p?.api_base || "", api_key: "", thinking_style: p?.thinking_style || "",
    use_proxy: !!p?.use_proxy, apis: providerApi.supported_apis || providerApi.supportedApis || (custom ? ["chat_completions"] : []),
    preferred: providerApi.preferred_api || providerApi.preferredApi || "",
    headers: p?.extra_headers ? JSON.stringify(p.extra_headers) : "", query: p?.extra_query ? JSON.stringify(p.extra_query) : "" };
  const input = (key, attrs = {}) => h("input", { class: "input", value: v[key], oninput: (e) => { v[key] = e.target.value; }, ...attrs });
  const error = h("div", { class: "notice error", hidden: true });
  const save = async () => {
    const payload = { api_base: v.api_base.trim() || null, thinking_style: v.thinking_style || null, use_proxy: v.use_proxy };
    if (v.api_key) payload.api_key = v.api_key;
    if (custom) {
      payload.name = v.name.trim();
      if (v.apis.length) payload.api = { supportedApis: v.apis, preferredApi: v.apis.includes(v.preferred) ? v.preferred : v.apis[0] };
    }
    try {
      if (v.headers) payload.extra_headers = JSON.parse(v.headers);
      if (v.query) payload.extra_query = JSON.parse(v.query);
    } catch { error.textContent = "请求头和查询参数需要填写 JSON 对象"; error.hidden = false; return; }
    try {
      if (p) { if (!custom) delete payload.name; await api(`/api/models/providers/${encodeURIComponent(p.name)}`, { method: "PUT", json: payload }); }
      else await api("/api/models/providers", { method: "POST", json: payload });
      d.close(); toast("已保存"); route();
    } catch (e) { error.textContent = e.message; error.hidden = false; }
  };
  const remove = async () => {
    if (!confirm(`删除提供商 ${p.label || p.name}？`)) return;
    try { await api(`/api/models/providers/${encodeURIComponent(p.name)}`, { method: "DELETE" }); d.close(); toast("已删除"); route(); }
    catch (e) { error.textContent = e.message; error.hidden = false; }
  };
  const d = dialog(p ? `配置 ${p.label || p.name}` : "添加自定义提供商",
    custom ? row("名称", input("name", { placeholder: "例如 我的 API" })) : null,
    row("API 地址", input("api_base", { placeholder: p?.default_api_base || "https://host/v1" })),
    row("API 密钥", input("api_key", { type: "password", autocomplete: "off", placeholder: p?.configured ? "已设置，留空保持不变" : "" }), "只保存在服务器上"),
    custom ? row("支持的接口", h("div", { class: "checks" }, Object.entries(API_LABELS).map(([k, label]) => h("label", { class: "check" },
      h("input", { type: "checkbox", checked: v.apis.includes(k), onchange: (e) => { v.apis = e.target.checked ? [...v.apis, k] : v.apis.filter((x) => x !== k); } }), label)))) : null,
    custom ? row("默认接口", h("select", { class: "input", onchange: (e) => { v.preferred = e.target.value; } },
      Object.entries(API_LABELS).map(([k, label]) => h("option", { value: k, selected: k === v.preferred }, label)))) : null,
    h("details", { class: "sys" }, h("summary", {}, "高级选项"),
      row("推理字段格式", h("select", { class: "input", onchange: (e) => { v.thinking_style = e.target.value; } },
        REASONING_FORMATS.map(([k, label]) => h("option", { value: k, selected: k === v.thinking_style }, label))), "部分接口用不同字段开启推理"),
      row("使用代理", h("input", { type: "checkbox", checked: v.use_proxy, onchange: (e) => { v.use_proxy = e.target.checked; } })),
      row("额外请求头", input("headers", { placeholder: '{"X-Key": "value"}' })),
      row("额外查询参数", input("query", { placeholder: '{"api-version": "2024-10-01"}' }))),
    error,
    h("div", { class: "modal-foot" }, p && p.is_custom ? h("button", { class: "btn danger", onclick: remove }, "删除") : null, h("span", { class: "spacer" }),
      h("button", { class: "btn primary", onclick: save }, "保存")));
}

// -- benchmarks ----------------------------------------------------------------

function settingsBenchmarks(body) {
  const s = S.config.settings || {};
  const wheel = h("input", { class: "input mono", value: s.runtime_wheel || "", placeholder: "/path/to/evoab_runtime-…-py3-none-any.whl" });
  put(body, h("div", { class: "page wide" },
    pageHead("评测集", "已接入的评测集与其运行环境；也可以按通用接口接入自己的评测集。",
      h("button", { class: "btn", onclick: async (e) => { e.currentTarget.disabled = true; await loadBenchmarks(); route(); toast("已重新检查"); } }, "重新检查"),
      h("button", { class: "btn primary", onclick: () => customBenchmarkDialog() }, "＋ 添加评测集")),
    h("div", { class: "card" }, row("运行时安装包", h("div", { class: "row" }, wheel,
      h("button", { class: "btn", onclick: async () => { S.config.settings = await api("/api/settings", { method: "PUT", json: { runtime_wheel: wheel.value.trim() || null } }); toast("已保存"); } }, "保存")),
      "安装评测环境时装入的 evoab-runtime wheel；留空则从软件源安装")),
    benchmarkCards()));
}

function benchmarkCards() {
  const profileRow = (bench, p) => {
    const guide = PROJECT_SOURCES[bench.integration]?.guide;
    const canInstall = ["auto", "custom"].includes(bench.setup) && ["not_prepared", "setup_failed"].includes(p.status);
    return [
      h("div", { class: "profile-row" },
        h("strong", {}, p.profile === "default" ? "运行环境" : `运行环境 · ${p.profile}`),
        pill(p.status, BENCH_STATUS),
        h("span", { class: "acts" },
          canInstall ? h("button", { class: "btn small", onclick: (e) => prepare(bench, p, e.currentTarget) },
            p.status === "setup_failed" ? "重新检查" : bench.setup === "custom" ? "检查环境" : "安装环境") : null,
          p.status === "external" && guide ? h("a", { class: "btn small", target: "_blank", rel: "noopener noreferrer",
            href: `https://github.com/Elvin-yk/EvoAB/blob/main/docs/${guide}` }, "配置指南") : null,
          p.status === "ready" && p.task_count > 0 ? h("button", { class: "btn small", onclick: () => openTasks(bench, p) }, `浏览任务（${p.task_count}）`) : null,
          p.launchable ? h("button", { class: "btn small", onclick: () => startWith(bench, p) }, "发起评测") : null,
          bench.setup === "custom" ? h("button", { class: "btn small", onclick: () => customBenchmarkDialog(bench.name) }, "清单") : null,
          bench.setup === "custom" ? h("button", { class: "btn small danger", onclick: () => removeCustom(bench.name) }, "移除") : null)),
      p.setup_log && ["preparing", "setup_failed"].includes(p.status) ? h("pre", { class: "profile-log log" }, p.setup_log) : null,
    ];
  };
  return S.benchmarks.map((b) => h("article", { class: "card bench" },
    h("div", { class: "bench-head" }, benchmarkIdentity(b, "h2"), b.setup === "custom" ? h("span", { class: "tag" }, "自定义") : projectLinks(b)),
    b.profiles.length ? b.profiles.map((p) => profileRow(b, p)) :
      h("div", { class: "profile-row" }, h("strong", {}, "运行环境"), h("span", { class: "muted" }, b.description?.startsWith("Invalid") ? b.description : "仅收录资料，暂不支持运行"))));
}

async function customBenchmarkDialog(name) {
  let text;
  try {
    const response = await fetch(name ? `/api/benchmarks/custom/${encodeURIComponent(name)}` : "/api/benchmarks/custom/template");
    text = await response.text();
  } catch (error) { toast(error.message, true); return; }
  const editor = h("textarea", { class: "input mono manifest-editor", spellcheck: "false" }, text);
  const error = h("div", { class: "notice error", hidden: true });
  const save = async (button) => {
    button.disabled = true;
    try {
      const r = await api("/api/benchmarks/custom", { method: "POST", json: { manifest: editor.value } });
      d.close(); toast(`已登记 ${r.name}，正在检查环境`); await loadBenchmarks(); route();
    } catch (e) { error.textContent = e.message; error.hidden = false; button.disabled = false; }
  };
  const d = dialog(name ? `评测集清单 · ${name}` : "添加评测集",
    h("p", { class: "small muted" }, "评测集提供一个 make(task_id, seed) 函数，返回 Gym 风格的环境（reset / step，观测为字典）。清单说明在哪个 Python 里导入它、观测里哪些键是相机图像和末端位姿、动作怎么写、怎样判定成功。保存后会在该 Python 中检查能否导入并列出任务。",
      h("a", { href: "https://github.com/Elvin-yk/EvoAB/blob/main/docs/custom-benchmarks.md", target: "_blank", rel: "noopener" }, " 接口说明")),
    editor, error,
    h("div", { class: "modal-foot" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: (e) => save(e.currentTarget) }, name ? "保存并重新检查" : "校验并添加")));
  d.box.classList.add("wide");
}

async function removeCustom(name) {
  if (!confirm(`移除评测集 ${name}？已有的运行记录不受影响。`)) return;
  try { await api(`/api/benchmarks/custom/${encodeURIComponent(name)}`, { method: "DELETE" }); toast("已移除"); await loadBenchmarks(); route(); }
  catch (e) { toast(e.message, true); }
}

function applyDefaults(force = false) {
  const s = S.config.settings || {};
  const w = S.wizard;
  if (force || !w.touched) w.method = S.config.methods.find((m) => m.id === s.default_method)?.id || S.config.methods[0].id;
  if (s.default_effort && (force || !w.touched)) w.effort = s.default_effort;
  if (s.default_model && (force || !w.model)) w.model = s.default_model;
}

// -- startup -----------------------------------------------------------------

async function refresh() {
  try { await loadRuns(); } catch { return; }
  const page = current.page;
  if (page === "history" && pageHistory.refresh) pageHistory.refresh();
  else if (page === "run") {
    const run = findRun(current.arg);
    if (run) refreshRun(run);
  } else if (page === "settings" && current.arg.startsWith("benchmarks") && S.benchmarks.some((b) => b.profiles.some((p) => p.status === "preparing"))) {
    await loadBenchmarks();
    route();
  }
}

async function init() {
  try {
    [S.config] = await Promise.all([api("/api/config"), loadBenchmarks(), loadRuns()]);
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
