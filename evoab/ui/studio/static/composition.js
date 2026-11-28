// EvoAB Studio: methods from the library, their parameters, and recipe graphs.
// Loaded before studio.js; uses its h()/put()/api() helpers at call time.
"use strict";

const CHANNEL_LABEL = { tools: "工具调用", code: "代码", action: "直接动作" };
const KIND_LABEL = { brain: "大脑", role: "角色", channel: "通道", context: "上下文", capability: "能力",
  body: "环境", loop: "回合循环" };
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
    const used = calls && counted ? node.calls : null;
    const group = svg("g", { class: `node node-${node.kind}${node.entry ? " entry" : ""}${used === 0 ? " idle" : ""}`,
      transform: `translate(${p.x},${p.y})` });
    group.append(svg("rect", { width: W, height: H, rx: 9 }));
    const label = node.kind === "channel" ? (CHANNEL_LABEL[node.label] || node.label) : node.label;
    group.append(svg("text", { x: 10, y: 18, class: "node-kind" }, node.kindLabel || KIND_LABEL[node.kind] || node.kind));
    group.append(svg("text", { x: 10, y: 35, class: "node-label" }, label.length > 22 ? `${label.slice(0, 21)}…` : label));
    if (calls && counted && used == null) group.append(svg("text", { x: W - 10, y: 18, class: "node-calls", "text-anchor": "end" }, "无法归属"));
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

const TEAM_METHOD = "tool_team";
const DIRECT_TOOL = "actor_act";
const TEAM_ROLE = { planner: "Planner（规划）", actor: "Actor（执行）" };
const teamCatalogs = new Map();

function teamPresets(role, groups, entryTask = false) {
  const available = Object.values(groups).flat();
  const ordinary = (names) => names.filter((name) => name !== DIRECT_TOOL);
  const presets = role === "planner" ? {
    "常用工具": available.filter((name) => !groups.gripper?.includes(name) && !groups.action_policy?.includes(name)),
    "观察与规划": [...(groups.skills || []), ...(groups.scene || []), "finish"],
  } : {
    "常用工具": ordinary([...(groups.scene || []), ...(groups.gripper || []), ...(entryTask ? groups.episode || [] : groups.jobs)]),
    "基础执行": ["capture_scene", "move", "grasp", "release", "hold"],
    "Direct 执行": ["capture_scene", DIRECT_TOOL],
  };
  return Object.fromEntries(Object.entries(presets)
    .filter(([, names]) => !names.includes(DIRECT_TOOL) || available.includes(DIRECT_TOOL))
    .map(([label, names]) => [label, names.filter((name) => available.includes(name))]));
}

function newAgentCard(role, groups, entryTask = false) {
  return { role, delegate_to: [], instructions: "", tools: teamPresets(role, groups, entryTask)["常用工具"] };
}

function entryTaskPrompt(w) {
  const first = w.team?.[0];
  return w.bench?.entry_prompt_as_task && !selectedMethod()?.external_evaluator && first?.instructions.trim()
    ? first.instructions : null;
}

function configuredTeam(w) {
  return w.team.map((card, i) => i === 0 && w.model ? {...card, model_preset: w.model} : card);
}

function addActor(team, caller, groups) {
  team.push(newAgentCard("actor", groups));
  caller.delegate_to.push(team.length);
  route();
}

function reachableAgents(team, start) {
  const reachable = new Set([start]);
  for (const number of reachable) {
    for (const target of team[number - 1].delegate_to) reachable.add(target);
  }
  return reachable;
}

function collaborationFields(team, number) {
  function choices(outgoing) {
    const title = outgoing ? "可委托给" : "接受谁委托（向其汇报）";
    const peers = team.flatMap((peer, i) => {
      const source = outgoing ? number : i + 1, target = outgoing ? i + 1 : number;
      if (target === 1 || source === target) return [];
      const caller = team[source - 1];
      const cycle = reachableAgents(team, target).has(source);
      return [h("label", {class: "check", title: cycle ? "这条委托会形成循环" : null}, h("input", {type: "checkbox", disabled: cycle,
        checked: caller.delegate_to.includes(target), onchange: (e) => {
          caller.delegate_to = e.target.checked ? [...caller.delegate_to, target] : caller.delegate_to.filter((n) => n !== target);
          route();
        }}), `Agent ${i + 1} · ${TEAM_ROLE[peer.role].split("（")[0]}`)];
    });
    return h("fieldset", {}, h("legend", {}, title), peers.length ? peers :
      h("small", {class: "muted"}, outgoing ? "暂无可委托的其他 Agent" : "入口 Agent 提交整个任务的结果"));
  }
  return h("div", {class: "team-relations"}, choices(true), choices(false));
}

function roleCard(card, index, groups, team, updateGraph) {
  const entryTask = index === 0 && S.wizard.bench.entry_prompt_as_task;
  const presets = teamPresets(card.role, groups, entryTask);
  const select = h("select", { class: "input", "aria-label": "角色", onchange: (e) => {
    card.role = e.target.value;
    card.tools = teamPresets(card.role, groups, entryTask)["常用工具"];
    route();
  } }, Object.entries(TEAM_ROLE).map(([value, label]) => h("option", { value, selected: value === card.role }, label)));
  const toolGroup = (key, names) => h("fieldset", {disabled: card.role === "planner" && key === "gripper"}, h("legend", {}, TOOL_GROUP_LABELS[key] || key),
    key === "gripper" && card.role === "planner" ? h("small", { class: "muted" }, "机械臂由 Actor 执行") : null,
    key === "action_policy" ? h("small", { class: "muted" }, names.length ? "通过 actor_act 把子目标交给 Direct，Direct 根据画面生成动作。" : "当前评测集暂无可用的动作策略。") : null,
    key === "jobs" ? h("small", { class: "muted" }, "勾选查询结果或等待工具后，动作可在后台执行。") : null,
    names.map((name) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: card.tools.includes(name),
      onchange: (e) => {
        card.tools = e.target.checked ? [...card.tools, name] : card.tools.filter((n) => n !== name);
        updateGraph();
      } }), h("span", {}, TOOL_LABELS[name] || name, h("code", {}, name)))),
    key === "action_policy" ? h("small", { class: "muted" }, "VLA · 暂未接入") : null);
  return h("section", { class: "card card-pad team-card", "aria-label": `Agent ${index + 1}` },
    h("div", { class: "toolbar" }, h("h2", { class: "section-title" }, `Agent ${index + 1}`), select,
      h("span", { class: "spacer" }), index ? h("button", { class: "btn small", onclick: () => {
        team.splice(index, 1);
        for (const peer of team) peer.delegate_to = peer.delegate_to.filter((n) => n !== index + 1)
          .map((n) => n > index + 1 ? n - 1 : n);
        route();
      } }, "移除") : null),
    collaborationFields(team, index + 1),
    !reachableAgents(team, 1).has(index + 1) ? h("small", {class: "notice error"}, "尚未接入任务，请从入口直接或间接委托给此 Agent。") : null,
    card.role === "planner" && !card.delegate_to.length ? h("div", {class: "toolbar"},
      h("small", {class: "muted"}, "需要其他 Agent 配合时，可添加执行 Actor。"),
      h("button", {class: "btn small", onclick: () => addActor(team, card, groups)}, "添加执行 Actor")) : null,
    h("label", { class: "field" }, entryTask ? `任务描述（${TEAM_ROLE[card.role].split("（")[0]}）` : "角色指令（可选）", h("textarea", { class: "input", rows: 2, maxlength: 8192,
      placeholder: entryTask ? "输入本轮任务，留空使用评测集指令。" : index ? "例如：根据腕部画面调整抓取位置。" : "例如：每次移动前先观察目标。",
      oninput: (e) => { card.instructions = e.target.value; updateGraph(); } }, card.instructions)),
    h("div", { class: "toolbar" }, h("b", {}, card.role === "planner" ? "Planner 工具预设" : "Actor 工具预设"),
      Object.entries(presets).map(([label, tools]) => h("button", { class: "btn small", onclick: () => {
        card.tools = tools; route();
      } }, label))),
    h("div", { class: "tool-picker-grid" }, Object.entries(groups).map(([key, names]) => toolGroup(key, names))));
}

// Preserve the policy view for older runs that generated a separate action role.
function showActionPolicies(graph) {
  for (const node of graph.nodes) {
    if (node.kind === "capability" && node.label === "hybrid" && node.binding) {
      node.kindLabel = "动作策略";
      node.label = node.binding === "robodojo_direct_eef" ? "Direct · actor_act" : "actor_act";
    }
  }
  const actions = graph.nodes.filter((n) => n.kind === "channel" && n.label === "action");
  for (const channel of actions) {
    const role = channel.id.slice(0, -":channel".length);
    const target = graph.nodes.find((n) => n.id === `role:${role}`);
    const callers = graph.edges.filter((e) => e.kind === "delegates" && e.to === target.id);
    // Keep standalone and shared action roles as independent nodes.
    if (target.entry || callers.length !== 1) continue;
    const model = graph.nodes.find((n) => n.id === `${role}:brain`)?.detail;
    const kept = graph.nodes.filter((n) => n !== target && !n.id.startsWith(`${role}:`));
    const ids = new Set(kept.map((n) => n.id));
    graph.nodes = kept;
    graph.edges = graph.edges.filter((e) => ids.has(e.from) && ids.has(e.to));
    const {from} = callers[0];
    const id = from.replace(/:channel$/, `:action:${role}`);
    graph.nodes.push({ id, kind: "capability", kindLabel: "动作策略", label: "Direct", calls: target.calls,
      detail: model || "沿用运行的模型预设" });
    graph.edges.push({ from, to: id, kind: "calls" }, { from: id, to: "body", kind: "action chunks" },
      { from: "body", to: id, kind: "observations" });
  }
}

function studioHarnessGraph(graph, view) {
  showActionPolicies(graph);
  const roleLabels = new Map();
  const agents = graph.nodes.filter((node) => node.kind === "role");
  agents.forEach((node, i) => roleLabels.set(node.id, `Agent ${i + 1} · ${TEAM_ROLE[view.team[i].role].split("（")[0]}`));
  for (const n of graph.nodes) {
    if (n.kind === "role") n.label = roleLabels.get(n.id);
    if (n.kind === "brain") {
      n.kindLabel = "运行时";
      n.label = view.runtime;
    }
    if (n.kind === "context" && n.label === "identity") n.label = "会话历史";
    if (n.kind === "context" && n.label === "stateless_window") n.label = "观测窗口";
    if (n.kind === "body") n.label = view.benchmark;
    if (n.kind === "capability" && !n.kindLabel) n.label = `${TOOL_GROUP_LABELS[n.label] || n.label} · ${n.tools?.length ?? 0}`;
  }
  return recipeGraphView(graph);
}

function harnessPreview(w, runtime) {
  const assembly = h("div", {});
  const root = h("section", { class: "card card-pad harness-panel", "aria-label": "当前 Harness 架构" },
    h("h2", { class: "section-title" }, "当前 Harness 架构"),
    h("p", { class: "muted small" }, w.model ? `入口 Agent 模型 · ${w.model}` : "模型预设在下一步选择"), assembly);
  let timer, revision = 0;
  function update() {
    const current = ++revision;
    clearTimeout(timer);
    const view = { team: w.team, runtime, benchmark: benchmarkName(w.bench, w.bench.profile) };
    const reachable = reachableAgents(w.team, 1);
    const missing = w.team.flatMap((_, i) => reachable.has(i + 1) ? [] : [i + 1]);
    if (missing.length) {
      put(assembly, h("p", {class: "notice"}, `${missing.map((n) => "Agent " + n).join("、")} 还没有从 Agent 1 通往它的委托路径。请勾选委托关系后继续。`));
      assembly.removeAttribute("aria-busy");
      return;
    }
    assembly.setAttribute("aria-busy", "true");
    timer = setTimeout(async () => {
      if (!root.isConnected) return;
      try {
        const graph = await api(`/api/benchmarks/${encodeURIComponent(w.bench.integration)}/team-preview?graph=true`, {
          method: "POST", json: { method: w.method, agent: w.agent, team: configuredTeam(w) },
        });
        if (current !== revision || !root.isConnected) return;
        put(assembly, studioHarnessGraph(graph, view),
          h("p", { class: "muted small" }, "虚线从委托方指向执行方，结果沿原路返回。工具组旁是勾选数量，悬停查看具体工具。"));
      } catch (error) {
        if (current === revision) put(assembly, h("p", { class: "notice error" }, error.message));
      } finally {
        if (current === revision) assembly.removeAttribute("aria-busy");
      }
    }, 120);
  }
  return { root, update };
}

async function stepMethod(body) {
  const w = S.wizard;
  const integration = w.bench.integration;
  body.append(h("h1", { class: "wizard-title" }, "配置 Agent"));
  try {
    if (!teamCatalogs.has(integration) || !METHODS_BY_INTEGRATION.has(integration)) {
      body.append(h("p", { class: "muted" }, "正在读取可用工具…"));
      const [groups] = await Promise.all([
        api(`/api/benchmarks/${encodeURIComponent(integration)}/team-tools`), loadMethods(integration),
      ]);
      teamCatalogs.set(integration, groups);
      if (body.isConnected) route();
      return;
    }
    const choices = availableMethods().filter((m) => m.id === TEAM_METHOD || m.external_evaluator);
    if (choices.length > 1) {
      body.append(h("label", { class: "field" }, "运行方法", h("select", { class: "input",
        onchange: (event) => {
          selectMethod(choices.find((m) => m.id === event.target.value), w.agent);
          w.model = null;
          w.touched = true;
          route();
        } }, choices.map((m) => h("option", { value: m.id, selected: m.id === w.method }, m.label)))));
    }
    if (selectedMethod()?.external_evaluator) {
      body.append(h("p", { class: "muted" }, selectedMethod().summary));
      return;
    }
    const groups = teamCatalogs.get(integration);
    if (!w.team) {
      const editable = w.bench.entry_prompt_as_task;
      const entry = newAgentCard(editable ? "actor" : "planner", groups, editable);
      if (editable) entry.instructions = w.task.instruction;
      w.team = [entry];
    }
    const method = selectedMethod();
    if (!method) throw new Error("当前环境没有可用的方法");
    const diagram = harnessPreview(w, selectedAgent().label);
    body.append(h("p", {class: "muted small"}, "委托关系可以从任一端勾选。子任务结束后，结果回给本次发起委托的 Agent。"));
    body.append(...w.team.map((card, index) => roleCard(card, index, groups, w.team, diagram.update)),
      h("button", { class: "btn", onclick: () => addActor(w.team, w.team[0], groups) }, "＋ 添加 Agent"), diagram.root);
    diagram.update();
  } catch (error) {
    body.append(h("div", { class: "notice error" }, error.message), h("button", { class: "btn", onclick: route }, "重试"));
  }
}

async function downloadTeamRecipe(button) {
  const w = S.wizard;
  button.disabled = true;
  try {
    const recipe = await api(`/api/benchmarks/${encodeURIComponent(w.bench.integration)}/team-preview`, {
      method: "POST", json: { method: w.method, agent: w.agent, team: configuredTeam(w) },
    });
    const url = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2)], { type: "application/json" }));
    h("a", { href: url, download: "team.recipe.json" }).click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

// -- run detail graph -----------------------------------------------------------------

async function renderRunGraph(run) {
  const holder = document.getElementById("rvGraph");
  if (!holder || !run.id || holder.dataset.run === run.id) return;
  holder.dataset.run = run.id;
  let graph;
  try { graph = await api(`/api/trace/${encodeURIComponent(run.id)}/graph`); } catch { put(holder); return; }
  showActionPolicies(graph);
  const overrides = Object.entries(graph.overrides || {});
  put(holder, h("details", { class: "card", style: "margin-top:14px" },
    h("summary", { class: "card-pad", style: "cursor:pointer;font-weight:600" }, "方案组成与调用次数"),
    h("div", { style: "padding:0 16px 16px" },
      recipeGraphView(graph, { calls: true }),
      overrides.length ? h("p", { class: "muted small" }, "本次覆盖：", overrides.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("  ")) : null)));
}
