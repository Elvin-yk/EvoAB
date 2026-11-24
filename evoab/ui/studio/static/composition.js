// EvoAB Studio: methods from the library, their parameters, and recipe graphs.
// Loaded before studio.js; uses its h()/put()/api() helpers at call time.
"use strict";

const PARADIGM_LABEL = { orchestrate: "编排", direct: "直接控制", code: "代码", policy: "策略模型" };
const CHANNEL_LABEL = { tools: "工具调用", code: "代码", action: "直接动作" };
const STATUS_LABEL = { builtin: "内置", reproduction: "复现", experimental: "实验" };
const KIND_LABEL = { brain: "大脑", role: "角色", channel: "通道", context: "上下文", capability: "能力",
  body: "环境", loop: "回合循环" };
const METHOD_GROUPS = [
  ["single", "单一范式", "一个智能体，用一种方式控制机器人。"],
  ["composed", "组合方案", "多个角色或多种范式拼在一起：例如规划者调用直接控制器，或跨回合积累程序。"],
];

function methodGroup(method) {
  return method.roles.length > 1 || method.paradigms.length > 1 || (method.episodes?.repeat || 1) > 1
    ? "composed" : "single";
}

// -- recipe graph ---------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) if (value != null) node.setAttribute(key, value);
  for (const kid of kids.flat()) if (kid != null) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}

// Layout: one band per role; columns brain/context → role → channel → capabilities → environment.
function layoutGraph(graph) {
  const W = 168, H = 46, GAP_X = 60, GAP_Y = 14, BAND_GAP = 26;
  const col = { brain: 0, context: 0, role: 1, channel: 2, capability: 3, body: 4, loop: 1 };
  const roles = graph.nodes.filter((n) => n.kind === "role");
  const pos = new Map();
  let y = 10;
  const loop = graph.nodes.find((n) => n.kind === "loop");
  if (loop) { pos.set(loop.id, { x: 10 + col.loop * (W + GAP_X), y }); y += H + BAND_GAP; }
  for (const role of roles) {
    const name = role.id.slice(5);
    const mine = (kind) => graph.nodes.filter((n) => n.kind === kind && n.id.startsWith(`${name}:`));
    const caps = mine("capability");
    const rows = Math.max(2, caps.length, 1);
    const bandH = rows * H + (rows - 1) * GAP_Y;
    const center = y + bandH / 2 - H / 2;
    const place = (node, column, top) => pos.set(node.id, { x: 10 + column * (W + GAP_X), y: top });
    const [brain] = mine("brain"); const [context] = mine("context"); const [channel] = mine("channel");
    if (brain) place(brain, col.brain, center - (H + GAP_Y) / 2);
    if (context) place(context, col.context, center + (H + GAP_Y) / 2);
    place(role, col.role, center);
    if (channel) place(channel, col.channel, center);
    caps.forEach((cap, i) => place(cap, col.capability, y + i * (H + GAP_Y)));
    y += bandH + BAND_GAP;
  }
  const body = graph.nodes.find((n) => n.kind === "body");
  const top = loop ? H + BAND_GAP + 10 : 10;
  if (body) pos.set(body.id, { x: 10 + col.body * (W + GAP_X), y: Math.max(top, (top + y - BAND_GAP) / 2 - H / 2) });
  return { pos, W, H, width: 10 + 5 * W + 4 * GAP_X + 10, height: y - BAND_GAP + 10 };
}

function recipeGraphView(graph, { calls = false } = {}) {
  const { pos, W, H, width, height } = layoutGraph(graph);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "recipe-graph", role: "img",
    "aria-label": `方案 ${graph.recipe} 的组件图` });
  const defs = svg("defs", {}, svg("marker", { id: "arrow", viewBox: "0 0 8 8", refX: "7", refY: "4",
    markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" }, svg("path", { d: "M0,0 L8,4 L0,8 z", class: "edge-head" })));
  root.append(defs);
  for (const edge of graph.edges) {
    const a = pos.get(edge.from), b = pos.get(edge.to);
    if (!a || !b) continue;
    const forward = b.x > a.x;
    const x1 = forward ? a.x + W : a.x, y1 = a.y + H / 2;
    const x2 = forward ? b.x : (b.x > a.x ? b.x : b.x + W), y2 = b.y + H / 2;
    const bend = Math.max(30, Math.abs(x2 - x1) / 2);
    const d = edge.kind === "delegates"
      ? `M${a.x + W / 2},${a.y + H} C${a.x + W / 2},${b.y - 30} ${b.x + W / 2},${a.y + H + 30} ${b.x + W / 2},${b.y}`
      : `M${x1},${y1} C${x1 + (forward ? bend : -bend)},${y1} ${x2 - (forward ? bend : -bend)},${y2} ${x2},${y2}`;
    root.append(svg("path", { d, class: `edge edge-${edge.kind.replace(/\s+/g, "-")}`, "marker-end": "url(#arrow)" },
      svg("title", {}, edge.kind)));
  }
  for (const node of graph.nodes) {
    const p = pos.get(node.id);
    if (!p) continue;
    const counted = node.kind === "capability" || (node.kind === "role" && node.calls != null);
    const used = calls && counted ? node.calls || 0 : null;
    const group = svg("g", { class: `node node-${node.kind}${node.entry ? " entry" : ""}${used === 0 ? " idle" : ""}`,
      transform: `translate(${p.x},${p.y})` });
    group.append(svg("rect", { width: W, height: H, rx: 9 }));
    const label = node.kind === "channel" ? (CHANNEL_LABEL[node.label] || node.label) : node.label;
    group.append(svg("text", { x: 10, y: 18, class: "node-kind" }, KIND_LABEL[node.kind] || node.kind));
    group.append(svg("text", { x: 10, y: 35, class: "node-label" }, label.length > 22 ? `${label.slice(0, 21)}…` : label));
    if (used != null) group.append(svg("text", { x: W - 10, y: 18, class: "node-calls", "text-anchor": "end" }, `${used} 次`));
    group.append(svg("title", {}, [label, node.detail].filter(Boolean).join("\n")));
    root.append(group);
  }
  return h("div", { class: "graph-wrap" }, root);
}

// -- method step ---------------------------------------------------------------------

const METHODS_BY_INTEGRATION = new Map();
async function loadMethods(integration) {
  if (!METHODS_BY_INTEGRATION.has(integration)) {
    METHODS_BY_INTEGRATION.set(integration, await api(`/api/methods?integration=${encodeURIComponent(integration)}`));
  }
  return METHODS_BY_INTEGRATION.get(integration);
}

function methodCard(method, selected, onSelect) {
  const disabled = !method.agents.length;
  const reason = method.unavailable || "当前没有能驱动该通道的 Agent 实现。";
  return h("button", {
    class: `choice${selected ? " selected" : ""}${disabled ? " disabled" : ""}`, "aria-pressed": String(selected),
    "aria-disabled": String(disabled), onclick: disabled ? null : onSelect, title: disabled ? reason : null,
  },
  h("span", { class: "title" }, method.label,
    h("span", { class: "tag" }, STATUS_LABEL[method.status] || method.status)),
  h("span", { class: "desc" }, method.summary),
  h("span", { class: "method-tags" },
    method.paradigms.map((p) => h("span", { class: `tag paradigm-${p}` }, PARADIGM_LABEL[p] || p)),
    h("span", { class: "tag" }, CHANNEL_LABEL[method.channel] || method.channel),
    method.roles.length > 1 ? h("span", { class: "tag" }, `${method.roles.length} 个角色`) : null),
  disabled ? h("span", { class: "muted small" }, reason) : null);
}

function parameterField(parameter, values) {
  const value = parameter.name in values ? values[parameter.name] : parameter.default;
  const set = (v) => {
    if (v === "" || v === parameter.default || String(v) === String(parameter.default)) delete values[parameter.name];
    else values[parameter.name] = v;
  };
  let control;
  if (parameter.choices) {
    control = h("div", { class: "segmented" }, parameter.choices.map((choice) => h("button", {
      class: String(value) === String(choice) ? "active" : "", "aria-pressed": String(String(value) === String(choice)),
      onclick: () => { set(choice); route(); } }, String(choice))));
  } else if (parameter.type === "text") {
    control = h("textarea", { class: "input", rows: 3, maxlength: 8192, oninput: (e) => set(e.target.value) }, value || "");
  } else if (parameter.type === "boolean") {
    control = h("input", { type: "checkbox", checked: !!value, onchange: (e) => set(e.target.checked) });
  } else {
    control = h("input", { class: "input", value: value ?? "", inputmode: parameter.type === "integer" ? "numeric" : null,
      placeholder: parameter.type === "pair" ? "A:B" : null, oninput: (e) => set(e.target.value) });
  }
  return h("label", { class: "field" }, h("span", { class: "section-title" }, parameter.label), control,
    parameter.help ? h("span", { class: "muted small" }, parameter.help) : null);
}

function methodDetails(method, w) {
  const values = (w.params[method.id] ||= {});
  const agents = method.agents.length > 1 ? h("section", { class: "agent-picker", "aria-label": "选择 Agent 实现" },
    h("h3", { class: "section-title" }, "入口角色的 Agent 运行时"),
    h("div", { class: "choice-grid" }, method.agents.map((agent) => h("button", {
      class: `choice${w.agent === agent.id ? " selected" : ""}`, "aria-pressed": String(w.agent === agent.id),
      onclick: () => { w.agent = agent.id; route(); },
    }, h("span", { class: "title" }, agent.label), h("span", { class: "desc" }, agent.description))))) : null;
  const references = method.references?.length ? h("p", { class: "muted small method-refs" }, "参考：",
    method.references.map((ref, i) => [i ? "；" : "", h("a", { href: ref.url, target: "_blank", rel: "noopener noreferrer" }, ref.title)])) : null;
  return h("section", { class: "method-group" },
    h("h2", { class: "section-title" }, `${method.label} 的组成`),
    recipeGraphView(method.graph),
    references,
    agents,
    method.parameters.length ? h("div", { class: "card direct-options" }, method.parameters.map((p) => parameterField(p, values))) : null);
}

async function stepMethodLibrary(body) {
  const w = S.wizard;
  const integration = w.bench?.integration;
  body.append(h("h1", { class: "wizard-title" }, "选择方法"));
  if (!integration) return;
  const holder = h("div", {});
  body.append(holder);
  let methods = METHODS_BY_INTEGRATION.get(integration);
  if (!methods) {
    put(holder, h("div", { class: "muted" }, "读取方法库…"));
    try { methods = await loadMethods(integration); } catch (error) {
      put(holder, h("div", { class: "notice error" }, `读取失败：${error.message}`));
      return;
    }
    if (holder.isConnected) route();
    return;
  }
  const selected = methods.find((m) => m.id === w.method);
  const sections = METHOD_GROUPS.map(([key, label, note]) => {
    const items = methods.filter((m) => methodGroup(m) === key);
    if (!items.length) return null;
    return h("section", { class: "method-group" }, h("h2", { class: "section-title" }, label),
      h("p", { class: "muted small", style: "margin:0 0 10px" }, note),
      h("div", { class: "choice-grid" }, items.map((m) => methodCard(m, m.id === w.method, () => {
        selectMethod(m, m.id === w.method ? w.agent : null); w.touched = true; route();
      }))));
  });
  put(holder, sections, selected && selected.agents.length ? methodDetails(selected, w) : null);
}

// -- run detail graph -----------------------------------------------------------------

async function renderRunGraph(run) {
  const holder = document.getElementById("rvGraph");
  if (!holder || !run.id || holder.dataset.run === run.id) return;
  holder.dataset.run = run.id;
  let graph;
  try { graph = await api(`/api/trace/${encodeURIComponent(run.id)}/graph`); } catch { put(holder); return; }
  const overrides = Object.entries(graph.overrides || {});
  put(holder, h("details", { class: "card", style: "margin-top:14px" },
    h("summary", { class: "card-pad", style: "cursor:pointer;font-weight:600" }, "方案组成与调用次数"),
    h("div", { style: "padding:0 16px 16px" },
      recipeGraphView(graph, { calls: true }),
      overrides.length ? h("p", { class: "muted small" }, "本次覆盖：", overrides.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("  ")) : null)));
}
