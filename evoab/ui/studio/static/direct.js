// Direct-policy replay: every control step and every inference of one run.
//
// The model answers each inference with one action chunk; the controller runs
// one row per control step. The player plays control steps at the control
// rate. "Include inference time" inserts each inference's real wait, so the
// timeline also shows how long the robot stood still while the model thought.
"use strict";

const DP = { tr: null, phases: [], total: 0, pos: 0, playing: false, raf: null, last: 0, speed: 1,
  wait: false, replayEnd: null, chunkOpen: false, follow: true, frameByStep: new Map(), cache: new Map() };

const pad2 = (n) => String(n).padStart(2, "0");
const simClock = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const num = (v, d = 3) => (v == null ? "—" : Number(v).toFixed(d));

function directPhases(tr, wait) {
  const hz = tr.policy.control_hz || 20;
  const phases = [];
  let at = 0;
  tr.calls.forEach((c, i) => {
    const thinking = (c.request_build_s || 0) + (c.api_latency_s || 0);
    if (wait) { phases.push({ kind: "wait", call: i, start: at, dur: thinking, step: c.start_step }); at += thinking; }
    const steps = c.executed.map((e) => e.step);
    if (steps.length) { phases.push({ kind: "act", call: i, start: at, dur: steps.length / hz, steps }); at += steps.length / hz; }
    else if (c.error) phases.push({ kind: "error", call: i, start: at, dur: 0, step: c.start_step });
  });
  return { phases, total: at };
}

function stateAt(pos) {
  const { phases } = DP;
  if (!phases.length) return { call: -1, row: -1, step: 0, kind: "idle" };
  let p = phases.find((x) => pos >= x.start && pos < x.start + x.dur) || phases[phases.length - 1];
  if (p.kind === "error") return { call: p.call, row: -1, step: p.step, kind: "error" };
  if (p.kind === "wait") return { call: p.call, row: -1, step: p.step, kind: "wait", into: pos - p.start, dur: p.dur };
  const row = Math.min(p.steps.length - 1, Math.max(0, Math.floor((pos - p.start) / (p.dur / p.steps.length))));
  return { call: p.call, row, step: p.steps[row], kind: "act" };
}

function posOfStep(step) {
  for (const p of DP.phases) if (p.kind === "act" && p.steps.includes(step)) return p.start + (p.steps.indexOf(step) + 0.001) * (p.dur / p.steps.length);
  const first = DP.phases.find((p) => p.kind === "act" && p.steps[0] >= step);
  return first ? first.start : DP.total;
}

function posOfCall(i) {
  const p = DP.phases.find((x) => x.call === i);
  return p ? p.start : 0;
}

// -- entry -------------------------------------------------------------------

function renderDirect(first) {
  const tr = RV.trace;
  DP.tr = tr;
  DP.frameByStep = new Map(tr.frames.map((f) => [f.step, f]));
  Object.assign(DP, directPhases(tr, DP.wait));
  if (first || (DP.follow && !DP.playing)) DP.pos = DP.total;
  let root = $("rvBody").querySelector(".dp");
  if (!root) {
    root = h("div", { class: "dp" },
      h("div", { class: "dp-player" },
        h("div", { class: "dp-cams", id: "dpCams" }),
        h("div", { class: "dp-track" }, h("div", { class: "dp-segments", id: "dpSegments" }),
          h("input", { type: "range", class: "dp-seek", id: "dpSeek", "aria-label": "回放进度", min: 0, max: 1000, value: 0,
            oninput: (e) => { pause(); DP.follow = false; DP.pos = (Number(e.target.value) / 1000) * DP.total; drawDirect(); } })),
        h("div", { class: "dp-legend", id: "dpLegend" }),
        h("div", { class: "dp-playback" },
          h("div", { class: "dp-transport" },
            h("button", { class: "btn small dp-play", id: "dpPlay", onclick: () => (DP.playing ? pause() : play()) }, "▶ 播放"),
            h("button", { class: "btn small", onclick: replayCall }, "重播本次推理"),
            h("button", { class: "btn small", id: "dpPrev", title: "上一次推理", onclick: () => jumpCall(-1) }, "‹"),
            h("button", { class: "btn small", id: "dpNext", title: "下一次推理", onclick: () => jumpCall(1) }, "›"),
            h("span", { class: "mono dp-time", id: "dpTime" }),
            h("select", { class: "dp-speed", "aria-label": "播放速度", onchange: (e) => { DP.speed = Number(e.target.value); } },
              [0.25, 0.5, 1, 2, 4, 8].map((s) => h("option", { value: s, selected: s === DP.speed }, `${s}×`))),
            h("label", { class: "dp-toggle", title: "把每次推理的真实等待时间插入时间轴" },
              h("input", { type: "checkbox", checked: DP.wait, onchange: (e) => toggleWait(e.target.checked) }), "计入推理等待")),
          h("div", { class: "dp-telemetry", id: "dpTelemetry" })),
        h("p", { class: "dp-alert", id: "dpAlert", hidden: true }),
        h("div", { class: "dp-note" }, h("span", { class: "eyebrow" }, "模型动作说明"), h("p", { id: "dpNote" })),
        h("section", { class: "dp-output", id: "dpOutput" })),
      h("div", { class: "dp-analysis" },
        h("details", { class: "dp-chart-card", ontoggle: (e) => { if (e.currentTarget.open) { drawChart(); drawChartCursor(stateAt(DP.pos).step); } } }, h("summary", {}, "夹爪运动轨迹"),
          h("span", { class: "muted small" }, "横轴是控制步。X、Y、Z 是夹爪位置（米），末行是闭合度（0 张开，1 闭合）。点是控制器执行的目标，实线是实测值。点击可跳到对应画面。"), h("div", { id: "dpChart" })),
        h("details", { class: "dp-calls-card" }, h("summary", {}, "推理记录"),
          h("span", { class: "muted small", id: "dpTotals" }), h("div", { class: "dp-calls", id: "dpCalls" })),
        h("details", { class: "dp-prompt-card" }, h("summary", {}, "系统提示词"),
          h("span", { class: "muted small" }, "各次请求共用以下提示词。"), h("div", { id: "dpPrompt" }))));
    put($("rvBody"), root);
    document.addEventListener("keydown", directKeys);
  }
  drawStatic();
  drawDirect();
}

function directKeys(e) {
  if (!DP.tr || !document.querySelector(".dp") || e.target.closest("input,select,textarea,button,summary,a,[contenteditable=true]")) return;
  if (e.key === " ") { e.preventDefault(); DP.playing ? pause() : play(); }
  else if (e.key === "ArrowLeft") jumpCall(-1);
  else if (e.key === "ArrowRight") jumpCall(1);
}

// -- parts that change only when the trace reloads ---------------------------

function drawStatic() {
  const { tr } = DP;
  const p = tr.policy;
  // Cameras: one labelled image per camera, side by side.
  const cams = p.cameras || Object.keys(tr.frames[0]?.images || {});
  if ($("dpCams").childElementCount !== cams.length) {
    put($("dpCams"), cams.map((name, i) => h("figure", { class: "dp-cam" },
      h("figcaption", {}, cameraLabel(name, i)),
      h("div", { class: "dp-frame" }, h("img", { "data-cam": name, alt: cameraLabel(name, i) }),
        h("div", { class: "dp-overlay", hidden: true })))));
  }
  // Track: one segment per phase; inference waits hatched when shown.
  const phaseType = (ph) => ph.kind === "act" && tr.calls[ph.call].safe_hold ? "hold" : ph.kind;
  const labels = { wait: "等待模型", act: "执行动作", hold: "保持位姿一步", error: "推理失败 · 未执行" };
  const phases = DP.phases.filter((ph) => ph.dur > 0 || ph.kind === "error");
  put($("dpSegments"), phases.map((ph) => {
    const type = phaseType(ph);
    const retried = ph.kind !== "wait" && tr.calls[ph.call].output_attempts > 1;
    return h("span", { class: retried ? `${type} retried` : type, style: `left:min(${100 * ph.start / (DP.total || 1)}%, calc(100% - 2px));width:${100 * ph.dur / (DP.total || 1)}%`,
      title: `第 ${ph.call + 1} 次推理 · ${labels[type]} · 尝试 ${tr.calls[ph.call].output_attempts || 1} 次` });
  }));
  const types = new Set(phases.map(phaseType));
  put($("dpLegend"), Object.entries(labels).filter(([type]) => types.has(type)).map(([type, label]) =>
    h("span", {}, h("i", { class: type }), label)),
    tr.calls.some((c) => c.output_attempts > 1) ? h("span", {}, h("i", { class: "retried" }), "推理重试") : null);
  const t = tr.totals;
  $("dpTotals").textContent = `${t.calls} 次 · ${t.steps} 步 · API ${secs(t.api_s)} · ${fmtTokens(t.total_tokens)} 词元`
    + (t.repairs ? ` · 重试 ${t.repairs}` : "") + (t.safe_holds ? ` · 保持 ${t.safe_holds}` : "");
  put($("dpCalls"), tr.calls.map((c, i) => h("button", { class: "dp-call", "data-call": i, onclick: () => { pause(); DP.follow = false; DP.pos = posOfCall(i) + (DP.wait ? 0 : 1e-6); drawDirect(); } },
    h("span", { class: "mono dp-call-n" }, pad2(c.index)),
    h("span", { class: "dp-call-main" },
      h("span", { class: "dp-call-line" }, h("b", { class: "mono" }, `${c.start_step}→${c.end_step}`), ` · ${c.horizon} 步 · ${secs(c.api_latency_s)} · ${fmtTokens(c.usage.total_tokens)}`,
        c.safe_hold ? h("span", { class: "tag bad" }, "保持") : null,
        (c.output_attempts || 1) > 1 ? h("span", { class: "tag warn" }, `重试×${c.output_attempts - 1}`) : null,
        c.safety_clamped ? h("span", { class: "tag" }, "限幅") : null),
      h("span", { class: "dp-call-note" }, c.note || errorLabel(c.error))),
    h("span", { class: "dp-call-open", title: "查看这次推理的完整请求与回复", onclick: (e) => { e.stopPropagation(); openInference(i); } }, "详情"))));
  put($("dpPrompt"), Object.entries(tr.prompt_blocks || {}).map(([name, text]) =>
    h("details", { class: "sys" }, h("summary", {}, `${PROMPT_BLOCKS[name] || name} · ${fmtInt(text.length)} 字`), h("pre", { class: "txt" }, text))),
    h("button", { class: "btn small ghost", onclick: () => openFile("direct/prompt.md") }, "完整提示词"),
    h("button", { class: "btn small ghost", onclick: () => openFile("direct/policy.json") }, "策略配置"));
  if ($("dpChart").closest("details").open) drawChart();
}

function errorLabel(message) {
  if (!message) return "";
  if (/timed?\s*out|timeout/i.test(message)) return "接口请求超时";
  if (/connection error/i.test(message)) return "接口连接失败";
  if (/provider error/i.test(message)) return "接口调用失败，详见尝试记录";
  return "动作输出异常，详见尝试记录";
}

function finishLabel(value) {
  return ({ error: "请求出错", tool_calls: "返回工具调用", stop: "正常结束", length: "达到输出上限",
    content_filter: "内容被过滤" })[value] || value || "未记录结束状态";
}

const PROMPT_BLOCKS = { robot: "本体与坐标系", timing: "执行时序与步长限制", gripper: "夹爪几何", camera: "相机几何", action: "输出格式" };

// -- parts that follow the playhead ------------------------------------------

function drawDirect() {
  const { tr } = DP;
  if (!tr) return;
  const s = stateAt(DP.pos);
  const call = tr.calls[s.call];
  const frame = DP.frameByStep.get(s.step) || tr.frames[tr.frames.length - 1];
  // Cameras
  for (const img of document.querySelectorAll(".dp-cam img")) {
    const path = frame?.images?.[img.dataset.cam];
    if (path && img.dataset.path !== path) { img.src = fileUrl(path); img.dataset.path = path; }
  }
  preload(s.step);
  document.querySelector(".dp-player").classList.toggle("waiting", s.kind === "wait");
  for (const [i, overlay] of [...document.querySelectorAll(".dp-overlay")].entries()) {
    overlay.hidden = s.kind !== "wait" || i > 0;
    if (s.kind === "wait") put(overlay, h("span", { class: "eyebrow" }, `第 ${s.call + 1} 次推理 · 等待模型`),
      h("b", { class: "mono" }, `${s.into.toFixed(1)} 秒 / ${s.dur.toFixed(1)} 秒`));
  }
  // Track
  $("dpSeek").value = DP.total ? Math.round((DP.pos / DP.total) * 1000) : 0;
  $("dpSegments").style.setProperty("--position", `${DP.total ? 100 * DP.pos / DP.total : 0}%`);
  $("dpSeek").setAttribute("aria-valuetext", call ? `第 ${s.call + 1} 次推理 · ${s.kind === "wait" ? "等待模型" : s.kind === "error" ? "推理失败" : call.safe_hold ? "原地保持" : "执行动作"} · 第 ${s.step} 步` : "等待记录");
  const hz = tr.policy.control_hz || 20;
  const lastStep = tr.frames.length ? tr.frames[tr.frames.length - 1].step : 0;
  $("dpTime").textContent = DP.wait ? `含等待 ${simClock(DP.pos)} / ${simClock(DP.total)}` : `仿真 ${simClock(s.step / hz)} / ${simClock(lastStep / hz)}`;
  $("dpTime").title = DP.wait ? "推理等待和仿真动作的累计播放时间" : "仿真时间（不含推理等待）";
  $("dpPrev").disabled = s.call <= 0;
  $("dpNext").disabled = s.call >= tr.calls.length - 1;
  const metric = (k, v) => h("div", { class: "dp-metric" }, h("span", { class: "eyebrow" }, k), h("strong", { class: "mono" }, v));
  put($("dpTelemetry"),
    metric("推理", call ? `${pad2(s.call + 1)} / ${tr.calls.length}` : "—"),
    metric("控制步", `${s.step}${tr.max_steps ? ` / ${tr.max_steps}` : ""}`),
    metric("块内动作", call && s.row >= 0 ? `${pad2(s.row + 1)} / ${call.horizon}` : s.kind === "wait" ? "等待模型" : "—"),
    metric("运行开始至发起请求", call?.requested_at != null && tr.t0 != null ? fmtDuration(call.requested_at - tr.t0) : "—"));
  // Alerts
  const alerts = [];
  if (call?.safety_clamped) alerts.push("控制器已限制动作幅度");
  if ((call?.output_attempts || 1) > 1) alerts.push(`输出尝试 ${call.output_attempts} 次`);
  if (call?.safe_hold) alerts.push("旧版控制器在尝试用尽后执行了一步保持位姿的动作");
  if (call?.error) alerts.push(errorLabel(call.error));
  if (call?.execution_error) alerts.push("动作执行出错，详见完整请求与回复");
  $("dpAlert").hidden = !alerts.length;
  $("dpAlert").textContent = alerts.join(" · ");
  $("dpNote").parentElement.hidden = !call?.note;
  $("dpNote").textContent = call?.note || "";
  drawOutput(call, s);
  drawChartCursor(s.step);
  document.querySelectorAll(".dp-call").forEach((el) => el.classList.toggle("active", Number(el.dataset.call) === s.call));
}

function drawOutput(call, s) {
  const out = $("dpOutput");
  if (!call) { put(out); return; }
  if (!call.executed.length && call.error) {
    out.dataset.call = "";
    put(out, h("span", { class: "small" }, `第 ${call.start_step} 步 · 推理失败，未执行动作`),
      h("button", { class: "btn small ghost", onclick: () => openInference(call.index - 1) }, "查看尝试记录"));
    return;
  }
  const p = DP.tr.policy;
  const eef = (p.columns || [])[0] === "x" || p.representation === "delta_eef";
  const key = `${call.index}`;
  if (out.dataset.call !== key) {
    out.dataset.call = key;
    const executed = new Map(call.executed.map((e) => [e.row, e]));
    const head = ["动作", "控制步", "X", "Y", "Z", "qw", "qx", "qy", "qz", "夹爪闭合度", "实测位置", "位置误差"];
    const rows = call.targets.map((t, i) => {
      const e = executed.get(i);
      const m = e?.measured?.ee_pos;
      const err = m ? Math.hypot(t[0] - m[0], t[1] - m[1], t[2] - m[2]) * 1000 : null;
      return h("tr", { "data-row": i, class: [call.clamped?.[i] ? "clamped" : "", e ? "" : "skipped"].join(" ") },
        h("td", {}, h("span", { class: "mark" }), pad2(i + 1)), h("td", {}, e ? e.step : "—"),
        t.slice(0, 7).map((v, k) => h("td", {}, num(v, k < 3 ? 3 : 4))), h("td", {}, num(t[7], 2)),
        h("td", { class: "muted" }, m ? m.map((v) => num(v, 3)).join(", ") : "未执行"),
        h("td", { class: err != null && err > 10 ? "warn" : "" }, err == null ? "—" : `${err.toFixed(1)} mm`));
    });
    put(out,
      h("div", { class: "dp-output-head" },
        h("button", { class: "dp-toggle-chunk", "aria-expanded": String(DP.chunkOpen), onclick: () => { DP.chunkOpen = !DP.chunkOpen; out.dataset.call = ""; drawDirect(); } }, "动作明细"),
        h("span", {}, `${call.horizon} 个目标 · 控制步 ${call.start_step + 1}–${call.end_step}`),
        h("span", {}, `请求耗时 ${num(call.api_latency_s, 2)} 秒`),
        h("span", {}, `输入 ${fmtTokens(call.usage.input_tokens)} · 输出 ${fmtTokens(call.usage.output_tokens)} 词元`),
        DP.chunkOpen && eef ? h("span", { class: "muted" }, "位置单位为米，姿态为四元数 wxyz，夹爪 0 张开、1 闭合") : null,
        h("span", { class: "spacer" }),
        h("button", { class: "btn small ghost", onclick: () => openInference(call.index - 1) }, "完整请求与回复")),
      DP.chunkOpen ? h("div", { class: "dp-table" }, h("table", {}, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))), h("tbody", {}, rows))) : null,
      DP.chunkOpen && p.representation !== "abs_eef" ? h("p", { class: "small muted" }, "表中为解码后的绝对目标；模型原始输出见“完整请求与回复”。") : null);
  }
  out.querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("current", Number(tr.dataset.row) === s.row));
}

// -- trajectory chart --------------------------------------------------------

const CHART = { padL: 40, padR: 6, padT: 6, padB: 16 };

function drawChart() {
  const { tr } = DP;
  const frames = tr.frames;
  if (frames.length < 2) { put($("dpChart"), h("div", { class: "muted small" }, "等待更多控制步…")); return; }
  // Scale to the steps actually taken; the budget is shown in the header.
  const maxStep = Math.max(10, frames[frames.length - 1].step);
  const { padL, padR, padT, padB } = CHART;
  const w = Math.max(300, $("dpChart").clientWidth);
  const laneH = 64;
  const lanes = [["x", 0], ["y", 1], ["z", 2]];
  const height = padT + lanes.length * laneH + 34 + padB;
  const X = (step) => padL + ((w - padL - padR) * step) / (maxStep || 1);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${height}`);
  svg.setAttribute("class", "dp-chart");
  const add = (tag, attrs, text) => { const el = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); if (text != null) el.textContent = text; svg.append(el); return el; };
  const targets = [];
  for (const c of tr.calls) for (const e of c.executed) targets.push({ step: e.step, t: c.targets[e.row], hold: c.safe_hold });
  lanes.forEach(([name, k], li) => {
    const top = padT + li * laneH;
    const values = [...frames.map((f) => f.proprio.ee_pos[k]), ...targets.map((x) => x.t[k])];
    let lo = Math.min(...values), hi = Math.max(...values);
    if (hi - lo < 0.02) { const mid = (hi + lo) / 2; lo = mid - 0.01; hi = mid + 0.01; }
    const Y = (v) => top + laneH - 6 - ((laneH - 12) * (v - lo)) / (hi - lo);
    add("line", { x1: padL, x2: w - padR, y1: top + laneH, y2: top + laneH, class: "grid" });
    add("text", { x: 4, y: top + 14, class: "lbl" }, name);
    add("text", { x: 4, y: top + 26, class: "lbl faint" }, hi.toFixed(2));
    add("text", { x: 4, y: top + laneH - 4, class: "lbl faint" }, lo.toFixed(2));
    add("polyline", { points: frames.map((f) => `${X(f.step).toFixed(1)},${Y(f.proprio.ee_pos[k]).toFixed(1)}`).join(" "), class: `meas c${k}` });
    for (const x of targets) add("circle", { cx: X(x.step).toFixed(1), cy: Y(x.t[k]).toFixed(1), r: 1.6, class: `tgt c${k}${x.hold ? " hold" : ""}` });
  });
  const gTop = padT + lanes.length * laneH + 6;
  add("text", { x: 4, y: gTop + 16, class: "lbl" }, "闭合度");
  add("polyline", { points: frames.map((f) => `${X(f.step).toFixed(1)},${(gTop + 26 - 22 * f.proprio.gripper_closure).toFixed(1)}`).join(" "), class: "meas grip" });
  for (const x of targets) add("circle", { cx: X(x.step).toFixed(1), cy: (gTop + 26 - 22 * x.t[7]).toFixed(1), r: 1.4, class: "tgt grip" });
  for (const c of tr.calls) add("line", { x1: X(c.start_step), x2: X(c.start_step), y1: padT, y2: height - padB, class: "callmark" });
  add("text", { x: padL, y: height - 4, class: "lbl faint" }, "0");
  add("text", { x: w - padR, y: height - 4, class: "lbl faint", "text-anchor": "end" }, `${maxStep} 步`);
  add("line", { x1: 0, x2: 0, y1: padT, y2: height - padB, class: "cursor", id: "dpCursor" });
  svg.addEventListener("click", (e) => {
    const box = svg.getBoundingClientRect();
    const step = Math.round(((e.clientX - box.left) / box.width * w - padL) / (w - padL - padR) * maxStep);
    pause(); DP.follow = false; DP.pos = posOfStep(Math.max(0, step)); drawDirect();
  });
  svg.dataset.maxStep = maxStep;
  put($("dpChart"), svg);
}

function drawChartCursor(step) {
  const cursor = document.getElementById("dpCursor");
  if (!cursor) return;
  const maxStep = Number(cursor.ownerSVGElement.dataset.maxStep) || 1;
  const x = CHART.padL + ((cursor.ownerSVGElement.viewBox.baseVal.width - CHART.padL - CHART.padR) * step) / maxStep;
  cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
}

// -- playback ----------------------------------------------------------------

function play() {
  if (DP.pos >= DP.total - 1e-6) DP.pos = 0;
  DP.playing = true; DP.follow = false; DP.last = performance.now();
  $("dpPlay").textContent = "Ⅱ 暂停";
  const tick = (now) => {
    if (!DP.playing) return;
    DP.pos = Math.min(DP.total, DP.pos + ((now - DP.last) / 1000) * DP.speed);
    DP.last = now;
    if (DP.replayEnd != null && DP.pos >= DP.replayEnd) { DP.pos = DP.replayEnd - 1e-6; pause(); }
    else if (DP.pos >= DP.total) pause();
    drawDirect();
    if (DP.playing) DP.raf = requestAnimationFrame(tick);
  };
  DP.raf = requestAnimationFrame(tick);
}

function pause() {
  DP.playing = false; DP.replayEnd = null;
  cancelAnimationFrame(DP.raf);
  const b = $("dpPlay");
  if (b) b.textContent = "▶ 播放";
}

function replayCall() {
  const s = stateAt(DP.pos);
  if (s.call < 0) return;
  const parts = DP.phases.filter((p) => p.call === s.call);
  pause();
  if (!parts.length || !parts.some((p) => p.dur > 0)) return;
  DP.pos = parts[0].start;
  const end = parts[parts.length - 1];
  play();
  DP.replayEnd = end.start + end.dur;
}

function jumpCall(direction) {
  const s = stateAt(DP.pos);
  const next = Math.max(0, Math.min(DP.tr.calls.length - 1, s.call + direction));
  pause(); DP.follow = false;
  DP.pos = posOfCall(next) + 1e-6;
  drawDirect();
}

function toggleWait(on) {
  const state = stateAt(DP.pos);
  DP.wait = on;
  Object.assign(DP, directPhases(DP.tr, on));
  DP.pos = state.kind === "error" ? DP.total : posOfStep(state.step);
  drawStatic();
  drawDirect();
}

function preload(step) {
  for (let s = step + 1; s <= step + 12; s++) {
    const f = DP.frameByStep.get(s);
    if (!f) continue;
    for (const path of Object.values(f.images)) {
      if (DP.cache.has(path)) continue;
      const img = new Image(); img.src = fileUrl(path); DP.cache.set(path, img);
      if (DP.cache.size > 400) DP.cache.delete(DP.cache.keys().next().value);
    }
  }
}

// -- inference detail --------------------------------------------------------

async function openInference(i) {
  const close = () => { backdrop.remove(); drawer.remove(); };
  const backdrop = h("div", { class: "drawer-backdrop", onclick: close });
  const body = h("div", { class: "drawer-body" }, h("div", { class: "muted" }, "读取完整请求…"));
  const tabs = h("div", { class: "tabs" });
  const call = DP.tr.calls[i];
  const drawer = h("div", { class: "drawer wide" },
    h("div", { class: "drawer-head" }, h("h2", {}, `推理 ${pad2(i + 1)} · 控制步 ${call.start_step}→${call.end_step}`), h("span", { class: "spacer" }),
      h("button", { class: "btn small", onclick: close }, "关闭")), tabs, body);
  document.body.append(backdrop, drawer);
  let d;
  try { d = await api(`/api/trace/${encodeURIComponent(RV.run.id)}/rounds/${i}`); } catch (error) {
    put(body, h("div", { class: "notice error" }, error.message)); return;
  }
  const part = (p) => p.type === "image" ? h("img", { class: "dp-req-img", src: fileUrl(p.path), onclick: () => lightbox(fileUrl(p.path)) })
    : h("pre", { class: "txt" }, p.text ?? pretty(p));
  const views = {
    timing: ["时间与消耗", () => [
      h("div", { class: "dp-facts" },
        ...kv("请求时刻", clock(call.requested_at)), ...kv("组装请求", secs(call.request_build_s)),
        ...kv("请求耗时（含重试）", secs(call.api_latency_s)), ...kv("执行动作块", secs(call.execution_s)),
        ...kv("观测的控制步", (call.observed_steps || []).join(", ")), ...kv("输出尝试", String(call.output_attempts)),
        ...kv("输入词元", fmtInt(call.usage.input_tokens)), ...kv("输出词元", fmtInt(call.usage.output_tokens)),
        ...kv("缓存命中", fmtInt(call.usage.cached_input_tokens)), ...kv("触发方式", "固定代码：上一动作块执行完或回合开始后立即发起")),
      (d.attempts || []).map((a) => h("div", { class: `item ${a.error ? "bad" : ""}` },
        h("div", { class: "item-head" }, h("b", {}, `第 ${a.attempt} 次尝试`), h("span", { class: "faint small" },
          `${secs(a.latency_s)} · ${finishLabel(a.finish_reason)} · ${fmtTokens(a.usage?.total_tokens)} 词元`)),
        a.error ? h("p", { class: "small bad-text" }, a.finish_reason === "error" ? `API 调用出错 · ${a.error}` : `动作校验未通过 · ${a.error}`) : null,
        a.reasoning ? h("details", { class: "sys" }, h("summary", {}, `模型推理（${fmtInt(a.reasoning.length)} 字）`), h("pre", { class: "txt" }, a.reasoning)) : null,
        a.content && a.finish_reason !== "error" ? h("pre", { class: "txt" }, a.content) : null,
        (a.tool_calls || []).length ? h("pre", { class: "txt" }, pretty(a.tool_calls)) : null))]],
    input: ["模型看到的输入", () => [
      h("p", { class: "small muted" }, "每次推理是独立请求：系统提示词 + 目标 + 最近几帧观测（图像与本体状态），不包含之前的回复。"),
      d.messages.map((m) => h("div", { class: "item" }, h("div", { class: "item-head" }, h("b", {}, ({ system: "系统", developer: "开发者", user: "用户", assistant: "助手", tool: "工具" })[m.role] || m.role)),
        typeof m.content === "string" ? h("pre", { class: "txt" }, m.content) : h("div", { class: "dp-req-parts" }, m.content.map(part))))]],
    output: ["输出与执行", () => [
      h("p", { class: "small muted" }, "模型原始参数 → 解码与限幅后的目标 → 每行执行后的测量状态。"),
      h("pre", { class: "txt" }, pretty(d.arguments)),
      h("pre", { class: "txt" }, pretty((d.executed || []).map((e) => ({ row: e.row + 1, step: e.step, target: d.targets[e.row], measured: e.measured }))))]],
    tools: ["工具定义", () => h("pre", { class: "txt" }, pretty({ tool_choice: d.tool_choice, tools: d.tools, model: d.model }))],
  };
  const show = (name) => {
    put(tabs, Object.entries(views).map(([k, [label]]) => h("button", { class: k === name ? "active" : "", onclick: () => show(k) }, label)));
    put(body, views[name][1]());
    body.scrollTop = 0;
  };
  show("timing");
}
