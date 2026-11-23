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
const BENCH_STATUS = { ready: "已就绪", not_prepared: "未部署", preparing: "部署中", setup_failed: "部署失败" };
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
    "Direct Policy": "直接策略", "Agentic Policy": "智能体策略", "Agent + Policy": "智能体 + 策略",
    direct: "直接控制", hybrid: "混合控制",
    native: "原生执行器", codex: "Codex",
  })[part] || part).join(" · ");
}

// Studio-only copy for the method cards; the execution mode still comes from the API.
const METHOD_COPY = {
  direct_policy: {
    title: "LLM 直接给动作数字",
    desc: "LLM 读取当前画面和历史观测，按固定格式一次交出 8D 绝对位姿或 7D 增量动作数字，由环境控制器逐行执行。它不自选机器人工具，也没有独立的 VLA 动作模型。",
  },
  agentic: {
    title: "Agent 选择工具（Tool）",
    desc: "Agent 看着当前画面和历史观测，自己挑机器人工具（移动、抓取、感知等），每次按返回结果决定下一步，动作由环境执行。没有独立的 VLA 动作模型。",
  },
  agent_policy: {
    title: "Agent 调用 VLA",
    desc: "Agent 通过 Hybrid 动作工具调用 VLA（视觉-语言-动作模型）：VLA 把图像和指令转成动作片段，Agent 决定何时调用并检查结果，再由环境控制器执行。",
  },
};
function methodCopy(method) {
  return METHOD_COPY[method.id] || { title: methodLabel(method.label), desc: method.description };
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
  agentModelSettings: new Map(),
  wizard: { step: 0, kind: null, bench: null, task: null, suite: "all", query: "", method: "direct_policy", agent: null,
            agentOptions: new Map(), effort: "low", provider: null, model: null, modelQuery: "" },
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
  // Only benchmarks Studio can deploy in one step (plus registered custom ones) are listed.
  S.benchmarks = integrations.filter((b) => b.kind === "simulation"
    && ((PROJECT_SOURCES[b.integration] && b.deploy) || b.integration === "custom"));
  return S.benchmarks;
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
let runRefreshTimer;
function route() {
  const hash = location.hash.slice(1) || "new";
  const [section, ...rest] = hash.split("/");
  if (section === "running") { location.replace("#history"); return; }
  const page = section === "launch" ? "run" : section;
  const arg = section === "launch" ? hash : rest.join("/");
  const same = current.page === page && current.arg === arg;
  if (!same) {
    clearInterval(runRefreshTimer);
    if (page === "settings" || current.page === "settings") {
      S.modelSettings = null;
      S.agentModelSettings.clear();
    }
    if (page === "history" || page === "run" || (page === "settings" && arg === "benchmarks")) {
      runRefreshTimer = setInterval(() => { if (!document.hidden) refresh(); }, 5000);
    }
  }
  current = { page, arg };
  for (const link of document.querySelectorAll(".main-nav [data-page]")) {
    const target = link.dataset.page;
    const active = target === page || (page === "run" && target === "history");
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  for (const link of $("settingsLinks").querySelectorAll("a")) {
    if (page === "settings" && link.hash === `#settings/${arg || "overview"}`) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
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
  const agent = selectedAgent();
  const compatiblePreset = !agent?.filters_presets ||
    S.agentModelSettings.get(agent.id)?.presets.some((p) => p.name === w.model);
  const checks = [!!w.kind, !!w.bench, !!w.task, !!agent, !!w.model && compatiblePreset];
  return checks.slice(0, upTo + 1).every(Boolean);
}
function availableMethods() {
  const integration = S.wizard.bench?.integration;
  return S.config.methods.map((method) => ({ ...method,
    agents: method.agents.filter((agent) => !agent.integrations || agent.integrations.includes(integration)),
  })).filter((method) => method.agents.length);
}
function selectedMethod() {
  return availableMethods().find((m) => m.id === S.wizard.method);
}
function selectedAgent() {
  return selectedMethod()?.agents.find((agent) => agent.id === S.wizard.agent);
}
function selectMethod(method, preferredAgent) {
  S.wizard.method = method?.id;
  S.wizard.agent = method?.agents.find((agent) => agent.id === preferredAgent)?.id ||
    method?.agents.find((agent) => agent.id === method.agent)?.id || method?.agents[0]?.id;
}
function goToWizardStep(step) {
  const w = S.wizard;
  if (step > w.step && w.step === 4) {
    const fields = document.querySelectorAll(".agent-options input");
    if ([...fields].some((input) => !input.reportValidity())) return;
  }
  w.step = step;
  route();
}

function pageNew(main) {
  const w = S.wizard;
  const body = h("div", { class: "step-body" });
  const renderers = [stepKind, stepBench, stepTask, stepMethod, stepModel, stepConfirm];
  renderers[w.step](body);
  const steps = h("nav", { class: "steps", "aria-label": "发起任务步骤" }, STEPS.map((name, i) => {
    const done = i < w.step;
    return h("button", {
      class: `step-tab${done ? " done" : ""}`,
      "aria-current": i === w.step ? "step" : null,
      "aria-label": `第 ${i + 1} 步，${name}${done ? "，已完成" : ""}`,
      disabled: !wizardReady(i - 1),
      onclick: () => goToWizardStep(i),
    }, h("span", { class: "n", "aria-hidden": "true" }, done ? "✓" : i + 1), name);
  }));
  const foot = w.step > 0 && h("div", { class: "step-foot" },
    h("button", { class: "btn", onclick: () => { w.step -= 1; route(); } }, "上一步"),
    w.step < 5 && h("button", { class: "btn primary", disabled: !wizardReady(w.step), onclick: () => goToWizardStep(w.step + 1) }, "下一步"));
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
  const method = selectedMethod();
  const groups = [
    ["direct", "直接控制", "模型直接输出末端动作，或由 Agent 调用机器人工具；两种方式都不接入动作模型（VLA）。"],
    ["hybrid", "策略协作", "Agent 调用动作模型（VLA）执行动作，适合已接入动作模型服务（actor）的评测集。"],
  ];
  body.append(h("h1", { class: "wizard-title" }, "选择运行方法"));
  for (const [profile, label, note] of groups) {
    const methods = availableMethods().filter((m) => m.action_profile === profile);
    const section = h("section", { class: "method-group" },
      h("h2", { class: "section-title" }, label),
      h("p", { class: "muted small", style: "margin:0 0 10px" }, note),
      methods.length ? h("div", { class: "choice-grid" }, methods.map((m) => {
        const copy = methodCopy(m);
        return h("button", {
          class: `choice${w.method === m.id ? " selected" : ""}`, "aria-pressed": String(w.method === m.id),
          onclick: () => { selectMethod(m, m.id === w.method ? w.agent : null); route(); },
        }, h("span", { class: "title" }, copy.title), h("span", { class: "desc" }, copy.desc));
      }))
        : h("p", { class: "muted small" }, profile === "hybrid"
          ? "暂无可用的策略协作 Agent。该模式需要评测集接入动作模型（VLA）；此处为空只说明当前没有 Agent 提供该模式。"
          : "当前评测集没有可用的直接控制 Agent，可在上一步换一个评测集。"));
    if (method?.action_profile === profile && method.interaction !== "direct_policy") {
      section.append(h("section", { class: "agent-picker", "aria-label": "选择 Agent 实现" },
        h("h3", { class: "section-title" }, "选择 Agent 实现"),
        h("p", { class: "muted small", style: "margin:0 0 10px" }, "同一方法可由不同 Agent 实现；Agent 决定工具调用和模型连接方式。"),
        h("div", { class: "choice-grid" }, method.agents.map((agent) => h("button", {
          class: `choice${w.agent === agent.id ? " selected" : ""}`, "aria-pressed": String(w.agent === agent.id),
          onclick: () => { w.agent = agent.id; route(); },
        }, h("span", { class: "title" }, agent.label), h("span", { class: "desc" }, agent.description))))));
    }
    body.append(section);
  }
  if (method?.interaction === "direct_policy") body.append(directOptions());
}

const DIRECT_DEFAULTS = { representation: "abs_eef", horizon: "3:15", history: "15:7", blocks: ["gripper", "camera"] };
const GRIPPER_NOTE = "夹爪一栏始终是绝对开合：0 表示张开，1 表示闭合，两种格式都一样。";
const REPRESENTATION_HELP = {
  abs_eef: "每行直接说清「移动到哪里、转到什么方向」。8 个数依次是：x、y、z 是抓取点在世界中的位置（米），qw、qx、qy、qz 描述目标朝向（按 wxyz 顺序），最后一个是夹爪开合。",
  delta_eef: "每行只说清「在上一个目标上移动多少、转多少」。7 个数依次是：dx、dy、dz 是位置移动量（米），drx、dry、drz 是旋转量（弧度，绕世界坐标轴），最后一个是夹爪开合。第一行从当前实际位姿算起。",
};
const HORIZON_HELP = "每轮让模型给几个动作目标。行数由模型在所选范围内自己决定，每行执行一个控制步。给得少，模型会更频繁地停下来重新看画面再决定；给得多，就一次连续执行更多目标，中间不再看图。";
const HISTORY_HELP = "按控制步计，不是秒。模型每次都拿到当前画面和往前若干步的画面：默认「当前、7 步前、14 步前」是 3 帧，「当前和 15 步前」是 2 帧。刚开局还没走过这些步，就只取已有的画面。";
function directOptions() {
  const w = S.wizard;
  w.direct = w.direct || { ...DIRECT_DEFAULTS, blocks: [...DIRECT_DEFAULTS.blocks] };
  const d = w.direct;
  const seg = (label, key, options, help) => h("div", { class: "field" },
    h("div", { class: "section-title" }, label),
    h("div", { class: "segmented" }, options.map(([value, text]) => h("button", { class: d[key] === value ? "active" : "",
      "aria-pressed": String(d[key] === value), onclick: () => { d[key] = value; route(); } }, text))),
    h("p", { class: "muted small", style: "margin:0" }, typeof help === "function" ? help(d[key]) : help));
  const block = (name, text, hint) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: d.blocks.includes(name),
      onchange: (e) => { d.blocks = e.target.checked ? [...new Set([...d.blocks, name])] : d.blocks.filter((b) => b !== name); } }),
    h("span", {}, h("b", {}, text), h("span", { class: "muted small" }, ` ${hint}`)));
  return h("section", { class: "method-group" },
    h("h2", { class: "section-title" }, "直接策略设置"),
    h("div", { class: "card direct-options" },
      h("p", { class: "muted small", style: "margin:0" }, "只影响直接策略。"),
      seg("动作表示（给目标位姿，或给每步的变化量）", "representation",
        [["abs_eef", "绝对位姿（8 个数）"], ["delta_eef", "增量位姿（7 个数）"]],
        (value) => `${REPRESENTATION_HELP[value]}${GRIPPER_NOTE}`),
      seg("动作块长度 H（每次推理输出几个动作目标）", "horizon",
        [["3:15", "3–15 个"], ["1:5", "1–5 个"], ["16:32", "16–32 个"], ["15:15", "固定 15 个"]], HORIZON_HELP),
      seg("观测历史（每个相机取哪些控制时刻的画面）", "history",
        [["15:7", "当前、7 步前、14 步前"], ["1:1", "仅当前"], ["30:15", "当前和 15 步前"]], HISTORY_HELP),
      h("div", { class: "field" },
        h("div", { class: "section-title" }, "额外提供给模型的信息"),
        h("p", { class: "muted small", style: "margin:0" },
          "本体与坐标系、执行时序、动作格式始终会传给模型，不能关闭。下面两项是环境实测的几何信息，默认开启；环境没有提供时会自动跳过。"),
        block("gripper", "夹爪几何", "开启后模型能读到：动作里的位置指两指之间的抓取点，夹爪各轴在世界中的朝向、两指张开的宽度，以及用测得开合值判断是否夹住物体的方法。"),
        block("camera", "相机几何", "开启后模型能读到：每个相机装在世界中的位置、朝哪个方向看、画面上下左右各对应什么方向，以及把画面里的位置换算成坐标用的焦距和中心点。")),
      h("label", { class: "field" },
        h("span", { class: "section-title" }, "自定义提示词（可选）"),
        h("textarea", { class: "input", rows: 3, maxlength: 8192,
          placeholder: "例如：接近物体时，先对齐夹爪再下降。",
          oninput: (e) => { d.custom_prompt = e.target.value; } }, d.custom_prompt || ""),
        h("small", { class: "muted" }, "追加到本次直接策略的每一轮提示词。留空使用默认提示词。"))));
}
function directPayload() {
  const d = S.wizard.direct || DIRECT_DEFAULTS;
  return { representation: d.representation, horizon: d.horizon, history: d.history,
           prompt_blocks: ["robot", "timing", ...d.blocks].join(","),
           ...(d.custom_prompt?.trim() ? { custom_prompt: d.custom_prompt } : {}) };
}

function agentOptions(agent) {
  if (!agent.options.length) return null;
  const values = S.wizard.agentOptions.get(agent.id) || {};
  S.wizard.agentOptions.set(agent.id, values);
  return h("div", { class: "agent-options card direct-options model-effort" }, agent.options.map((option) =>
    h("label", { class: "field" }, h("span", { class: "section-title" }, option.label),
      h("input", { class: "input", type: "number", min: option.minimum, step: 1,
        value: values[option.name] ?? "", oninput: (event) => {
          const input = event.target;
          input.setCustomValidity(input.value && !Number.isSafeInteger(input.valueAsNumber) ? "请输入有效整数。" : "");
          if (!input.value) delete values[option.name];
          else if (input.validity.valid) values[option.name] = input.valueAsNumber;
        } }), h("span", { class: "muted small" }, option.help))));
}

async function stepModel(body) {
  const w = S.wizard;
  const agent = selectedAgent();
  if (!agent) return;
  const m = agent.filters_presets ? S.agentModelSettings.get(agent.id) : S.modelSettings;
  const holder = h("div", {}, !m ? h("div", { class: "muted" }, "读取模型预设…") : null);
  body.append(holder);
  if (!m) {
    try {
      await loadModelSettings(agent.filters_presets ? agent.id : null);
      if (holder.isConnected) route();
    } catch (error) {
      put(holder, h("div", { class: "notice error" }, `读取失败：${error.message}`));
    }
    return;
  }
  const manage = h("a", { class: "btn small", href: "#settings/models" }, "管理模型");
  const incompatible = m.incompatible_presets?.length ? h("div", { class: "list model-effort" },
    h("div", { class: "list-group" }, "不兼容的模型预设"),
    m.incompatible_presets.map((p) => h("div", { class: "list-item", role: "button", "aria-disabled": "true" },
      h("div", { class: "t mono" }, p.name), h("div", { class: "s" }, p.reason)))) : null;
  if (!m.presets.length) {
    w.model = null;
    put(holder, h("div", { class: "card empty" }, h("b", {}, agent.filters_presets ? "没有可用的模型预设" : "还没有模型预设"),
      agent.model_help || "先在设置中添加提供商并新建预设。", manage), incompatible);
    return;
  }
  if (!m.presets.some((p) => p.name === w.model)) w.model = null;
  if (!w.model && m.presets.length === 1) {
    const only = m.presets[0];
    w.model = only.name;
    if (!w.effortPicked && only.reasoning_effort && S.config.reasoning_efforts.includes(only.reasoning_effort)) {
      w.effort = only.reasoning_effort;
    }
  }
  const label = (name) => m.providers.find((p) => p.name === name)?.label || name;
  const groups = {};
  for (const p of m.presets) (groups[p.resolved_provider || p.provider] ||= []).push(p);
  put(holder,
    h("div", { class: "toolbar" },
      h("span", { class: "section-title", style: "margin:0" }, "模型预设"),
      !w.model ? h("span", { class: "muted small" }, "请点击下方预设以选中") : null,
      h("span", { class: "spacer" }), manage),
    agent.model_help ? h("p", { class: "muted small" }, agent.model_help) : null,
    h("div", { class: "list" }, Object.entries(groups).map(([provider, items]) => [
      h("div", { class: "list-group" }, label(provider)),
      items.map((p) => h("div", { class: `list-item${w.model === p.name ? " selected" : ""}`,
        onclick: () => {
          w.model = p.name;
          if (!w.effortPicked && p.reasoning_effort && S.config.reasoning_efforts.includes(p.reasoning_effort)) w.effort = p.reasoning_effort;
          route();
        } },
        h("div", { class: "t mono" }, p.name),
        h("span", { class: "muted small" }, `${p.model} · 最多输出 ${fmtInt(p.max_tokens)} · ${fmtTokens(p.context_window_tokens)} 上下文${p.reasoning_effort ? ` · 预设推理 ${effortLabel(p.reasoning_effort)}` : ""}`)))])),
    incompatible,
    h("div", { class: "model-effort" },
      h("div", { class: "section-title", id: "effort-label" }, "推理强度"),
      h("div", { class: "segmented", role: "group", "aria-labelledby": "effort-label" },
        S.config.reasoning_efforts.map((e) => h("button", { class: w.effort === e ? "active" : "",
          "aria-pressed": String(w.effort === e), onclick: () => { w.effort = e; w.effortPicked = true; route(); } }, effortLabel(e))))),
    agentOptions(agent));
}

function commandPreview() {
  const w = S.wizard;
  const method = selectedMethod();
  const agent = selectedAgent();
  const {runtime_profile: profile, native_task: task} = w.task;
  const parts = [
    "evoab run", `--integration ${w.bench.integration}`, "--deploy local", ...(profile ? [`--runtime-profile ${profile}`] : []),
    `--task ${task}`, `--tool-profile ${method.tool_profile}`, "--no-sam3", `--agent ${agent.id}`,
    `--model ${w.model}`, `--reasoning-effort ${w.effort}`, `--interaction-mode ${method.interaction}`,
    ...agent.cli_flags,
  ];
  const options = w.agentOptions.get(agent.id) || {};
  for (const option of agent.options) {
    if (option.name in options) parts.push(`${option.flag} ${options[option.name]}`);
  }
  if (method.interaction === "direct_policy") {
    const d = directPayload();
    parts.push(`--direct-representation ${d.representation}`, `--direct-horizon ${d.horizon}`, `--direct-history ${d.history}`,
      `--direct-prompt-blocks ${d.prompt_blocks}`);
    if (d.custom_prompt) parts.push("--direct-custom-prompt='" + d.custom_prompt.replaceAll("'", "'\"'\"'") + "'");
  }
  if (w.bench.container) parts.push("--container");
  return [...parts, "--monitor"].join(" \\\n  ");
}

function stepConfirm(body) {
  const w = S.wizard;
  if (!wizardReady(4)) {
    body.append(h("div", { class: "notice info" }, "请先完成前面的步骤。"));
    return;
  }
  const method = selectedMethod();
  const agent = selectedAgent();
  const options = w.agentOptions.get(agent.id) || {};
  const rows = [
    ["环境", `${benchmarkName(w.bench, w.bench.profile)}（${KINDS[w.kind]}）`], ["套件", `${suiteLabel(w.task.suite)}（${w.task.suite}）`], ["任务", taskName(w.task.task)],
    ["说明", w.task.instruction || "—"], ["本体", w.task.embodiment], ["步数上限", w.task.max_steps || "—"],
    ["方法", methodLabel(method.label)], ["推理强度", effortLabel(w.effort)],
    ["模型预设", w.model],
    ...(method.interaction === "direct_policy" ? [] : [["Agent", agent.label]]),
    ...agent.options.filter((option) => option.name in options).map((option) => [option.label, options[option.name]]),
    ...(method.interaction === "direct_policy" ? [["直接策略设置", Object.entries(directPayload()).map(([k, v]) => `${k}=${v}`).join("  ")]] : []),
  ];
  body.append(
    h("div", { class: "card" }, h("table", { class: "table confirm-table" }, h("tbody", {}, rows.map(([k, v]) =>
      h("tr", {}, h("td", { class: "muted" }, k), h("td", {}, v)))))),
    h("div", { class: "section-title", style: "margin-top:20px" }, "等价命令"),
    h("div", { class: "command" }, commandPreview()),
    h("p", { class: "muted small" }, "API 密钥保存在服务器上，不会出现在页面或命令中。"),
    h("button", { class: "btn primary", style: "margin-top:8px", onclick: launchRun }, "启动运行"));
}

async function launchRun(event) {
  const w = S.wizard;
  const agent = selectedAgent();
  const options = w.agentOptions.get(agent.id);
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/runs", {
      method: "POST",
      json: { integration: w.bench.integration, profile: w.bench.profile, task: w.task.task,
              model: w.model, reasoning_effort: w.effort, method: w.method, agent: agent.id,
              ...(options && Object.keys(options).length ? { agent_options: options } : {}),
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
  const renderTable = () => {
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
      : h("div", { class: "card empty" }, h("b", {}, S.runs.length ? "没有匹配的运行" : "还没有运行记录"), S.runs.length ? "调整筛选条件试试。" : "在“发起任务”里开始第一次运行。"));
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
  libero_pro: { repo: "Zxy-MLlab/LIBERO-PRO", paper: "2510.03827" },
  metaworld: { repo: "Farama-Foundation/Metaworld", paper: "1910.10897" },
  libero_plus: { repo: "sylvestf/LIBERO-plus", paper: "2510.13626" },
  robotwin: { repo: "RoboTwin-Platform/RoboTwin", paper: "2506.18088" },
  calvin: { repo: "mees/calvin", paper: "2112.03227" },
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
  Object.assign(S.wizard, { kind: bench.kind, bench: { integration: bench.integration, profile: p.profile,
    title: bench.title, container: !!p.image }, task: null, suite: "all", query: "" });
  selectMethod(selectedMethod() || availableMethods()[0], S.wizard.agent);
}

function startWith(bench, p, task = null) {
  selectBenchmark(bench, p);
  Object.assign(S.wizard, { task, step: task ? 3 : 2 });
  location.hash = "#new";
}

// One deploy action per benchmark: its Docker recipe when it has one, else its native setup recipe.
async function deploy(bench, p, button) {
  const what = benchmarkName(bench, p.profile);
  const docker = bench.deploy === "container" ? bench.container : null;
  const license = docker?.license ? `\n\n部署需要接受 ${docker.license}（https://docs.omniverse.nvidia.com/eula/）。点“确定”即表示接受。` : "";
  if (!confirm(`部署 ${what}？完成后即可发起任务。${license}`)) return;
  button.disabled = true;
  try {
    const query = docker ? `?container=true${docker.license ? `&accept_license=${encodeURIComponent(docker.license)}` : ""}` : "";
    await api(`/api/benchmarks/${bench.integration}/${p.profile}/setup${query}`, { method: "POST" });
    toast(`已开始部署 ${what}，进度见下方日志`);
  } catch (error) {
    toast(`无法开始：${error.message}`, true);
  }
  await loadBenchmarks();
  route();
}

// Uninstall removes this Docker deployment only; runs, archives and shared images stay.
async function uninstall(bench, p, button) {
  const what = benchmarkName(bench, p.profile);
  const message = `卸载 ${what}？\n\n`
    + "会移除这份 Docker 部署的部署记录和本地运行镜像，之后可以重新部署。\n"
    + "评测运行记录与轨迹、导入的镜像归档、共享的上游镜像与构建缓存、主机上的模型资源都会保留。\n"
    + "正在部署、有运行中的评测或镜像仍被容器使用时不会卸载，也不会停止任何容器。";
  if (!confirm(message)) return;
  button.disabled = true;
  try {
    const result = await api(`/api/benchmarks/${bench.integration}/${p.profile}/setup`, { method: "DELETE" });
    const image = result.image_action === "shared"
      ? "；镜像仍被其他部署使用，已保留"
      : result.image_action === "missing" ? "；镜像此前已不在本机" : "，运行镜像已移除";
    const remaining = result.remaining
      ? `；仍有${result.remaining.kind === "container" ? " Docker " : "本机 "}部署（${result.remaining.profile}）可用`
      : "";
    toast(`已卸载 ${what}${image}${remaining}`);
  } catch (error) {
    toast(`卸载失败：${error.message}`, true);
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
    } }, "发起任务"));
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

function bindSettingsDropdown() {
  const dropdown = $("settingsDropdown");
  const trigger = dropdown.querySelector("button");
  const links = $("settingsLinks");
  let openedByHover = false;
  function show(open, hover = false) {
    links.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
    openedByHover = hover;
  }
  dropdown.addEventListener("pointerenter", event => {
    if (event.pointerType === "mouse" && links.hidden) show(true, true);
  });
  dropdown.addEventListener("pointerleave", event => {
    if (event.pointerType === "mouse" && openedByHover && !links.contains(document.activeElement)) show(false);
  });
  trigger.addEventListener("click", () => show(links.hidden || openedByHover));
  trigger.addEventListener("keydown", event => {
    if (event.key !== "ArrowDown") return;
    event.preventDefault();
    show(true);
    links.firstElementChild.focus();
  });
  dropdown.addEventListener("keydown", event => {
    if (event.key === "Escape" && !links.hidden) {
      show(false);
      trigger.focus();
    }
  });
  dropdown.addEventListener("focusout", event => {
    if (!dropdown.contains(event.relatedTarget)) show(false);
  });
  document.addEventListener("pointerdown", event => {
    if (!dropdown.contains(event.target)) show(false);
  });
  links.addEventListener("click", () => { show(false); trigger.focus(); });
  window.addEventListener("hashchange", () => show(false));
}

function pageSettings(main, arg) {
  const [tab] = (arg || "overview").split("/");
  const body = h("div", { class: "settings-body" });
  put(main, h("div", { class: "settings" }, body));
  ({ overview: settingsOverview, models: settingsModels, benchmarks: settingsBenchmarks }[tab] || settingsOverview)(body);
}

const row = (label, control, hint) => h("label", { class: "form-row" }, h("span", { class: "form-label" }, label, hint ? h("small", {}, hint) : null), control);

// -- overview ----------------------------------------------------------------

function usageTrend(days) {
  const end = new Date();
  const dates = Array.from({ length: 30 }, (_, i) => new Date(end.getTime() - (29 - i) * 86400000).toISOString().slice(0, 10));
  const byDate = new Map(days.map(day => [day.date, day]));
  const peak = Math.max(1, ...dates.map(date => byDate.get(date)?.total_tokens || 0));
  const bars = dates.map(date => {
    const day = byDate.get(date);
    const cached = day?.cache_read_tokens || 0;
    const segments = [[Math.max(0, (day?.input_tokens || 0) - cached), "miss"], [cached, "cached"], [day?.output_tokens || 0, "out"]];
    return h("div", { class: "day-column", title: `${date} · ${fmtTokens(day?.total_tokens || 0)} 词元 · ${day?.requests || 0} 次请求` },
      segments.map(([value, cls]) => value > 0 && h("i", { class: cls, style: `height:${100 * value / peak}%` })));
  });
  return h("figure", { class: "usage-trend", "aria-label": "近 30 天每日词元用量" },
    h("div", { class: "day-columns" }, bars),
    h("div", { class: "chart-dates" }, h("span", {}, dates[0].slice(5)), h("span", {}, dates[29].slice(5))),
    h("figcaption", { class: "usage-legend" }, [["miss", "未缓存输入"], ["cached", "缓存输入"], ["out", "输出"]].map(([cls, label]) =>
      h("span", { class: `k ${cls}` }, label))));
}

function usageTable(title, period, columns, rows, nameColumns, empty) {
  const cell = (tag, value, i) => h(tag, { class: i >= nameColumns ? "num" : "", scope: tag === "th" ? "col" : null }, value);
  return h("section", { class: "usage-section" },
    h("div", { class: "section-head" }, h("h2", {}, title), h("span", {}, period)),
    h("div", { class: "usage-table table-wrap" }, rows.length ? h("table", { class: "table", "aria-label": title },
      h("thead", {}, h("tr", {}, columns.map((x, i) => cell("th", x, i)))),
      h("tbody", {}, rows.map(row => h("tr", {}, row.map((x, i) => cell("td", x, i)))))) : h("p", { class: "empty" }, empty)));
}

async function settingsOverview(body) {
  const holder = h("div", { class: "page overview" }, h("div", { class: "muted" }, "读取用量…"));
  put(body, holder);
  let u;
  try { u = await api("/api/usage"); } catch (error) { put(holder, h("div", { class: "notice error" }, error.message)); return; }
  const metrics = [
    ["词元总量", fmtTokens(u.total_tokens_30d || 0)],
    ["请求次数", fmtInt(u.requests_30d || 0)],
    ["失败次数", fmtInt(u.failed_requests_30d || 0)],
    ["缓存命中率", `${Math.round((u.cache_read_rate_30d || 0) * 100)}%`],
    ["活跃天数", fmtInt(u.active_days_30d || 0)],
  ];
  put(holder,
    h("section", {}, h("div", { class: "section-head" }, h("h1", {}, "模型用量"), h("span", {}, "近 30 天")),
      h("div", { class: "usage-summary" },
        h("dl", { class: "usage-metrics" }, metrics.map(([label, value]) => h("div", {}, h("dt", {}, label), h("dd", {}, value)))),
        usageTrend(u.days || []))),
    usageTable("模型调用", "近 30 天，含重试", ["提供商", "模型", "请求", "输入词元", "缓存词元", "输出词元", "失败"],
      (u.providers_30d || []).map(r => [r.provider, r.model, fmtInt(r.requests), fmtTokens(r.input_tokens),
        fmtTokens(r.cache_read_tokens), fmtTokens(r.output_tokens), fmtInt(r.failed_requests)]), 2, "还没有模型调用记录。"),
    usageTable("评测运行", "全部运行", ["模型", "运行", "成功", "请求", "输入词元", "输出词元"],
      (S.totals?.by_model || []).map(r => [r.key, fmtInt(r.runs), fmtInt(r.success), fmtInt(r.requests),
        fmtTokens(r.input_tokens), fmtTokens(r.output_tokens)]), 1, "还没有运行记录。"));
}

// -- models ------------------------------------------------------------------

async function loadModelSettings(agent) {
  if (!agent) return (S.modelSettings = await api("/api/models"));
  const settings = await api(`/api/models?agent=${encodeURIComponent(agent)}`);
  S.agentModelSettings.set(agent, settings);
  return settings;
}

function settingsModels(body) {
  put(body, h("iframe", { class: "model-settings-frame", title: "模型设置", src: "/model-ui/" }));
}

// -- benchmarks ----------------------------------------------------------------

const CHECK_LABELS = { docker: "Docker", docker_access: "Docker 权限", gpu_containers: "GPU 容器", disk: "磁盘空间",
  agent_runtime: "Agent 运行时", model_presets: "模型预设", network: "下载源" };

function imageImportPanel() {
  const panel = h("div", { class: "card card-pad import-panel" });
  async function refreshFiles() {
    try {
      const imports = await api("/api/benchmarks/image-imports");
      const targets = S.benchmarks.filter((b) => b.deploy === "container").flatMap((b) =>
        b.profiles.map((p) => ({ bench: b, profile: p })));
      const target = h("select", { class: "input", "aria-label": "导入目标仿真", disabled: !targets.length },
        targets.length ? targets.map((t, i) => h("option", { value: i }, benchmarkName(t.bench, t.profile.profile)))
          : h("option", { value: "" }, "没有可导入的 Docker 仿真"));
      const archive = h("select", { class: "input", "aria-label": "本地镜像文件", disabled: !imports.files.length },
        h("option", { value: "" }, imports.files.length ? "选择镜像文件" : "目录中暂无镜像文件"),
        imports.files.map((f) => h("option", { value: f.name }, `${f.name}（${(f.size / 2 ** 30).toFixed(2)} GiB）`)));
      // The directory is a Studio setting on this server; an invalid path is refused by
      // the backend before it is written, so the saved directory stays in effect.
      const directory = h("input", { class: "input", id: "imageImportDirectory", type: "text", spellcheck: "false",
        value: imports.configured || "", placeholder: imports.default_directory,
        "aria-label": "镜像目录（Dashboard 服务器）" });
      const save = h("button", { class: "btn", onclick: () => {
        const value = directory.value.trim();
        if (value && !(value.startsWith("/") || value.startsWith("~/"))) {
          toast("请填写以 / 开头的绝对路径，或以 ~/ 开头的路径", true);
          return;
        }
        saveDirectory(value);
      } }, "保存目录");
      const restore = h("button", { class: "btn", disabled: !imports.configured, onclick: () => saveDirectory("") }, "恢复默认");
      async function saveDirectory(value) {
        save.disabled = restore.disabled = true;
        try {
          const saved = await api("/api/settings", { method: "PUT", json: { image_import_dir: value } });
          S.config.settings = { ...(S.config.settings || {}), ...saved };
          toast(value ? "镜像目录已保存" : "已恢复默认镜像目录");
          await refreshFiles();
        } catch (error) {
          toast(`目录未保存：${error.message}`, true);
          save.disabled = restore.disabled = false;
        }
      }
      const submit = h("button", { class: "btn primary", disabled: true, onclick: async () => {
        const selected = targets[Number(target.value)];
        if (!selected) return;
        const docker = selected.bench.container;
        const license = docker.license ? `
需要接受 ${docker.license}。确定即表示接受。` : "";
        if (!confirm(`导入 ${archive.value} 并验证 ${selected.bench.title}？${license}`)) return;
        submit.disabled = true;
        try {
          const params = new URLSearchParams({ container: "true", image_archive: archive.value });
          if (docker.license) params.set("accept_license", docker.license);
          await api(`/api/benchmarks/${selected.bench.integration}/${selected.profile.profile}/setup?${params}`, { method: "POST" });
          toast("已开始导入和验证，进度见对应仿真的日志");
          await loadBenchmarks();
          route();
        } catch (error) {
          toast(`无法导入：${error.message}`, true);
          submit.disabled = false;
        }
      } }, "导入并验证");
      archive.addEventListener("change", () => { submit.disabled = !archive.value || !targets.length; });
      put(panel,
        h("div", { class: "import-head" },
          h("div", { class: "section-title" }, "本地镜像导入"),
          h("p", { class: "muted small import-intro" },
            "为了避开网络问题导致的镜像下载失败，可以自己下载镜像归档放到 Dashboard 服务器上的目录，再在这里选择归档和目标仿真，由 Studio 通过该服务器上的 Docker 导入并校验；校验通过后环境才会显示为已就绪。")),
        h("div", { class: "import-dir" },
          h("label", { class: "import-label", for: "imageImportDirectory" }, "镜像目录（Dashboard 服务器）"),
          h("div", { class: "import-dir-controls" },
            directory,
            h("div", { class: "import-dir-actions" }, save, restore)),
          h("small", { class: "muted import-hint" },
            "填写服务器上的绝对路径（以 / 或 ~/ 开头），留空则使用默认目录；切换目录不会移动或删除已有文件。")),
        h("div", { class: "import-path" },
          h("div", { class: "import-path-label" }, "当前使用的服务器目录"),
          h("code", {}, imports.directory),
          h("div", { class: "import-path-extra" }, imports.configured
            ? `默认目录：${imports.default_directory}`
            : "当前使用默认目录，可在上方改为其他服务器目录。")),
        h("div", { class: "import-fields" },
          h("label", { class: "import-field" },
            h("span", { class: "import-label" }, "目标仿真"),
            target,
            h("small", { class: "muted" }, "选择用哪套仿真的固定版本和任务要求来校验并安装这个镜像。")),
          h("label", { class: "import-field" },
            h("span", { class: "import-label" }, "镜像文件"),
            archive,
            h("small", { class: "muted" }, "必须是 docker save 导出、且只包含一个镜像的归档；复制完成后先点“刷新文件”。"))),
        imports.files.length ? null
          : h("p", { class: "muted small import-empty" },
            "目录中暂无镜像文件；把归档复制到上面的目录后点“刷新文件”。"),
        h("div", { class: "import-actions" },
          submit,
          h("button", { class: "btn", onclick: refreshFiles }, "刷新文件")),
        h("p", { class: "muted small import-note" },
          "支持 .tar、.tar.gz、.tgz、.tar.bz2、.tar.xz。镜像须与所选仿真匹配（否则校验失败），并包含该仿真的固定版本源码和任务资源；安装运行依赖时仍可能需要联网。"));
    } catch (error) {
      put(panel,
        h("div", { class: "section-title" }, "本地镜像导入"),
        h("div", { class: "notice error" }, error.message),
        h("div", { class: "import-actions" },
          h("button", { class: "btn", onclick: refreshFiles }, "重试"),
          // A saved directory may disappear, lose permissions or become a file; the
          // default is reachable without reading the broken one.
          h("button", { class: "btn", onclick: async () => {
            try {
              const saved = await api("/api/settings", { method: "PUT", json: { image_import_dir: null } });
              S.config.settings = { ...(S.config.settings || {}), ...saved };
              toast("已恢复默认镜像目录");
              await refreshFiles();
            } catch (reset) {
              toast(`无法恢复默认目录：${reset.message}`, true);
            }
          } }, "恢复默认目录")));
    }
  }
  put(panel,
    h("div", { class: "section-title" }, "本地镜像导入"),
    h("p", { class: "muted small" }, "读取导入目录…"));
  refreshFiles();
  return panel;
}

function settingsBenchmarks(body) {
  const checks = h("div", { class: "card card-pad system-check" }, h("div", { class: "muted small" }, "检查部署条件…"));
  api("/api/system/check").then((r) => put(checks,
    h("div", { class: "section-title" }, r.ok ? "部署条件：全部满足" : "部署条件：还有未满足的项"),
    r.checks.map((c) => h("div", { class: `check-row ${c.ok ? "ok" : "bad"}` },
      h("span", { class: "mark" }, c.ok ? "✓" : "✗"), h("b", {}, CHECK_LABELS[c.key] || c.key), h("span", { class: "muted" }, c.detail),
      c.fix ? h("div", { class: "fix mono small" }, c.fix) : null)))).catch((e) => put(checks, h("div", { class: "notice error" }, e.message)));
  put(body, h("div", { class: "page wide" },
    pageHead("评测环境"),
    checks,
    imageImportPanel(),
    h("div", { id: "benchmarkCards" }, benchmarkCards())));
}

function benchmarkCards() {
  // Report an installed runtime's actual kind; before installation, name the method Studio
  // will use, so a native benchmark never implies a Docker deployment.
  const deployKind = (bench, p) => {
    const kind = p.installed ? p.runtime_kind || bench.deploy : bench.deploy;
    return kind === "container" ? "Docker 容器" : kind === "native" ? "本机安装（无 Docker）" : null;
  };
  const profileRow = (bench, p) => {
    // A runtime that registered no tasks is not usable: show it as a failed deployment to redo.
    const status = p.status === "ready" && !p.launchable ? "setup_failed" : p.status;
    const dockerMissing = bench.deploy === "container" && !bench.container?.docker;
    const kind = deployKind(bench, p);
    // 部署 until the runtime is ready, then a disabled 已部署 (runs start from 发起任务).
    const action = status === "ready"
      ? h("button", { class: "btn small", disabled: true },
          p.runtime_kind === "container" ? "已部署（Docker）" : p.runtime_kind === "native" ? "已安装（本机）" : "已部署")
      : h("button", { class: "btn small primary", disabled: dockerMissing || status === "preparing",
          title: dockerMissing ? "需要 Docker，见上方部署条件" : "",
          onclick: (e) => deploy(bench, p, e.currentTarget) },
        bench.deploy === "container" ? "部署（Docker）" : bench.deploy === "native" ? "本机安装" : "部署");
    // Only a Docker deployment can be removed; an adopted native runtime, a running
    // setup or a missing Docker client leaves 卸载 disabled (the backend refuses too).
    const nativeRuntime = p.installed && p.runtime_kind !== "container";
    const removal = bench.deploy === "container"
      ? h("button", { class: "btn small", disabled: !p.installed || nativeRuntime || dockerMissing || status === "preparing",
          title: nativeRuntime ? "当前是本机安装，不是 Docker 部署"
            : dockerMissing ? "需要 Docker，见上方部署条件" : p.installed ? "" : "尚未部署",
          onclick: (e) => uninstall(bench, p, e.currentTarget) }, "卸载")
      : null;
    const detail = status === "ready"
      ? h("a", { class: "small", href: "#", onclick: (e) => { e.preventDefault(); openTasks(bench, p); } }, `${p.task_count} 个任务`)
      : status === "preparing" ? h("span", { class: "muted small" }, "正在部署，日志见下方")
      : bench.setup === "custom" ? h("span", { class: "muted small" }, "检查已注册的自定义环境") : null;
    return [
      h("div", { class: "profile-row" },
        h("strong", {}, p.profile === "default" ? "运行环境" : `运行环境 · ${p.profile}`),
        kind ? h("span", { class: "tag" }, kind) : null,
        pill(status, BENCH_STATUS), detail,
        h("span", { class: "acts" }, action, removal)),
      status === "ready" && p.last_setup_failed ? h("div", { class: "notice error" }, "最近一次部署失败，当前仍可使用此前验证的环境。") : null,
      p.setup_log && (["preparing", "setup_failed"].includes(status) || p.last_setup_failed) ? h("pre", { class: "profile-log log" }, p.setup_log) : null,
    ];
  };
  return S.benchmarks.map((b) => h("article", { class: "card bench" },
    h("div", { class: "bench-head" }, benchmarkIdentity(b, "h2"), b.setup === "custom" ? h("span", { class: "tag" }, "自定义") : projectLinks(b)),
    b.profiles.map((p) => profileRow(b, p))));
}

function applyDefaults(force = false) {
  const s = S.config.settings || {};
  const w = S.wizard;
  if (force || !w.touched) {
    const method = S.config.methods.find((m) => m.id === s.default_method) || S.config.methods[0];
    selectMethod(method, s.default_agent);
  }
  if (s.default_effort && (force || !w.touched)) w.effort = s.default_effort;
  if (s.default_model && (force || !w.model)) w.model = s.default_model;
}

// -- startup -----------------------------------------------------------------

async function refresh() {
  if (current.page === "settings" && current.arg === "benchmarks") {
    try {
      await loadBenchmarks();
      if ($("benchmarkCards")) put($("benchmarkCards"), benchmarkCards());
    } catch { /* Keep the last status until the next refresh. */ }
    return;
  }
  try { await loadRuns(); } catch { return; }
  const page = current.page;
  if (page === "history" && pageHistory.refresh) pageHistory.refresh();
  else if (page === "run") {
    const run = findRun(current.arg);
    if (run) refreshRun(run);
  }
}

async function loadWorkspace() {
  [S.config] = await Promise.all([api("/api/config"), loadBenchmarks(), loadRuns()]);
  applyDefaults();
}

function init() {
  bindSettingsDropdown();
  let workspace;
  async function navigate() {
    const hash = location.hash;
    if (hash !== "#settings/models" && !S.config) {
      workspace ??= loadWorkspace().catch(error => { workspace = null; throw error; });
      try {
        await workspace;
      } catch (error) {
        if (location.hash === hash) put($("main"), h("div", { class: "page" },
          h("div", { class: "notice error" }, `无法连接 Studio 服务：${error.message}`)));
        return;
      }
    }
    if (location.hash === hash) route();
  }
  window.addEventListener("hashchange", navigate);
  navigate();
}
window.addEventListener("DOMContentLoaded", init, { once: true });
