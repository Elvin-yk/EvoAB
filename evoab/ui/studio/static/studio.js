// EvoAB Studio page: launch runs, list them, show outcome, usage and replay.
const $ = (id) => document.getElementById(id);
const state = { config: {}, catalog: [], providers: [], runs: [], totals: null, selected: null };

const STATUS = {
  success: "成功", failure: "失败", error: "出错", running: "运行中",
  starting: "启动中", failed: "启动失败", interrupted: "已中断",
};
const fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString("zh-CN"));
function fmtDuration(s) {
  if (s == null) return "—";
  s = Math.round(s);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} 分 ${s % 60} 秒` : `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}
function fmtTime(epoch) {
  if (!epoch) return "";
  const d = new Date(epoch * 1000), p = (v) => String(v).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) n.append(k instanceof Node ? k : String(k));
  return n;
}
async function api(path, options) {
  const r = await fetch(path, { cache: "no-store", ...options });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status}`);
  return body;
}
function fill(select, items, keep) {
  const previous = keep ? select.value : null;
  select.replaceChildren(...items);
  if (previous && [...select.options].some((o) => o.value === previous)) select.value = previous;
}
const option = (value, label = value) => el("option", { value }, label);

// -- launch form ------------------------------------------------------------

function currentRuntime() {
  const [integration, profile] = $("runtime").value.split("/");
  return state.catalog.find((r) => r.integration === integration && r.profile === profile);
}
function onRuntime() {
  const rt = currentRuntime();
  fill($("benchmark"), (rt?.benchmarks || []).map((b) => option(b.name, `${b.name}（${b.tasks.length}）`)));
  onBenchmark();
}
function onBenchmark() {
  const bench = currentRuntime()?.benchmarks.find((b) => b.name === $("benchmark").value);
  fill($("task"), (bench?.tasks || []).map((t) => option(t.task)));
  onTask();
}
function currentTask() {
  const bench = currentRuntime()?.benchmarks.find((b) => b.name === $("benchmark").value);
  return bench?.tasks.find((t) => t.task === $("task").value);
}
function onTask() {
  const t = currentTask();
  $("taskHint").textContent = t ? `${t.instruction || ""}${t.embodiment ? ` · 本体：${t.embodiment}` : ""}` : "";
}
async function onProvider() {
  const provider = state.providers.find((p) => p.id === $("provider").value);
  $("model").replaceChildren(option("", "加载中…"));
  if (!provider) return;
  try {
    const { models } = await api(`/api/providers/${encodeURIComponent(provider.id)}/models`);
    const recommended = provider.recommended.filter((m) => models.includes(m));
    const others = models.filter((m) => !recommended.includes(m));
    const groups = [];
    if (recommended.length) groups.push(el("optgroup", { label: "推荐（已验证 图像 + 工具调用）" }, ...recommended.map((m) => option(m))));
    if (others.length) groups.push(el("optgroup", { label: "其他模型（未验证）" }, ...others.map((m) => option(m))));
    $("model").replaceChildren(...groups);
  } catch (error) {
    $("model").replaceChildren(option("", `模型列表获取失败：${error.message}`));
  }
}
async function onLaunch(event) {
  event.preventDefault();
  const [integration, profile] = $("runtime").value.split("/");
  const payload = {
    integration, profile, task: $("task").value, provider_id: $("provider").value,
    model: $("model").value, reasoning_effort: $("effort").value, action_profile: $("actionProfile").value,
  };
  $("launchBtn").disabled = true;
  $("formStatus").className = "form-status";
  $("formStatus").textContent = "正在启动…";
  try {
    const { launch_id } = await api("/api/runs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    $("formStatus").textContent = "已启动，环境准备约需 30 秒";
    location.hash = `#launch/${launch_id}`;
    await refreshRuns();
  } catch (error) {
    $("formStatus").className = "form-status error";
    $("formStatus").textContent = `启动失败：${error.message}`;
  } finally {
    $("launchBtn").disabled = false;
  }
}

// -- runs -------------------------------------------------------------------

const runKey = (r) => (r.id ? `run/${r.id}` : `launch/${r.launch_id}`);
function attemptOf(run) {
  // "第几次": order of this run among runs of the same task, oldest first.
  const same = state.runs.filter((r) => r.task === run.task).sort((a, b) => (a.started_at || 0) - (b.started_at || 0));
  return same.indexOf(run) + 1;
}
async function refreshRuns() {
  try {
    const data = await api("/api/runs");
    state.runs = data.runs;
    state.totals = data.totals;
  } catch { return; }
  renderRunList();
  route();
}
function renderRunList() {
  $("runCount").textContent = state.runs.length ? `（${state.runs.length}）` : "";
  const current = location.hash.slice(1);
  $("runList").replaceChildren(...state.runs.map((r) =>
    el("a", { class: `run-item${runKey(r) === current ? " active" : ""}`, href: `#${runKey(r)}` },
      el("span", { class: `dot ${r.status}` }),
      el("div", {},
        el("div", { class: "t" }, r.task || "—"),
        el("div", { class: "s" }, `${STATUS[r.status] || r.status} · ${r.model || "?"} · ${fmtTime(r.started_at)}`)))));
}
function card(label, value, cls = "") {
  return el("div", { class: "card" }, el("div", { class: "k" }, label), el("div", { class: `v ${cls}`, title: String(value) }, value));
}
function chip(label, value) {
  return value == null || value === "" ? null : el("span", { class: "chip" }, el("b", {}, label), value);
}
function showRun(run) {
  $("usageView").hidden = true;
  $("runView").hidden = false;
  const task = state.catalog.flatMap((rt) => rt.benchmarks.flatMap((b) => b.tasks)).find((t) => t.task === run.task);
  $("chips").replaceChildren(...[
    chip("", run.simulated === false ? "真机" : "仿真"),
    chip("环境", run.integration && `${run.integration}${run.profile ? ` / ${run.profile}` : ""}`),
    chip("Benchmark", run.benchmark),
    chip("本体", run.embodiment || task?.embodiment),
    chip("任务", run.task),
    chip("第", `${attemptOf(run)} 次`),
    chip("方法", run.agent && `${run.agent} · ${run.action_profile || "direct"}`),
    chip("API", run.provider),
    chip("模型", run.model),
    chip("推理", run.reasoning_effort),
  ].filter(Boolean));
  $("runTitle").textContent = run.task || "—";
  $("runInstruction").textContent = task?.instruction || "";
  const u = run.usage || {};
  $("runCards").replaceChildren(
    card("结果", STATUS[run.status] || run.status, run.status),
    card("用时", fmtDuration(run.elapsed_s)),
    card("环境步数", fmtInt(run.environment_steps)),
    card("模型请求", fmtInt(u.requests)),
    card("输入 tokens", fmtInt(u.input_tokens)),
    card("缓存命中", fmtInt(u.cached_input_tokens)),
    card("输出 tokens", fmtInt(u.output_tokens)),
  );
  const message = run.error || run.agent_message;
  $("runMessage").hidden = !message;
  $("runMessage").textContent = message ? `${run.error ? "错误：" : "Agent 总结："}${message}` : "";
  const stoppable = run.launch_id && ["running", "starting"].includes(run.status);
  $("stopBtn").hidden = !stoppable;
  $("stopBtn").onclick = async () => {
    $("stopBtn").disabled = true;
    try { await api(`/api/runs/${run.launch_id}/stop`, { method: "POST" }); } catch (error) { alert(error.message); }
    $("stopBtn").disabled = false;
    refreshRuns();
  };
  const monitor = state.config.monitor_url;
  const src = run.monitor_id && monitor ? `${monitor}/#run/${run.monitor_id}` : "";
  $("openMonitor").hidden = !src;
  $("openMonitor").href = src || "#";
  if ($("monitor").dataset.src !== src) {
    $("monitor").dataset.src = src;
    $("monitor").src = src || "about:blank";
  }
}
function showUsage() {
  $("runView").hidden = true;
  $("usageView").hidden = false;
  $("rootInfo").textContent = `运行目录：${state.config.runs_root || ""}`;
  const o = state.totals?.overall || {};
  $("usageCards").replaceChildren(
    card("运行次数", fmtInt(o.runs)), card("成功", fmtInt(o.success), "success"),
    card("模型请求", fmtInt(o.requests)), card("输入 tokens", fmtInt(o.input_tokens)),
    card("缓存命中", fmtInt(o.cached_input_tokens)), card("输出 tokens", fmtInt(o.output_tokens)),
  );
  $("usageTable").tBodies[0].replaceChildren(...(state.totals?.by_model || []).map((m) => el("tr", {},
    el("td", {}, m.key), el("td", {}, fmtInt(m.runs)), el("td", {}, fmtInt(m.success)),
    el("td", {}, fmtInt(m.requests)), el("td", {}, fmtInt(m.input_tokens)),
    el("td", {}, fmtInt(m.cached_input_tokens)), el("td", {}, fmtInt(m.output_tokens)))));
}
function route() {
  const key = location.hash.slice(1);
  const run = state.runs.find((r) => runKey(r) === key) ||
    (key.startsWith("launch/") && state.runs.find((r) => r.launch_id === key.slice(7)));
  if (run) {
    if (runKey(run) !== key) history.replaceState(null, "", `#${runKey(run)}`);
    showRun(run);
  } else {
    showUsage();
  }
  renderRunList();
}

// -- startup ----------------------------------------------------------------

async function init() {
  [state.config, state.catalog, state.providers] = await Promise.all([
    api("/api/config"), api("/api/catalog"), api("/api/providers"),
  ]);
  fill($("runtime"), state.catalog.map((r) => option(`${r.integration}/${r.profile}`, `${r.integration} / ${r.profile}（仿真）`)));
  fill($("provider"), state.providers.map((p) => option(p.id, p.label)));
  fill($("effort"), state.config.reasoning_efforts.map((e) => option(e)));
  $("effort").value = "low";
  fill($("actionProfile"), state.config.action_profiles.map((e) => option(e)));
  $("runtime").onchange = onRuntime;
  $("benchmark").onchange = onBenchmark;
  $("task").onchange = onTask;
  $("provider").onchange = onProvider;
  $("launch").onsubmit = onLaunch;
  onRuntime();
  onProvider();
  window.addEventListener("hashchange", route);
  await refreshRuns();
  setInterval(() => { if (!document.hidden) refreshRuns(); }, 5000);
}
init();
