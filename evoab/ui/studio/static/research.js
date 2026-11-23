// PhysicalRSI campaigns span multiple episodes and preserve upstream lineage.
"use strict";
async function pageResearch(main) {
  put(main, h("div", { class: "page wide" },
    pageHead("PhysicalRSI 自我改进", "System 1 执行 → 开发观测 → System 2 提案 → 配对评估 → 选择与继承 → 研究记忆更新"),
    h("div", { id: "researchForm", class: "card card-pad" }, "正在读取环境和模型…"),
    h("div", { id: "researchCards", style: "margin-top:20px" })));
  try {
    const [models] = await Promise.all([api("/api/models"), loadBenchmarks()]);
    if (current.page !== "research") return;
    const ready = S.benchmarks.flatMap(b => b.profiles.filter(p => p.launchable).map(p => ({ b, p })));
    const presets = models.presets || [];
    const env = h("select", { class: "input", "aria-label": "研究环境" },
      ready.map((r,i) => h("option", { value:i }, r.b.title)));
    const task = h("select", { class: "input", "aria-label": "研究任务" });
    const model = h("select", { class: "input", "aria-label": "研究模型" },
      presets.map(p => h("option", { value:p.name, selected:p.name===S.config.settings.default_model }, p.name)));
    const number = (label, value, min, max) => h("input", { class:"input",type:"number",value,min,max,"aria-label":label });
    const rounds=number("研究轮数",2,1,20),candidates=number("每轮候选数",1,1,4);
    const validation=number("验证种子数",3,2,50), turns=number("每回合工具轮数上限",8,1,100);
    const seed=number("起始种子",0,0,1000000);
    const field=(label,node)=>h("label",{class:"field"},h("span",{class:"section-title"},label),node);
    const load=async()=>{
      const selected=ready[Number(env.value)];if(!selected)return;
      const tasks=await loadTasks(selected.b,selected.p.profile);
      put(task,tasks.map(t=>h("option",{value:t.task,selected:t.task==="reach-v3"},t.task)));
    };
    env.addEventListener("change",()=>load().catch(e=>toast(e.message,true)));
    await load();
    const submit=h("button",{class:"btn primary",disabled:!ready.length||!presets.length,onclick:async()=>{
      const selected=ready[Number(env.value)];
      const payload={integration:selected.b.integration,task:task.value,model:model.value,
        rounds:Number(rounds.value),candidates:Number(candidates.value),validation_cases:Number(validation.value),
        max_turns:Number(turns.value),seed:Number(seed.value)};
      const episodes=payload.rounds*(1+(payload.candidates+1)*payload.validation_cases)+1;
      payload.max_trials=episodes;
      if(!confirm(`启动 ${payload.rounds} 轮 PhysicalRSI 自我改进？预计最多 ${episodes} 个仿真回合，每回合最多 ${payload.max_turns} 次模型决策，另有提案与反思调用。将使用当前 API 并产生用量。`))return;
      submit.disabled=true;
      try { const result=await api("/api/research",{method:"POST",json:payload});toast(`已启动 ${result.research_id}`);await refreshResearch(); }
      catch(e){toast(`启动失败：${e.message}`,true);}finally{submit.disabled=false;}
    }},"启动完整自我改进");
    put($("researchForm"),h("h2",{class:"section-title"},"新建研究"),
      !ready.length||!presets.length?h("p",{class:"notice"},"请先部署一个评测环境并配置模型预设。"):null,
      h("div",{class:"choice-grid"},field("环境",env),field("任务",task),field("模型",model),
        field("研究轮数",rounds),field("每轮候选数",candidates),field("验证种子数",validation),
        field("每回合工具轮数上限",turns),field("起始种子",seed)),
      h("p",{class:"muted small"},"使用 PhysicalRSI 原生研究循环与统计选择。开发、验证和最终测试使用分离的种子；候选可改变运动步数、接近间距和观测历史长度，研究记忆随轮次更新。模型权重保持固定。"),submit);
  } catch(e){if($("researchForm"))put($("researchForm"),h("p",{class:"notice error"},e.message));}
  await refreshResearch();
}
const researchSelection = { selected: new Set(), busy: false };
async function refreshResearch() {
  const holder=$("researchCards");if(!holder)return;
  try {
    const {campaigns}=await api("/api/research");
    if (researchSelection.busy) return;
    if(!$("researchCards")||current.page!=="research")return;
    const selection=researchSelection;
    selection.selected=new Set([...selection.selected].filter(id=>campaigns.some(c=>c.research_id===id)));
    const render=()=>{
    const ids=[...selection.selected];
    const active=campaigns.some(c=>selection.selected.has(c.research_id)&&c.alive);
    const label={running:"运行中",completed:"已完成",error:"出错",interrupted:"已中断",budget_exhausted:"预算用完",starting:"启动中"};
    put(holder,
      h("div",{class:"toolbar",style:"margin-bottom:16px"},
        h("label",{class:"check"},h("input",{type:"checkbox","aria-label":"全选自我改进记录",
          checked:campaigns.length>0&&ids.length===campaigns.length,disabled:!campaigns.length||selection.busy,
          onchange:e=>{selection.selected=new Set(e.target.checked?campaigns.map(c=>c.research_id):[]);render();}}),"全选"),
        h("span",{class:"muted"},`共 ${campaigns.length} 条，已选 ${ids.length} 条`),
        h("button",{class:"btn small",disabled:!campaigns.length||selection.busy,
          onclick:()=>{selection.selected=new Set(campaigns.map(c=>c.research_id));render();}},"全选记录"),
        h("button",{class:"btn small",disabled:!ids.length||selection.busy,
          onclick:()=>{selection.selected.clear();render();}},"取消选择"),
        h("button",{class:"btn small danger",disabled:!ids.length||active||selection.busy,
          title:active?"请先停止运行中的研究":"移入回收站，可恢复",
          onclick:()=>recycleResearch(ids,render)},"删除"),
        active?h("span",{class:"muted small"},"请先停止运行中的研究，再删除。"):null),
      h("p",{class:"muted small"},"删除研究会保留完整证据以便恢复；关联的任务运行记录单独管理。"),
      campaigns.length?campaigns.map(c=>h("article",{class:"card card-pad",style:"margin-bottom:16px"},
      h("div",{class:"toolbar"},
        h("input",{type:"checkbox","aria-label":`选择研究 ${c.research_id}`,checked:selection.selected.has(c.research_id),disabled:selection.busy,
          onchange:e=>{if(e.target.checked)selection.selected.add(c.research_id);else selection.selected.delete(c.research_id);render();}}),
        h("h2",{class:"section-title"},c.research_id),
        h("span",{class:"pill"},label[c.state]||c.state),
        h("span",{class:"muted"},`${c.completed_rounds} / ${c.config.rounds||"?"} 轮`),
        h("button",{class:"btn small",disabled:!c.alive,onclick:()=>researchAction(c,"stop")},"停止研究"),
        h("button",{class:"btn small",disabled:c.alive||c.state==="completed",onclick:()=>researchAction(c,"resume")},"继续研究")),
      h("p",{},`${c.label ? c.label + " · " : ""}${c.config.integration||""} · ${c.config.task||""} · ${c.config.model||""}`),
      h("p",{class:"muted small"},`提案轮次：${c.proposals} · 研究记忆：${Object.keys(c.lessons).length} 条`),
      h("p",{class:"muted small"},`已完成 ${c.completed_episodes||0} 个仿真回合 · 当前阶段：${({reset:"环境初始化",episode:"System 1 执行",verification:"官方判分",finalization:"回合收尾",system2:"System 2 提案、选择或反思",completed:"研究完成",error:"出错",interrupted:"已中断"})[c.phase]||c.phase||"正在启动"}`),
      c.error?h("p",{class:"notice error"},c.error):null,
      h("div",{class:"table-wrap"},h("table",{class:"table"},
        h("thead",{},h("tr",{},["轮次","选择结果","技能版本","记忆版本"].map(t=>h("th",{},t)))),
        h("tbody",{},c.rounds.map(r=>h("tr",{},h("td",{},r.index+1),h("td",{},r.outcome),
          h("td",{class:"mono",title:r.result_revision},r.result_revision.slice(0,12)),
          h("td",{class:"mono",title:r.result_memory},r.result_memory.slice(0,12))))))),
      h("details",{},h("summary",{},"研究记忆"),Object.entries(c.lessons).map(([name,l])=>h("p",{},`${name}: ${l.hypothesis}`))),
      h("details",{},h("summary",{},"候选改动与评估"),h("pre",{class:"log"},JSON.stringify({candidates:c.proposal_details,evaluations:c.evaluations},null,2))),
      c.test_report?h("details",{},h("summary",{},"最终独立测试结果"),h("pre",{class:"log"},JSON.stringify(c.test_report.metrics||c.test_report,null,2))):null,
      h("details",{},h("summary",{},"执行日志"),h("pre",{class:"log"},c.log||"正在启动…"))
    )):h("div",{class:"card empty"},"还没有研究记录。上方可启动完整的 PhysicalRSI 自我改进流程。"));
    };
    render();
  }catch(e){if($("researchCards"))put($("researchCards"),h("p",{class:"notice error"},e.message));}
}
async function researchAction(c,action) {
  if(!confirm(action==="stop"?"停止当前研究？已完成轮次和证据会保留。":"从已有证据继续？不确定的执行阶段需要先核对，系统不会直接重放。"))return;
  try{await api(`/api/research/${c.research_id}/${action}`,{method:"POST"});await refreshResearch();}
  catch(e){toast(e.message,true);}
}

async function recycleResearch(ids,render) {
  if(researchSelection.busy||!ids.length)return;
  if(!confirm(`将选中的 ${ids.length} 条自我改进记录移入回收站？研究证据保留，可恢复；关联任务运行记录单独管理。`))return;
  researchSelection.busy=true;render();
  try {
    await api("/api/research/trash",{method:"POST",json:{refs:ids}});
    researchSelection.selected.clear();toast("已移入回收站 · 自我改进");
  } catch(e){toast(`删除失败：${e.message}`,true);}
  finally {researchSelection.busy=false;if(current.page==="research")await refreshResearch();}
}
