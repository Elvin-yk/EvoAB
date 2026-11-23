// Run review: a step-by-step, replayable account of one run.
//
// Data comes from /api/trace/<run>: fixed harness stages, one round per model
// request (trigger, new input, cost, output), every tool execution with its
// arguments, timing, environment effect and result, and the round that used
// each result. Left: synchronized cameras over one shared time axis. Right: the
// flow of steps. Selecting either moves the other.
"use strict";

const RV = {
  run: null, trace: null, timer: null, playTimer: null,
  t: null, follow: true, playing: false, speed: 1, selected: null, open: new Set(),
};

const replayAgent = () => RV.trace?.agent || RV.run?.agent;
const agentName = () => ({ native: "原生 Agent", codex: "Codex" })[replayAgent()] || replayAgent() || "Agent";
const nativeReplay = () => replayAgent() === "native";

const ORIGIN = {
  code: ["固定代码", "harness"],
  get runtime() { return [agentName() + " 运行时", "runtime"]; },
  model: ["模型决定", "model"],
  robot: ["EvoAB 机器人工具", "robot"],
  get agent() { return [agentName() + " 内置工具", "runtime"]; },
  script: ["Codex 脚本 → 机器人工具", "robot"],
};
const originTag = (kind) => h("span", { class: `origin ${ORIGIN[kind][1]}` }, ORIGIN[kind][0]);
const fileUrl = (path, width) => `/api/trace/${encodeURIComponent(RV.run.id)}/files/${path}${width ? `?width=${width}` : ""}`;
const rel = (t) => (t == null || RV.trace?.t0 == null ? null : t - RV.trace.t0);
const clock = (t) => (t == null ? "—" : `+${fmtClock(rel(t))}`);
const secs = (s) => (s == null ? "—" : s < 1 ? `${Math.round(s * 1000)} 毫秒` : s < 60 ? `${s.toFixed(1)} 秒` : fmtDuration(s));
const pretty = (value) => (typeof value === "string" ? value : JSON.stringify(value, null, 2));

function cameraLabel(name, index) {
  return ({ agentview: "全景相机", wrist: "腕部相机", robot0_eye_in_hand: "腕部相机", frontview: "正面相机",
    sideview: "侧面相机" })[name] || `相机 ${index + 1}（${name}）`;
}

function pageRun(main, key) {
  const run = findRun(key);
  if (!run) {
    put(main, h("div", { class: "page" }, h("div", { class: "card empty" }, h("b", {}, "找不到这次运行"),
      h("a", { class: "btn small", href: "#history" }, "返回运行记录"))));
    return;
  }
  if (run.id && location.hash !== `#${run.key}`) history.replaceState(null, "", `#${run.key}`);
  if (RV.run?.key !== run.key) {
    closeReplay();
    Object.assign(RV, { run, trace: null, t: null, follow: true, selected: null, open: new Set() });
    put(main, h("div", { class: "page review", "data-run": run.key },
      h("div", { id: "rvHead" }), h("div", { id: "rvBody" }), h("div", { id: "rvLog" })));
    current.arg = run.key;
  }
  RV.run = run;
  renderHead();
  if (!run.id) {
    put($("rvBody"), h("div", { class: "card empty" }, h("b", {}, run.status === "starting" ? "环境准备中" : "没有运行记录"),
      run.status === "starting" ? "运行目录创建后，这里会逐步显示每一轮模型调用与工具执行。" : "这次运行没有生成运行目录，见下方启动日志。"));
    renderLaunchLog(run);
    return;
  }
  if (!RV.trace) put($("rvBody"), h("div", { class: "card empty" }, "读取运行记录…"));
  loadTrace();
  renderLaunchLog(run);
}

function closeReplay() {
  if (typeof pause === "function" && DP.tr) { pause(); DP.tr = null; DP.cache.clear(); }
  document.removeEventListener("keydown", directKeys);
  clearInterval(RV.timer);
  clearInterval(RV.playTimer);
  RV.timer = RV.playTimer = null;
  RV.playing = false;
  RV.run = null;
}

async function loadTrace() {
  const run = RV.run;
  let trace;
  try {
    trace = await api(`/api/trace/${encodeURIComponent(run.id)}`);
  } catch (error) {
    if (RV.run === run) put($("rvBody"), h("div", { class: "notice error" }, `运行记录读取失败：${error.message}`));
    return;
  }
  if (RV.run !== run) return;
  const first = !RV.trace;
  RV.trace = trace;
  if (trace.kind === "direct") {
    renderHead();
    renderDirect(first);
    clearInterval(RV.timer);
    RV.timer = trace.live || isLive(run) ? setInterval(() => !document.hidden && loadTrace(), 3000) : null;
    return;
  }
  RV.moments = buildMoments(trace);
  if (first || RV.follow) RV.t = endTime();
  renderHead();
  renderBody(first);
  clearInterval(RV.timer);
  RV.timer = trace.live || isLive(run) ? setInterval(() => !document.hidden && loadTrace(), 3000) : null;
}

const endTime = () => {
  const tr = RV.trace;
  const last = Math.max(tr.t_end || 0, ...tr.rounds.map((r) => r.finished_at || r.started_at || 0),
    ...tr.calls.map((c) => c.finished_at || 0), ...tr.frames.map((f) => f.t));
  return last || tr.t0;
};

// Every selectable step, in time order.
function buildMoments(tr) {
  const list = [];
  for (const s of tr.stages) if (s.at) list.push({ id: `stage:${s.id}`, t: s.at });
  for (const r of tr.rounds) {
    list.push({ id: `round:${r.index}`, t: r.started_at });
    for (const id of r.calls) {
      const c = tr.calls.find((x) => x.call_id === id);
      if (!c) continue;
      list.push({ id: `call:${c.call_id}`, t: c.started_at || c.issued_at || r.finished_at, round: r.index });
      for (const n of c.nested) list.push({ id: `nested:${n.transport_id}`, t: n.started_at || n.queued_at, round: r.index });
    }
  }
  if (tr.ending) list.push({ id: "ending", t: endTime() });
  return list.filter((m) => m.t != null).sort((a, b) => a.t - b.t);
}

// -- header and ending -------------------------------------------------------

function renderHead() {
  const run = RV.run;
  const tr = RV.trace;
  const u = tr?.agent === "native" ? {
    input_tokens: tr.rounds.reduce((n, r) => n + r.usage.input, 0),
    output_tokens: tr.rounds.reduce((n, r) => n + r.usage.output, 0),
    cached_input_tokens: tr.rounds.reduce((n, r) => n + r.usage.cached, 0),
  } : run.usage || {};
  const direct = tr?.kind === "direct";
  const robotCalls = !tr ? null : direct ? tr.totals.steps
    : tr.calls.filter((c) => c.executor === "robot").length + tr.calls.reduce((n, c) => n + c.nested.length, 0);
  const lastSteps = !tr ? null : direct ? tr.totals.steps : [...tr.frames].reverse().find((f) => f.steps != null)?.steps;
  const steps = run.environment_steps ?? lastSteps;
  const policy = direct ? tr.policy : {};
  const model = policy.model || {};
  const metadata = [
    ["类型", run.simulated === false ? "真机" : "仿真"], ["评测集", runBenchmark(run)],
    ["套件", run.benchmark && suiteLabel(run.benchmark)], ["本体", run.embodiment],
    ["API 提供商", model.provider || run.provider], ["推理强度", effortLabel(model.reasoning_effort || run.reasoning_effort)],
    ["动作表示", ({ abs_eef: "末端绝对位姿", delta_eef: "末端位姿增量" })[policy.representation] || policy.representation_label], ["动作块长度", policy.horizon?.join("–")],
    ["历史窗口", policy.history_length && `${policy.history_length} 步 · 间隔 ${policy.history_stride} 步`],
    ["控制频率", policy.control_hz && `${policy.control_hz} Hz`], ["开始", fmtTime(run.started_at)],
    ["同任务同模型", run.attempt && `第 ${run.attempt} 次`],
    ["输入词元", fmtTokens(u.input_tokens)], ["输出词元", fmtTokens(u.output_tokens)],
    ["缓存词元", u.cached_input_tokens && fmtInt(u.cached_input_tokens)],
    ["推理词元", u.reasoning_tokens && fmtInt(u.reasoning_tokens)],
    ["机器人动作", !direct && robotCalls != null ? String(robotCalls) : null],
  ];
  const method = methodLabel(run.method);
  const context = [direct || !replayAgent() ? method : [method.split(" · ")[0], agentName()].filter(Boolean).join(" · "), model.model || tr?.model || run.model].filter(Boolean).join(" · ");
  const facts = [
    ["用时", fmtDuration(run.elapsed_s)], ["环境步数", `${steps ?? "—"}${tr?.max_steps ? ` / ${tr.max_steps}` : ""}`],
    [direct ? "推理次数" : "模型轮次", fmtInt(tr ? (direct ? tr.totals.calls : tr.rounds.length) : u.requests)],
  ];
  put($("rvHead"), h("section", { class: "run-summary" },
    h("div", { class: "run-top" },
      h("button", { class: "back", title: "返回", onclick: () => (history.length > 1 ? history.back() : (location.hash = "#history")) }, "←"),
      h("span", { class: "mono muted" }, runTitle(run)), pill(run.status),
      isLive(run) && run.launch_id ? h("button", { class: "btn small danger", onclick: (e) => stopRun(run, e.currentTarget) }, "停止") : null),
    h("h1", {}, tr?.instruction || runTask(run)),
    h("div", { class: "run-facts" }, h("b", {}, context), facts.map(([k, v]) => h("span", {}, k, " ", h("strong", {}, v)))),
    h("div", { class: "run-disclosures" }, h("details", { id: "rvConfig", open: $("rvConfig")?.open }, h("summary", {}, "运行配置与用量"),
      h("dl", { class: "run-meta" }, metadata.filter(([, v]) => v != null && v !== false && v !== "").map(([k, v]) =>
        h("div", {}, h("dt", {}, k), h("dd", {}, v))))),
    endingDetails(run, tr))));
}

function endingDetails(run, tr) {
  const e = tr?.ending;
  if (!e) return null;
  const failed = run.status === "failure" || run.status === "error";
  let reasons = e.chain;
  if (tr.kind === "direct") {
    const atLimit = e.step_limit && e.steps >= e.step_limit;
    const stop = atLimit ? `仿真已用完 ${e.steps} / ${e.step_limit} 步。`
      : ({ call_limit: "达到推理次数上限。", episode_ended: "环境结束回合。", cancelled: "运行已取消。" })[e.stop_reason];
    const verdict = e.task_success == null ? null : `环境验证结果：${e.task_success ? "任务已完成" : "任务未完成"}。`;
    reasons = [run.error, stop || (!run.error && e.stop_reason), verdict].filter(Boolean);

  }
  return h("details", { id: "rvReason", open: $("rvReason")?.open },
    h("summary", {}, failed ? "失败原因" : "结束详情"),
    h("ul", {}, reasons.map((line) => h("li", {}, line))));
}

// -- body: player + flow -----------------------------------------------------

function renderBody(first) {
  let body = $("rvBody").querySelector(".rv-grid");
  if (!body) {
    body = h("div", { class: "rv-grid" },
      h("div", { class: "rv-left" },
        h("div", { class: "card player" },
          h("div", { class: "cams", id: "rvCams" }),
          h("div", { class: "transport" },
            h("button", { class: "btn small", id: "rvPlay", onclick: togglePlay }, "▶ 播放"),
            h("div", { class: "segmented small" }, [1, 4, 16].map((s) => h("button", { "data-speed": s, class: s === RV.speed ? "active" : "",
              onclick: () => { RV.speed = s; document.querySelectorAll("[data-speed]").forEach((b) => b.classList.toggle("active", Number(b.dataset.speed) === s)); } }, `${s}×`))),
            h("span", { class: "mono", id: "rvClock" }),
            h("span", { class: "spacer" }),
            h("button", { class: "btn small", id: "rvFollow", onclick: () => { RV.follow = !RV.follow; if (RV.follow) setTime(endTime()); updateFollow(); } }, "跟随最新")),
          h("div", { class: "lanes", id: "rvLanes" })),
        h("div", { class: "card inspector", id: "rvNow" })),
      h("div", { class: "rv-right card" },
        h("div", { class: "flow-head" }, h("b", {}, "运行流水"), h("span", { class: "muted small" }, "按时间顺序：每一步的触发、输入、耗时、消耗、输出与去向"),
          h("span", { class: "spacer" }),
          h("button", { class: "btn small ghost", onclick: () => toggleAll(true) }, "全部展开"),
          h("button", { class: "btn small ghost", onclick: () => toggleAll(false) }, "全部收起")),
        h("div", { class: "flow", id: "rvFlow" })));
    put($("rvBody"), body);
  }
  drawLanes();
  drawFlow();
  syncTime(first);
}

// -- shared time axis --------------------------------------------------------

function span() {
  const tr = RV.trace;
  const t0 = tr.t0;
  return { t0, t1: Math.max(endTime(), t0 + 1) };
}
const pct = (t) => { const { t0, t1 } = span(); return `${Math.max(0, Math.min(100, (100 * (t - t0)) / (t1 - t0)))}%`; };
const width = (a, b) => { const { t0, t1 } = span(); return `${Math.max(0.15, (100 * ((b ?? a) - a)) / (t1 - t0))}%`; };

function drawLanes() {
  const tr = RV.trace;
  const bar = (start, end, cls, title, id) => h("i", { class: cls, title, style: `left:${pct(start)};width:${width(start, end)}`,
    onclick: (e) => { e.stopPropagation(); select(id, true); } });
  const robot = [];
  for (const c of tr.calls) {
    if (c.executor === "robot" && c.started_at) robot.push(bar(c.started_at, c.finished_at, `tool ${c.success === false ? "fail" : ""}`, `${c.name} · ${secs(c.duration_s)}`, `call:${c.call_id}`));
    for (const n of c.nested) if (n.started_at) robot.push(bar(n.started_at, n.finished_at, `tool ${n.success === false ? "fail" : ""}`, `${n.name} · ${secs(n.duration_s)}`, `nested:${n.transport_id}`));
  }
  const agentTools = tr.calls.filter((c) => c.executor !== "robot").map((c) => {
    const t = c.started_at || c.issued_at;
    return t ? h("i", { class: "tick", title: c.name, style: `left:${pct(t)}`, onclick: (e) => { e.stopPropagation(); select(`call:${c.call_id}`, true); } }) : null;
  });
  // Environment steps as a filled step chart from frames and robot results.
  const points = [];
  for (const f of tr.frames) if (f.steps != null) points.push([f.t, f.steps]);
  for (const c of tr.calls) {
    for (const x of [c, ...c.nested]) if (x.robot?.native_steps != null && x.finished_at) points.push([x.finished_at, x.robot.native_steps]);
  }
  points.sort((a, b) => a[0] - b[0]);
  const max = tr.max_steps || Math.max(1, ...points.map((p) => p[1]));
  const { t0, t1 } = span();
  let d = "M0,30";
  let prev = 0;
  for (const [t, s] of points) {
    const x = (1000 * (t - t0)) / (t1 - t0);
    d += ` L${x.toFixed(1)},${(30 - (30 * prev) / max).toFixed(1)} L${x.toFixed(1)},${(30 - (30 * s) / max).toFixed(1)}`;
    prev = s;
  }
  d += ` L1000,${(30 - (30 * prev) / max).toFixed(1)} L1000,30 Z`;
  const stepsSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  stepsSvg.setAttribute("viewBox", "0 0 1000 30");
  stepsSvg.setAttribute("preserveAspectRatio", "none");
  stepsSvg.innerHTML = `<path d="${d}" class="steps-area"/>`;
  const lane = (label, sub, ...content) => h("div", { class: "lane" }, h("div", { class: "lane-label" }, label, sub ? h("small", {}, sub) : null),
    h("div", { class: "lane-track" }, content));
  const ticks = [];
  const { t0: a, t1: b } = span();
  const stepS = [10, 30, 60, 120, 300, 600, 1200].find((s) => (b - a) / s <= 8) || 1800;
  for (let s = 0; s <= b - a; s += stepS) ticks.push(h("span", { style: `left:${pct(a + s)}` }, fmtClock(s)));
  const lanes = $("rvLanes");
  put(lanes,
    h("div", { class: "lane axis" }, h("div", { class: "lane-label" }), h("div", { class: "lane-track" }, ticks)),
    lane("模型", `${tr.rounds.length} 轮`, tr.rounds.map((r) => bar(r.started_at, r.finished_at,
      `round ${r.trigger.kind.startsWith("compact") ? "compact" : ""} ${r.error ? "fail" : ""}`, `第 ${r.index + 1} 轮 · ${secs(r.duration_s)} · ${fmtTokens(r.usage.input)} 入 / ${fmtTokens(r.usage.output)} 出`, `round:${r.index}`))),
    lane("机器人", `${tr.calls.filter((c) => c.executor === "robot").length + tr.calls.reduce((n, c) => n + c.nested.length, 0)} 次`, robot),
    lane("内置工具", `${tr.calls.filter((c) => c.executor !== "robot").length} 次`, agentTools),
    lane("环境步数", tr.max_steps ? `上限 ${tr.max_steps}` : "", stepsSvg),
    lane("画面", `${tr.frames.length} 帧`, tr.frames.map((f) => h("i", { class: "frame", style: `left:${pct(f.t)}` }))),
    h("div", { class: "cursor", id: "rvCursor" }));
  lanes.onpointerdown = (event) => {
    const track = lanes.querySelector(".lane-track");
    const rect = track.getBoundingClientRect();
    const move = (e) => {
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      RV.follow = false; stopPlay(); updateFollow();
      setTime(a + ratio * (b - a));
    };
    move(event);
    lanes.setPointerCapture(event.pointerId);
    lanes.onpointermove = move;
    lanes.onpointerup = () => { lanes.onpointermove = null; };
  };
}

function setTime(t) {
  RV.t = t;
  syncTime(false);
}

function frameAt(t) {
  const frames = RV.trace.frames;
  let found = frames[0] || null;
  for (const f of frames) { if (f.t <= t + 1e-6) found = f; else break; }
  return found;
}

function momentAt(t) {
  let found = null;
  for (const m of RV.moments) { if (m.t <= t + 1e-6) found = m; else break; }
  return found;
}

function syncTime(scroll) {
  const tr = RV.trace;
  if (!tr) return;
  const t = RV.t ?? endTime();
  const cursor = $("rvCursor");
  // The cursor spans all lanes; it shares the tracks' origin and width (lanes have 12px side padding).
  if (cursor) cursor.style.left = `calc(var(--lane-label) + (100% - 24px - var(--lane-label)) * ${parseFloat(pct(t)) / 100})`;
  $("rvClock").textContent = `${fmtClock(rel(t))} / ${fmtClock(rel(span().t1))}`;
  // Cameras: every camera of the latest snapshot at or before t, side by side.
  const frame = frameAt(t);
  const cams = $("rvCams");
  if (!frame) put(cams, h("div", { class: "empty" }, "还没有画面"));
  else {
    const names = Object.keys(frame.cameras);
    if (cams.dataset.layout !== names.join("|")) {
      cams.dataset.layout = names.join("|");
      cams.style.setProperty("--cams", Math.min(names.length, 3));
      put(cams, names.map((name, i) => h("figure", { "data-cam": name }, h("img", { alt: cameraLabel(name, i) }), h("figcaption", {}, cameraLabel(name, i)))));
    }
    for (const name of names) {
      const img = cams.querySelector(`[data-cam="${CSS.escape(name)}"] img`);
      const src = fileUrl(frame.cameras[name], 960);
      if (img && img.getAttribute("src") !== src) img.src = src;
    }
    cams.dataset.stamp = `${clock(frame.t)} · 第 ${frame.steps ?? "—"} 步`;
  }
  const now = momentAt(t);
  if (now && now.id !== RV.selected) {
    RV.selected = now.id;
    highlight(scroll || RV.follow || RV.playing);
  }
  drawInspector(t, frame, now);
}

function drawInspector(t, frame, now) {
  const tr = RV.trace;
  const round = [...tr.rounds].reverse().find((r) => r.started_at <= t);
  const running = round && (!round.finished_at || round.finished_at > t);
  const robotNow = tr.calls.flatMap((c) => [c, ...c.nested]).find((x) => x.started_at && x.started_at <= t && (!x.finished_at || x.finished_at >= t) && (x.robot || x.transport_id));
  let what;
  if (tr.ending && t >= endTime()) what = tr.ending.task_success ? "运行结束 · 任务成功" : "运行结束 · " + (tr.ending.task_success === false ? "任务未完成" : "未记录任务判定");
  else if (robotNow) what = `机器人正在执行 ${robotNow.name}`;
  else if (running && nativeReplay()) what = `原生 Agent 第 ${round.index + 1} 轮进行中`;
  else if (running) what = `等待模型输出（第 ${round.index + 1} 轮，已用 ${secs(t - round.started_at)}）`;
  else if (round) what = `第 ${round.index + 1} 轮已返回，${agentName()} 正在执行工具或准备下一轮`;
  else what = "Agent 尚未开始：固定代码正在准备环境与提示词";
  put($("rvNow"),
    h("div", { class: "now-row" }, h("span", { class: "muted small" }, "此刻"), h("b", {}, what)),
    h("div", { class: "now-row small muted" }, `画面：${frame ? `${clock(frame.t)}，第 ${frame.steps ?? "—"} 步` : "—"}`,
      round ? ` · 上下文：第 ${round.index + 1} 轮输入 ${fmtTokens(round.usage.input)} 词元` : ""));
}

function highlight(scroll) {
  const flow = $("rvFlow");
  if (!flow) return;
  flow.querySelectorAll(".step.current").forEach((n) => n.classList.remove("current"));
  const node = flow.querySelector(`[data-step="${CSS.escape(RV.selected || "")}"]`);
  if (node) {
    node.classList.add("current");
    if (scroll) {
      // Scroll only the flow panel, never the page, so the header stays in view.
      const top = node.offsetTop;
      if (top < flow.scrollTop + 20 || top > flow.scrollTop + flow.clientHeight - 60) {
        flow.scrollTo({ top: Math.max(0, top - flow.clientHeight / 3), behavior: RV.playing ? "auto" : "smooth" });
      }
    }
  }
}

function select(id, pin) {
  const moment = RV.moments.find((m) => m.id === id);
  if (!moment) return;
  if (pin) { RV.follow = false; stopPlay(); updateFollow(); }
  RV.selected = null;
  RV.open.add(id);
  const node = $("rvFlow")?.querySelector(`[data-step="${CSS.escape(id)}"]`);
  if (node && !node.classList.contains("open")) node.classList.add("open");
  setTime(moment.t);
  highlight(true);
}

function updateFollow() {
  const b = $("rvFollow");
  if (b) b.classList.toggle("active", RV.follow);
}

function togglePlay() {
  if (RV.playing) { stopPlay(); return; }
  const tr = RV.trace;
  if (!tr) return;
  RV.follow = false; updateFollow();
  const frames = tr.frames;
  if (RV.t >= endTime() - 0.5) RV.t = tr.t0;
  RV.playing = true;
  $("rvPlay").textContent = "❚❚ 暂停";
  const tick = 100;
  RV.playTimer = setInterval(() => {
    // Real time × speed, but jump over long stretches without new frames or steps.
    let next = RV.t + (tick / 1000) * RV.speed;
    const upcoming = RV.moments.find((m) => m.t > RV.t) ;
    const nextFrame = frames.find((f) => f.t > RV.t);
    const target = Math.min(upcoming?.t ?? Infinity, nextFrame?.t ?? Infinity);
    if (target !== Infinity && target - RV.t > 2 * RV.speed) next = Math.max(next, target - 0.5);
    if (next >= endTime()) { setTime(endTime()); stopPlay(); return; }
    setTime(next);
  }, tick);
}

function stopPlay() {
  if (!RV.playing) return;
  RV.playing = false;
  clearInterval(RV.playTimer);
  if ($("rvPlay")) $("rvPlay").textContent = "▶ 播放";
}

// -- flow --------------------------------------------------------------------

function toggleAll(open) {
  const flow = $("rvFlow");
  flow.querySelectorAll(".step").forEach((n) => {
    n.classList.toggle("open", open);
    if (open) RV.open.add(n.dataset.step); else RV.open.delete(n.dataset.step);
  });
}

function step(id, cls, head, ...body) {
  const node = h("div", { class: `step ${cls}${RV.open.has(id) ? " open" : ""}${RV.selected === id ? " current" : ""}`, "data-step": id },
    h("div", { class: "step-head", onclick: () => {
      if (node.classList.toggle("open")) {
        RV.open.add(id);
        select(id, true);
      } else {
        RV.open.delete(id);
      }
    } }, head),
    h("div", { class: "step-body" }, body));
  return node;
}

function section(label, ...content) {
  return h("div", { class: "sec" }, h("div", { class: "sec-label" }, label), h("div", { class: "sec-body" }, content));
}

function textBlock(text, truncated, cls = "") {
  if (!text) return null;
  return h("pre", { class: `txt ${cls}` }, text + (truncated ? "\n…（已截断，完整内容见“完整输入/输出”）" : ""));
}

function thumbs(paths) {
  if (!paths?.length) return null;
  return h("div", { class: "thumbs" }, paths.map((p) => h("img", { src: fileUrl(p, 320), alt: p, title: "点击查看原图",
    onclick: (e) => { e.stopPropagation(); lightbox(fileUrl(p)); } })));
}

function lightbox(src) {
  const close = () => box.remove();
  const box = h("div", { class: "lightbox", onclick: close }, h("img", { src }));
  document.body.append(box);
}

function itemView(item) {
  const label = {
    message: `${{ developer: "开发者指令", user: "用户消息", assistant: "助手消息", system: "系统" }[item.role] || item.role} · message`,
    reasoning: "推理摘要",
    function_call: `工具调用 ${item.name}`,
    custom_tool_call: `脚本调用 ${item.name}`,
    function_call_output: "工具结果",
    custom_tool_call_output: "脚本结果",
    additional_tools: "工具清单（开发者）",
  }[item.type] || item.type;
  return h("div", { class: `item ${item.type}` },
    h("div", { class: "item-head" }, h("b", {}, label), h("span", { class: "faint small" },
      `${fmtInt(item.chars)} 字${item.images?.length ? ` · ${item.images.length} 张图` : ""}${item.inline_images ? ` · ${item.inline_images} 张内联图` : ""}`)),
    textBlock(item.text, item.truncated, item.type === "reasoning" ? "muted" : ""),
    thumbs(item.images));
}

function drawFlow() {
  const tr = RV.trace;
  const flow = $("rvFlow");
  const keep = flow.scrollTop;
  const nodes = [];
  nodes.push(h("div", { class: "flow-group" }, "准备阶段（固定代码，不涉及模型）"));
  for (const s of tr.stages) {
    const id = `stage:${s.id}`;
    nodes.push(step(id, "stage",
      [h("span", { class: "t mono" }, clock(s.at)), h("b", {}, s.title), originTag(s.origin), h("span", { class: "spacer" }),
        s.end && s.at ? h("span", { class: "faint small" }, secs(s.end - s.at)) : null],
      h("p", { class: "small" }, s.summary),
      s.frame ? thumbs(Object.values(s.frame.cameras)) : null,
      s.files?.length ? section("相关文件", h("div", { class: "chips" }, s.files.map((f) => h("button", { class: "chip-btn", onclick: () => openFile(f) }, f)))) : null,
      s.file ? section("文件", h("button", { class: "chip-btn", onclick: () => openFile(s.file) }, s.file)) : null,
      s.detail ? section("详情", h("pre", { class: "txt" }, pretty(s.detail))) : null));
  }
  nodes.push(h("div", { class: "flow-group" }, `Agent 循环：${tr.rounds.length} 轮模型调用`));
  const calls = new Map(tr.calls.map((c) => [c.call_id, c]));
  for (const r of tr.rounds) {
    nodes.push(roundStep(r, calls));
    for (const id of r.calls) {
      const c = calls.get(id);
      if (c) nodes.push(callStep(c, r));
    }
  }
  if (tr.ending) {
    nodes.push(h("div", { class: "flow-group" }, "结束"));
    nodes.push(step("ending", "stage end",
      [h("span", { class: "t mono" }, clock(endTime())), h("b", {}, "结果判定"), originTag("code"), h("span", { class: "spacer" }),
        pill(tr.ending.task_success ? "success" : "failure")],
      h("ol", { class: "small" }, tr.ending.chain.map((l) => h("li", {}, l))),
      section("环境结果 native_outcome", h("pre", { class: "txt" }, pretty(tr.ending.native_outcome))),
      tr.ending.finish_request && Object.keys(tr.ending.finish_request).length ? section("finish 请求", h("pre", { class: "txt" }, pretty(tr.ending.finish_request))) : null));
  }
  put(flow, nodes);
  flow.scrollTop = keep;
}

function roundStep(r, calls) {
  const id = `round:${r.index}`;
  const u = r.usage;
  const text = r.output.filter((o) => o.type === "message").map((o) => o.text).join("\n");
  const outCalls = r.calls.map((cid) => calls.get(cid)).filter(Boolean);
  const kindLabel = { initial: "首轮", tool_results: "工具结果返回", compaction_request: "上下文压缩", compacted: "压缩后重启", message: "新消息", continuation: "继续" }[r.trigger.kind];
  const summary = outCalls.length ? `→ ${outCalls.map((c) => c.name).join(", ")}` : text ? `→ 文字回复` : r.error ? "→ 出错" : "";
  return step(id, `round ${r.trigger.kind.startsWith("compact") ? "compact" : ""}`,
    [h("span", { class: "t mono" }, clock(r.started_at)), h("b", {}, `第 ${r.index + 1} 轮 · 模型调用`),
      h("span", { class: "tag" }, kindLabel), h("span", { class: "muted small ellipsis" }, summary), h("span", { class: "spacer" }),
      h("span", { class: "faint small" }, `${secs(r.duration_s)} · ${fmtTokens(u.input)} 入 / ${fmtTokens(u.output)} 出`)],
    section("为什么调用", h("div", { class: "why" }, originTag(r.trigger.kind === "initial" ? "code" : "runtime"), r.trigger.text)),
    section("输入",
      h("div", { class: "small muted" }, nativeReplay() ? `本轮新收到 ${r.new_input.length} 项消息 · 可用 EvoAB 工具 ${r.context.tools} 个` : `本轮新增 ${r.new_input.length} 项` + (r.echoed ? `（另有 ${r.echoed} 项是上一轮模型自己的输出，原样回传）` : "") +
        `；完整上下文 ${r.context.items} 项 · ${r.context.instructions_chars ? `系统指令 ${fmtInt(r.context.instructions_chars)} 字` : "系统指令在开发者消息中"} · 可用工具 ${r.context.tools} 个` +
        (r.context.truncated ? ` · 上下文策略截掉 ${r.context.dropped} 项` : "")),
      r.new_input.map(itemView),
      h("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); openRound(r.index, "input"); } }, nativeReplay() ? "查看本轮记录" : "查看完整输入（含系统指令、工具定义、实际发给模型的请求）")),
    section("调用与耗时",
      h("div", { class: "kv-grid" },
        kv("模型", `${r.model || "—"}${r.reasoning ? ` · 推理 ${r.reasoning}` : ""}`),
        kv("接口", `${r.endpoint || "—"}（${r.wire_profile || "—"}）`),
        kv("开始 → 结束", `${clock(r.started_at)} → ${clock(r.finished_at)}`),
        kv(nativeReplay() ? "整轮耗时（含工具执行）" : "耗时", secs(r.duration_s)),
        kv("尝试", r.attempts.length ? r.attempts.map((a) => `#${a.attempt} ${a.status ?? "—"} ${secs(a.elapsed_s)}`).join("，") : "—"),
        kv("状态", r.error ? `出错：${r.error}` : r.complete ? "完成" : "进行中"))),
    section("消耗",
      h("div", { class: "kv-grid" },
        kv("输入词元", `${fmtInt(u.input)}（缓存命中 ${fmtInt(u.cached)}，${u.input ? Math.round((100 * u.cached) / u.input) : 0}%）`),
        kv("输出词元", `${fmtInt(u.output)}（其中推理 ${fmtInt(u.reasoning)}）`))),
    section("输出", r.output.length ? r.output.map(itemView) : h("div", { class: "muted small" }, "无输出")),
    section("去向", outCalls.length
      ? h("ul", { class: "small" }, outCalls.map((c) => h("li", {}, h("a", { onclick: (e) => { e.stopPropagation(); select(`call:${c.call_id}`, true); } }, c.name),
          ` 由${ORIGIN[c.executor][0]}执行`, c.used_in_round != null ? `，结果作为第 ${c.used_in_round + 1} 轮的输入` : nativeReplay() ? "" : "，结果未被后续轮次读取")))
      : h("div", { class: "small" }, text ? "本轮模型回复了文字，没有调用工具。" : "—")));
}

const kv = (k, v) => [h("span", { class: "k" }, k), h("span", { class: "v" }, v)];

function robotFacts(x) {
  const f = x.robot;
  if (!f) return null;
  const arm = Object.entries(f.arm_feedback || {})[0];
  return h("div", { class: "kv-grid" },
    kv("环境步数", `${f.native_steps ?? "—"}${f.steps_executed != null ? `（本次执行 ${f.steps_executed} 步）` : ""}${f.remaining_steps != null ? `，剩余 ${f.remaining_steps}` : ""}`),
    f.stop_reason ? kv("停止原因", f.stop_reason) : null,
    f.pose_reached != null ? kv("到达目标", f.pose_reached ? "是" : "否") : null,
    arm && arm[1].position_error_m != null ? kv("位置误差", `${(arm[1].position_error_m * 100).toFixed(1)} cm`) : null,
    ...Object.entries(f.arms || {}).map(([name, a]) => kv(`末端 ${name}`, `${(a.position || []).map((v) => v.toFixed(3)).join(", ")} · 夹爪 ${a.gripper?.toFixed?.(2) ?? "—"}`)),
    f.ended ? kv("回合", "已结束") : null,
    f.error ? kv("错误", pretty(f.error)) : null);
}

function services(x) {
  const entries = Object.entries(x.service_calls || {});
  if (!entries.length) return null;
  return h("div", { class: "small muted" }, "环境服务请求：", entries.map(([k, v]) => `${k} × ${v.count}（${secs(v.seconds)}）`).join("；"));
}

function callStep(c, r) {
  const id = `call:${c.call_id}`;
  const res = c.result;
  return step(id, `call ${c.executor}`,
    [h("span", { class: "t mono" }, clock(c.started_at || c.issued_at)), h("b", { class: "mono" }, c.name), originTag(c.executor),
      c.success === false ? h("span", { class: "pill bad" }, "失败") : null, h("span", { class: "spacer" }),
      h("span", { class: "faint small" }, secs(c.duration_s))],
    section("谁发起", h("div", { class: "small" }, `第 ${r.index + 1} 轮模型输出了这次调用；${c.executor === "robot" ? `${agentName()} 将调用交给 EvoAB 工具执行器。` : c.executor === "script" ? "Codex 在沙箱里运行模型写的脚本，脚本再调用 EvoAB 机器人工具（见下方）。" : "由当前 Agent 的内置工具执行。"}`)),
    section(c.arguments?.script != null ? "输入（脚本）" : "输入（参数）", h("pre", { class: "txt" }, c.arguments?.script ?? pretty(c.arguments))),
    section("时间", h("div", { class: "kv-grid" },
      kv("发出", clock(c.issued_at)), c.queued_at ? kv("排队", clock(c.queued_at)) : null,
      kv("开始 → 结束", `${clock(c.started_at)} → ${clock(c.finished_at)}`), kv("用时", secs(c.duration_s)))),
    c.robot ? section("对环境的影响", robotFacts(c), services(c)) : null,
    c.nested.length ? section(`脚本里的机器人调用（${c.nested.length}）`, c.nested.map((n) => nestedView(n))) : null,
    section(nativeReplay() ? "工具执行结果" : "输出（模型看到的结果）", res ? itemView(res) : h("div", { class: "muted small" }, "尚未返回或未被记录")),
    section("去向", h("div", { class: "small" }, c.used_in_round != null
      ? [`结果被追加到对话，成为 `, h("a", { onclick: (e) => { e.stopPropagation(); select(`round:${c.used_in_round}`, true); } }, `第 ${c.used_in_round + 1} 轮`), " 的输入。"]
      : nativeReplay() ? "原生日志未记录后续请求与这次结果的关联。" : "没有后续轮次读取这个结果。")));
}

function nestedView(n) {
  return h("div", { class: "nested", "data-step": `nested:${n.transport_id}` },
    h("div", { class: "item-head" }, h("b", { class: "mono" }, n.name), h("span", { class: "faint small" },
      `${clock(n.started_at || n.queued_at)} · ${secs(n.duration_s)}${n.success === false ? " · 失败" : ""}`)),
    h("pre", { class: "txt" }, pretty(n.arguments)),
    robotFacts(n), services(n));
}

// -- full input / output drawer ---------------------------------------------

async function openRound(index, tab = "input") {
  const close = () => { backdrop.remove(); drawer.remove(); };
  const backdrop = h("div", { class: "drawer-backdrop", onclick: close });
  const body = h("div", { class: "drawer-body" }, h("div", { class: "muted" }, "读取完整请求…"));
  const tabs = h("div", { class: "tabs" });
  const drawer = h("div", { class: "drawer wide" },
    h("div", { class: "drawer-head" }, h("h2", {}, `第 ${index + 1} 轮 · ${nativeReplay() ? "原生 Agent 记录" : "完整输入与输出"}`), h("span", { class: "spacer" }),
      h("button", { class: "btn small", onclick: close }, "关闭")), tabs, body);
  document.body.append(backdrop, drawer);
  let d;
  try { d = await api(`/api/trace/${encodeURIComponent(RV.run.id)}/rounds/${index}`); } catch (error) {
    put(body, h("div", { class: "notice error" }, error.message)); return;
  }
  const fresh = new Set(RV.trace.rounds[index].new_input.map((i) => i.key));
  const views = {
    input: ["智能体收到的输入", () => [
      h("p", { class: "small muted" }, `Codex 发给网关的 Responses 请求：系统指令 + ${d.input.length} 项对话。标黄的是本轮新增内容。`),
      h("details", { class: "sys" }, h("summary", {}, `系统指令（instructions，${fmtInt(d.instructions.length)} 字）`), h("pre", { class: "txt" }, d.instructions)),
      d.input.map((item) => { const v = itemView({ ...item, truncated: false }); if (fresh.has(item.key)) v.classList.add("fresh"); return v; })]],
    wire: ["实际发给模型的请求", () => [
      h("p", { class: "small muted" }, `网关按 ${d.provider_request.wire_profile || "—"} 转换后发往 ${d.provider_request.endpoint || "—"}；图片以内联数据发送，这里只计数。`),
      h("pre", { class: "txt" }, pretty(d.provider_request.settings)),
      d.provider_request.messages.map((m) => h("div", { class: "item" },
        h("div", { class: "item-head" }, h("b", {}, m.role), h("span", { class: "faint small" },
          `${fmtInt(m.text.length)} 字${m.images ? ` · ${m.images} 张图` : ""}${m.tool_call_id ? ` · 回复 ${m.tool_call_id}` : ""}`)),
        textBlock(m.text), m.tool_calls.length ? h("pre", { class: "txt" }, pretty(m.tool_calls)) : null))]],
    output: ["模型输出", () => d.output.map((item) => itemView({ ...item, truncated: false }))],
    tools: ["可用工具定义", () => d.tools.map((t) => h("details", { class: "sys" },
      h("summary", {}, `${t.name}${t.tools ? `（${t.tools.length} 个子工具）` : ""}`),
      t.description ? h("pre", { class: "txt" }, t.description) : null,
      t.tools ? t.tools.map((x) => h("details", { class: "sys inner" }, h("summary", { class: "mono" }, x.name),
        h("pre", { class: "txt" }, x.description), x.parameters ? h("pre", { class: "txt" }, pretty(x.parameters)) : null))
        : t.parameters ? h("pre", { class: "txt" }, pretty(t.parameters)) : null))],
    meta: ["原始记录", () => h("pre", { class: "txt" }, pretty(d.meta))],
  };
  if (d.kind === "native") {
    views.input = ["本轮新增输入", () => d.input.map(itemView)];
    delete views.wire;
  }
  const show = (name) => {
    put(tabs, Object.entries(views).map(([k, [label]]) => h("button", { class: k === name ? "active" : "", onclick: () => show(k) }, label)));
    put(body, views[name][1]());
    body.scrollTop = 0;
  };
  show(tab);
}

async function openFile(name) {
  const close = () => { backdrop.remove(); drawer.remove(); };
  const backdrop = h("div", { class: "drawer-backdrop", onclick: close });
  const body = h("div", { class: "drawer-body" }, h("div", { class: "muted" }, "读取…"));
  const drawer = h("div", { class: "drawer wide" },
    h("div", { class: "drawer-head" }, h("h2", { class: "mono" }, name), h("span", { class: "spacer" }),
      h("button", { class: "btn small", onclick: close }, "关闭")), body);
  document.body.append(backdrop, drawer);
  try {
    const response = await fetch(fileUrl(name));
    put(body, h("pre", { class: "txt" }, response.ok ? await response.text() : `无法读取（${response.status}）`));
  } catch (error) {
    put(body, h("div", { class: "notice error" }, error.message));
  }
}

// Periodic refresh from the shell: keep the header current; the trace polls itself.
function refreshRun(run) {
  const same = RV.run && (RV.run.key === run.key || (RV.run.launch_id && RV.run.launch_id === run.launch_id));
  if (!same) return;
  const promoted = !RV.run.id && !!run.id;
  RV.run = run;
  if (promoted) {
    current.arg = run.key;
    if (location.hash !== `#${run.key}`) history.replaceState(null, "", `#${run.key}`);
  }
  renderHead();
  if (run.id && !RV.trace) loadTrace();
  if (!run.id || promoted) renderLaunchLog(run);
}
