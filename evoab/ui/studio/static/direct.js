// Direct-policy replay: every control step and every inference of one run.
//
// The model answers each inference with one action chunk; the controller runs
// one row per control step. The player plays control steps at the control
// rate. "Include inference time" inserts each inference's real wait, so the
// timeline also shows how long the robot stood still while the model thought.
"use strict";

const DP = { tr: null, phases: [], total: 0, pos: 0, playing: false, raf: null, last: 0, speed: 1,
  wait: false, replayEnd: null, chunkOpen: true, follow: true, frameByStep: new Map(), cache: new Map() };

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
  });
  return { phases, total: at };
}

function stateAt(pos) {
  const { phases } = DP;
  if (!phases.length) return { call: -1, row: -1, step: 0, kind: "idle" };
  let p = phases.find((x) => pos >= x.start && pos < x.start + x.dur) || phases[phases.length - 1];
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
  if (first || (DP.follow && !DP.playing)) DP.pos = Math.max(0, DP.total - 1e-6);
  let root = $("rvBody").querySelector(".dp");
  if (!root) {
    root = h("div", { class: "dp" },
      h("div", { class: "card dp-player" },
        h("div", { class: "dp-selection", id: "dpSel" }),
        h("div", { class: "dp-instruction" }, h("span", { class: "eyebrow" }, "Task instruction"),
          h("p", { id: "dpInstruction" }), h("span", { class: "dp-result", id: "dpResult" })),
        h("div", { class: "dp-cams", id: "dpCams" }),
        h("div", { class: "dp-track" }, h("div", { class: "dp-segments", id: "dpSegments" }),
          h("input", { type: "range", class: "dp-seek", id: "dpSeek", min: 0, max: 1000, value: 0,
            oninput: (e) => { pause(); DP.follow = false; DP.pos = (Number(e.target.value) / 1000) * DP.total; drawDirect(); } })),
        h("div", { class: "dp-playback" },
          h("div", { class: "dp-transport" },
            h("button", { class: "btn small dp-play", id: "dpPlay", onclick: () => (DP.playing ? pause() : play()) }, "▶ Play"),
            h("button", { class: "btn small", onclick: replayCall }, "Replay call"),
            h("button", { class: "btn small", id: "dpPrev", title: "上一次推理", onclick: () => jumpCall(-1) }, "‹"),
            h("button", { class: "btn small", id: "dpNext", title: "下一次推理", onclick: () => jumpCall(1) }, "›"),
            h("span", { class: "mono dp-time", id: "dpTime" }),
            h("select", { class: "dp-speed", onchange: (e) => { DP.speed = Number(e.target.value); } },
              [0.25, 0.5, 1, 2, 4, 8].map((s) => h("option", { value: s, selected: s === DP.speed }, `${s}×`))),
            h("label", { class: "dp-toggle", title: "把每次推理的真实等待时间插入时间轴" },
              h("input", { type: "checkbox", checked: DP.wait, onchange: (e) => toggleWait(e.target.checked) }), "Include inference time")),
          h("div", { class: "dp-telemetry", id: "dpTelemetry" })),
        h("p", { class: "dp-alert", id: "dpAlert", hidden: true }),
        h("section", { class: "dp-output", id: "dpOutput" }),
        h("div", { class: "dp-note" }, h("span", { class: "eyebrow" }, "Model note"), h("p", { id: "dpNote" }))),
      h("div", { class: "dp-side" },
        h("div", { class: "card dp-chart-card" }, h("div", { class: "dp-card-head" }, h("span", { class: "eyebrow" }, "Trajectory"),
          h("span", { class: "muted small" }, "实线 = 测得的抓取点，点 = 模型给出的目标；点击跳转")), h("div", { id: "dpChart" })),
        h("div", { class: "card dp-calls-card" }, h("div", { class: "dp-card-head" }, h("span", { class: "eyebrow" }, "Inferences"),
          h("span", { class: "muted small", id: "dpTotals" })), h("div", { class: "dp-calls", id: "dpCalls" })),
        h("div", { class: "card dp-prompt-card" }, h("div", { class: "dp-card-head" }, h("span", { class: "eyebrow" }, "Prompt"),
          h("span", { class: "muted small" }, "每次推理相同的系统提示词，按块组装")), h("div", { id: "dpPrompt" }))));
    put($("rvBody"), root);
    document.addEventListener("keydown", directKeys);
  }
  drawStatic();
  drawDirect();
}

function directKeys(e) {
  if (!DP.tr || !document.querySelector(".dp") || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.key === " ") { e.preventDefault(); DP.playing ? pause() : play(); }
  else if (e.key === "ArrowLeft") jumpCall(-1);
  else if (e.key === "ArrowRight") jumpCall(1);
}

// -- parts that change only when the trace reloads ---------------------------

function drawStatic() {
  const { tr } = DP;
  const p = tr.policy;
  const field = (k, v) => h("div", { class: "dp-field" }, h("span", { class: "eyebrow" }, k), h("b", {}, v || "—"));
  const model = p.model || {};
  put($("dpSel"), field("Policy type", "Direct Policy"), field("Representation", p.representation_label),
    field("Model", `${model.model || "—"}${model.reasoning_effort ? ` · ${model.reasoning_effort}` : ""}`),
    field("Horizon", p.horizon ? `${p.horizon[0]}–${p.horizon[1]} rows` : null),
    field("History", p.history_length ? `${p.history_length} steps · stride ${p.history_stride}` : null),
    field("Control", p.control_hz ? `${p.control_hz} Hz` : null), runSwitcher());
  $("dpInstruction").textContent = tr.instruction || "";
  const ok = tr.ending?.task_success;
  const result = $("dpResult");
  result.textContent = tr.live ? "Running" : ok === true ? "Success" : ok === false ? "Failed" : "—";
  result.className = `dp-result ${tr.live ? "live" : ok === true ? "success" : ok === false ? "failed" : ""}`;
  // Cameras: one labelled image per camera, side by side.
  const cams = p.cameras || Object.keys(tr.frames[0]?.images || {});
  if ($("dpCams").childElementCount !== cams.length) {
    put($("dpCams"), cams.map((name, i) => h("figure", { class: "dp-cam" },
      h("figcaption", {}, h("b", { class: "mono" }, pad2(i + 1)), ` / ${name.replace(/_/g, " ").toUpperCase()}`),
      h("div", { class: "dp-frame" }, h("img", { "data-cam": name, alt: name }),
        h("div", { class: "dp-overlay", hidden: true })))));
  }
  // Track: one segment per phase; inference waits hatched when shown.
  put($("dpSegments"), DP.phases.map((ph) => h("span", { "data-call": ph.call, "data-kind": ph.kind,
    class: [tr.calls[ph.call].safe_hold ? "hold" : "", (tr.calls[ph.call].output_attempts || 1) > 1 ? "repaired" : ""].join(" "),
    style: `flex-grow:${Math.max(ph.dur, 1e-3)}`, title: `推理 ${ph.call + 1}${ph.kind === "wait" ? " · 等待模型" : ` · ${ph.steps.length} 步`}` })));
  const t = tr.totals;
  $("dpTotals").textContent = `${t.calls} 次 · ${t.steps} 步 · API ${secs(t.api_s)} · ${fmtTokens(t.total_tokens)} tokens`
    + (t.repairs ? ` · 修复 ${t.repairs}` : "") + (t.safe_holds ? ` · 保持 ${t.safe_holds}` : "");
  put($("dpCalls"), tr.calls.map((c, i) => h("button", { class: "dp-call", "data-call": i, onclick: () => { pause(); DP.follow = false; DP.pos = posOfCall(i) + (DP.wait ? 0 : 1e-6); drawDirect(); } },
    h("span", { class: "mono dp-call-n" }, pad2(c.index)),
    h("span", { class: "dp-call-main" },
      h("span", { class: "dp-call-line" }, h("b", { class: "mono" }, `${c.start_step}→${c.end_step}`), ` · ${c.horizon} 步 · ${secs(c.api_latency_s)} · ${fmtTokens(c.usage.total_tokens)}`,
        c.safe_hold ? h("span", { class: "tag bad" }, "保持") : null,
        (c.output_attempts || 1) > 1 ? h("span", { class: "tag warn" }, `修复×${c.output_attempts - 1}`) : null,
        c.safety_clamped ? h("span", { class: "tag" }, "限幅") : null),
      h("span", { class: "dp-call-note" }, c.note || c.error || "")),
    h("span", { class: "dp-call-open", title: "查看这次推理的完整请求与回复", onclick: (e) => { e.stopPropagation(); openInference(i); } }, "详情"))));
  put($("dpPrompt"), Object.entries(tr.prompt_blocks || {}).map(([name, text]) =>
    h("details", { class: "sys" }, h("summary", {}, `${PROMPT_BLOCKS[name] || name} · ${fmtInt(text.length)} 字`), h("pre", { class: "txt" }, text))),
    h("button", { class: "btn small ghost", onclick: () => openFile("direct/prompt.md") }, "完整提示词"),
    h("button", { class: "btn small ghost", onclick: () => openFile("direct/policy.json") }, "策略配置"));
  drawChart();
}

// Other Direct runs, newest first: step through rollouts like a gallery.
function runSwitcher() {
  const runs = (S.runs || []).filter((r) => r.id && r.method_id === "direct_policy");
  if (runs.length < 2) return null;
  const index = runs.findIndex((r) => r.key === RV.run.key);
  const go = (i) => { location.hash = `#${runs[(i + runs.length) % runs.length].key}`; };
  return h("div", { class: "dp-field dp-switch" }, h("span", { class: "eyebrow" }, `Rollout ${index + 1} / ${runs.length}`),
    h("div", { class: "row" },
      h("button", { class: "btn small", title: "上一个 Direct 运行", onclick: () => go(index - 1) }, "←"),
      h("select", { onchange: (e) => go(Number(e.target.value)) }, runs.map((r, i) => h("option", { value: i, selected: i === index },
        `#${r.short_id} · ${taskName(r.task) || r.task} · ${r.model || ""} · ${RUN_STATUS[r.status] || r.status}`))),
      h("button", { class: "btn small", title: "下一个 Direct 运行", onclick: () => go(index + 1) }, "→")));
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
    if (s.kind === "wait") put(overlay, h("span", { class: "eyebrow" }, `Inference ${pad2(s.call + 1)} · model thinking`),
      h("b", { class: "mono" }, `${s.into.toFixed(1)} s / ${s.dur.toFixed(1)} s`));
  }
  // Track
  $("dpSeek").value = DP.total ? Math.round((DP.pos / DP.total) * 1000) : 0;
  [...$("dpSegments").children].forEach((el) => {
    const c = Number(el.dataset.call);
    el.classList.toggle("active", c === s.call);
    el.classList.toggle("past", c < s.call);
  });
  const hz = tr.policy.control_hz || 20;
  const lastStep = tr.frames.length ? tr.frames[tr.frames.length - 1].step : 0;
  $("dpTime").textContent = DP.wait ? `${simClock(DP.pos)} / ${simClock(DP.total)}` : `${simClock(s.step / hz)} / ${simClock(lastStep / hz)}`;
  $("dpTime").title = DP.wait ? "含推理等待的真实时间" : "仿真时间（不含推理等待）";
  $("dpPrev").disabled = s.call <= 0;
  $("dpNext").disabled = s.call >= tr.calls.length - 1;
  const metric = (k, v) => h("div", { class: "dp-metric" }, h("span", { class: "eyebrow" }, k), h("strong", { class: "mono" }, v));
  put($("dpTelemetry"),
    metric("Call", call ? `${pad2(s.call + 1)} / ${tr.calls.length}` : "—"),
    metric("Control step", `${s.step}${tr.max_steps ? ` / ${tr.max_steps}` : ""}`),
    metric("Action", call && s.row >= 0 ? `${pad2(s.row + 1)} / ${call.horizon}` : s.kind === "wait" ? "thinking" : "—"),
    metric("Wall clock", call ? `+${fmtClock((call.requested_at || tr.t0) - tr.t0)}` : "—"));
  // Alerts
  const alerts = [];
  if (call?.safety_clamped) alerts.push("Safety clamp applied");
  if ((call?.output_attempts || 1) > 1) alerts.push(`Output repaired · ${call.output_attempts} attempts`);
  if (call?.safe_hold) alerts.push(`Safe hold · ${call.error || "no valid chunk"}`);
  if (call?.execution_error) alerts.push(`Execution error · ${call.execution_error}`);
  $("dpAlert").hidden = !alerts.length;
  $("dpAlert").textContent = alerts.join(" · ");
  $("dpNote").textContent = call?.note || (call?.error ? `（无合法输出）${call.error}` : "");
  drawOutput(call, s);
  drawChartCursor(s.step);
  document.querySelectorAll(".dp-call").forEach((el) => el.classList.toggle("active", Number(el.dataset.call) === s.call));
}

function drawOutput(call, s) {
  const out = $("dpOutput");
  if (!call) { put(out); return; }
  const p = DP.tr.policy;
  const eef = (p.columns || [])[0] === "x" || p.representation === "delta_eef";
  const key = `${call.index}`;
  if (out.dataset.call !== key) {
    out.dataset.call = key;
    const executed = new Map(call.executed.map((e) => [e.row, e]));
    const head = ["Action", "Step", "x", "y", "z", "qw", "qx", "qy", "qz", "Gripper", "Measured xyz", "Error"];
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
        h("button", { class: "dp-toggle-chunk", "aria-expanded": String(DP.chunkOpen), onclick: () => { DP.chunkOpen = !DP.chunkOpen; out.dataset.call = ""; drawDirect(); } }, "ACTION CHUNK"),
        h("code", {}, call.tool || p.tool),
        h("span", {}, `${call.horizon} steps · controls ${call.start_step + 1}–${call.end_step}`),
        h("span", {}, `Call latency ${num(call.api_latency_s, 2)} s`),
        h("span", {}, `${fmtTokens(call.usage.input_tokens)} in · ${fmtTokens(call.usage.output_tokens)} out`),
        h("span", { class: "muted" }, eef ? "xyz: m · quaternion wxyz · gripper 0 open–1 closed" : ""),
        h("span", { class: "spacer" }),
        h("button", { class: "btn small ghost", onclick: () => openInference(call.index - 1) }, "完整请求与回复")),
      DP.chunkOpen ? h("div", { class: "dp-table" }, h("table", {}, h("thead", {}, h("tr", {}, head.map((x) => h("th", {}, x)))), h("tbody", {}, rows))) : null,
      DP.chunkOpen && p.representation !== "abs_eef" ? h("p", { class: "small muted" }, "表中为解码后的绝对目标；模型原始输出见“完整请求与回复”。") : null);
  }
  out.querySelectorAll("tbody tr").forEach((tr) => tr.classList.toggle("current", Number(tr.dataset.row) === s.row));
}

// -- trajectory chart --------------------------------------------------------

const CHART = { w: 360, padL: 30, padR: 6, padT: 6, padB: 16 };

function drawChart() {
  const { tr } = DP;
  const frames = tr.frames;
  if (frames.length < 2) { put($("dpChart"), h("div", { class: "muted small" }, "等待更多控制步…")); return; }
  // Scale to the steps actually taken; the budget is shown in the header.
  const maxStep = Math.max(10, frames[frames.length - 1].step);
  const { w, padL, padR, padT, padB } = CHART;
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
  add("text", { x: 4, y: gTop + 16, class: "lbl" }, "grip");
  add("polyline", { points: frames.map((f) => `${X(f.step).toFixed(1)},${(gTop + 26 - 22 * f.proprio.gripper_closure).toFixed(1)}`).join(" "), class: "meas grip" });
  for (const x of targets) add("circle", { cx: X(x.step).toFixed(1), cy: (gTop + 26 - 22 * x.t[7]).toFixed(1), r: 1.4, class: "tgt grip" });
  for (const c of tr.calls) add("line", { x1: X(c.start_step), x2: X(c.start_step), y1: padT, y2: height - padB, class: "callmark" });
  add("text", { x: padL, y: height - 4, class: "lbl faint" }, "0");
  add("text", { x: w - padR, y: height - 4, class: "lbl faint", "text-anchor": "end" }, `${maxStep} steps`);
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
  const x = CHART.padL + ((CHART.w - CHART.padL - CHART.padR) * step) / maxStep;
  cursor.setAttribute("x1", x); cursor.setAttribute("x2", x);
}

// -- playback ----------------------------------------------------------------

function play() {
  if (DP.pos >= DP.total - 1e-6) DP.pos = 0;
  DP.playing = true; DP.follow = false; DP.last = performance.now();
  $("dpPlay").textContent = "Ⅱ Pause";
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
  if (b) b.textContent = "▶ Play";
}

function replayCall() {
  const s = stateAt(DP.pos);
  if (s.call < 0) return;
  const parts = DP.phases.filter((p) => p.call === s.call);
  pause();
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
  const step = stateAt(DP.pos).step;
  DP.wait = on;
  Object.assign(DP, directPhases(DP.tr, on));
  DP.pos = posOfStep(step);
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
        ...kv("模型耗时（含修复）", secs(call.api_latency_s)), ...kv("执行动作块", secs(call.execution_s)),
        ...kv("观测的控制步", (call.observed_steps || []).join(", ")), ...kv("输出尝试", String(call.output_attempts)),
        ...kv("输入 tokens", fmtInt(call.usage.input_tokens)), ...kv("输出 tokens", fmtInt(call.usage.output_tokens)),
        ...kv("缓存命中", fmtInt(call.usage.cached_input_tokens)), ...kv("触发方式", "固定代码：上一动作块执行完或回合开始后立即发起")),
      (d.attempts || []).map((a) => h("div", { class: `item ${a.error ? "bad" : ""}` },
        h("div", { class: "item-head" }, h("b", {}, `第 ${a.attempt} 次尝试`), h("span", { class: "faint small" },
          `${secs(a.latency_s)} · ${a.finish_reason} · ${fmtTokens(a.usage?.total_tokens)} tokens`)),
        a.error ? h("p", { class: "small bad-text" }, `被拒绝：${a.error}`) : null,
        a.reasoning ? h("details", { class: "sys" }, h("summary", {}, `模型推理（${fmtInt(a.reasoning.length)} 字）`), h("pre", { class: "txt" }, a.reasoning)) : null,
        a.content ? h("pre", { class: "txt" }, a.content) : null,
        (a.tool_calls || []).length ? h("pre", { class: "txt" }, pretty(a.tool_calls)) : null))]],
    input: ["模型看到的输入", () => [
      h("p", { class: "small muted" }, "每次推理是独立请求：系统提示词 + 目标 + 最近几帧观测（图像与本体状态），不包含之前的回复。"),
      d.messages.map((m) => h("div", { class: "item" }, h("div", { class: "item-head" }, h("b", {}, m.role)),
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
