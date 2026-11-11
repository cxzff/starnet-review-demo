import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, Expand, LoaderCircle, Network, Pause, Play, Search } from 'lucide-react';
import { KnowledgeGraph } from './KnowledgeGraph';
import { GraphInspector } from './GraphInspector';
import { nodeKinds, ontologyNetwork, traceNetwork } from './knowledge';
import type { GraphData, GraphItem, Lifecycle } from './types';

const LifecycleGraph=lazy(()=>import('./LifecycleGraph').then(module=>({default:module.LifecycleGraph})));

type Props={projectKey:string;life:Lifecycle|null;historical:boolean;allowExecution:boolean;mode:string;setMode:(s:string)=>void;selected:GraphItem|null;setSelected:(n:GraphItem|null)=>void;selectedRun:string;onRun:(id:string)=>void;running:boolean;followLive:boolean;onFollow:(value:boolean)=>void;streams:Record<string,{role:string;text:string}>;onLocate:(block:string,document?:string)=>void};

export function GraphWorkspace({projectKey,life,historical,allowExecution,mode,setMode,selected,setSelected,selectedRun,onRun,running,followLive,onFollow,streams,onLocate}:Props){
  const [expanded,setExpanded]=useState(new Set<string>()),[allClauses,setAllClauses]=useState(false),[search,setSearch]=useState('');
  const [step,setStep]=useState(6),[playing,setPlaying]=useState(false);
  const trace=life?.trace&&life.selected_run===selectedRun?life.trace:null;
  const isLive=!!(running&&followLive&&trace?.status==='running');
  useEffect(()=>{setExpanded(new Set());setSearch('');setPlaying(false);},[projectKey]);
  useEffect(()=>{setStep(trace?.live_step??0);setPlaying(false);setSelected(null);},[trace?.run_id]);
  useEffect(()=>{if(isLive&&trace)setStep(trace.live_step);},[isLive,trace?.live_step]);
  useEffect(()=>{if(!playing||!trace)return;const timer=setTimeout(()=>{if(step>=trace.max_step)setPlaying(false);else{setSelected(null);setStep(s=>s+1);}},2100);return()=>clearTimeout(timer);},[playing,step,trace?.run_id]);
  const toggle=useCallback((n:GraphItem)=>{if(!Number(n.details?.expand_count))return;setExpanded(old=>{const next=new Set(old);next.has(n.id)?next.delete(n.id):next.add(n.id);return next;});},[]);
  const graph=useMemo<GraphData>(()=>{
    if(!life)return {nodes:[],edges:[]};
    let value=mode==='process'?life.process_graph:mode==='static'?ontologyNetwork(life.static_graph,expanded,allClauses):mode==='consumption'&&trace?traceNetwork(life.consumption_graph,trace,step):ontologyNetwork(life.ontology_graph,expanded,allClauses);
    if(search.trim()){
      const matches=new Set(value.nodes.filter(n=>n.label.includes(search)||n.subtitle?.includes(search)||n.document_name?.includes(search)||n.document_title?.includes(search)).map(n=>n.id)),ids=new Set(matches);
      for(const e of value.edges)if(matches.has(e.source)||matches.has(e.target)){ids.add(e.source);ids.add(e.target);}
      value={nodes:value.nodes.filter(n=>ids.has(n.id)),edges:value.edges.filter(e=>ids.has(e.source)&&ids.has(e.target))};
    }
    return value;
  },[life,mode,expanded,allClauses,search,trace?.run_id,step]);
  const kinds=[...new Set(graph.nodes.map(n=>n.kind))].filter(k=>nodeKinds[k]);
  const pendingUpstream=life?.content_status.filter(d=>d.document_id!==life.plan.document_id&&!d.saved)||[];
  const hasContent=!!life?.content_status.find(d=>d.document_id===life.plan.document_id)?.saved;
  const ontologyTitle=hasContent?'文档内容本体（已保存）':'原文与静态挂接';
  const modes=[['ontology',ontologyTitle],['static','静态规范本体'],...(allowExecution?[['consumption',historical?'历史消费记录':'本次消费记录'],['process',historical?'历史流程':'本次流程']]:[])];
  function replay(){if(!trace)return;onFollow(false);setMode('consumption');setSelected(null);setSearch('');setStep(0);setPlaying(true);}
  function viewMode(id:string){setMode(id);setSelected(null);setSearch('');setPlaying(false);}
  return <div className="ontology-workspace knowledge-workspace"><section className="ontology-main">
    {mode==='ontology'&&!!pendingUpstream.length&&<p className="trace-note">已上传但尚未建立本体的上游文档：{pendingUpstream.map(d=>d.title).join('、')}。可先完成这些上游文档；本轮不会自动替它们建图或重评。</p>}
    <div className="graph-scene-header"><div><h2><Network size={18}/>{mode==='process'?(historical?'历史评审流程':'本次评审流程'):mode==='consumption'?(historical?'历史评审如何使用本体':'本次评审如何使用本体'):mode==='static'?'静态规范与类型本体':ontologyTitle}</h2><p>{mode==='ontology'?(hasContent?'这是当前文档已保存的内容本体，可在新评审中复用。':'原文导入后的条款结构与通用类型挂接；文档内容本体将在开始评审后构建。'):mode==='static'?'静态标准条款保留规范原文与来源，不被项目内容覆盖。':mode==='consumption'?'沿所选记录查看召回、输入与引用；历史回放不会重新发起评审。':'流程概览说明所选评审记录的阶段。'}</p></div>{mode!=='process'&&trace&&allowExecution&&<button className="secondary" disabled={isLive} onClick={replay}><Play size={12}/>回放所选评审记录</button>}</div>
    <div className="graph-toolbar"><div className="graph-mode">{modes.map(([id,label])=><button key={id} className={mode===id?'active':''} onClick={()=>viewMode(id)}>{label}</button>)}</div>
      {mode==='ontology'&&<><label className="graph-option"><input type="checkbox" checked={allClauses} onChange={e=>setAllClauses(e.target.checked)}/>展开全部条款</label>{!!expanded.size&&<button className="kg-expand-reset" onClick={()=>setExpanded(new Set())}>收起展开</button>}</>}
      {mode==='consumption'&&<><select aria-label="查看实际处理记录" value={selectedRun} onChange={e=>{setPlaying(false);onRun(e.target.value);}}><option value="">等待真实处理记录</option>{life?.runs.map((r,i)=><option value={r.id} key={r.id}>{r.agent} · {r.scope==='dimension'?'完整维度':'历史记录'} {i+1}</option>)}</select>{running&&<label className="trace-live-follow"><input type="checkbox" checked={followLive} onChange={e=>onFollow(e.target.checked)}/>跟随当前处理</label>}</>}
      <label className="graph-search"><Search size={13}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="查找概念或条款"/></label></div>
    {mode==='consumption'&&trace&&<div className="trace-player"><div className="trace-player-head"><div><b>{isLive?'实时评审':playing?'历史轨迹回放':'已保存的真实记录'} · {trace.steps[step]?.label}</b><p>{trace.agent} · {trace.snapshot_id?'快照 '+trace.snapshot_id.slice(0,10):'当前记录'} · {isLive?'跟随实际事件更新':'回放不调用模型，不创建新的本体关系'}</p></div><button className="secondary" disabled={isLive} onClick={()=>playing?setPlaying(false):replay()}>{playing?<Pause size={12}/>:<Play size={12}/>} {playing?'暂停':'从头回放'}</button></div><div className="trace-steps">{trace.steps.map((s,i)=><button key={s.id} className={step===i?'active':step>i?'passed':''} disabled={i>trace.max_step||(isLive&&i>trace.live_step)} onClick={()=>{setPlaying(false);setSelected(null);setStep(i);}}>{i+1} {s.label}</button>)}</div></div>}
    {mode!=='process'&&<div className="kg-node-legend">{kinds.map(k=><span key={k}><i style={{background:nodeKinds[k].color}}/>{nodeKinds[k].label}</span>)}{mode==='consumption'&&<span className="kg-layer-key"><i className="runtime-outline"/>虚线外圈：运行记录</span>}</div>}
    {!life?<div className="graph-loading"><LoaderCircle className="spin"/>正在读取本体节点与关系</div>:mode==='process'?<Suspense fallback={<div className="graph-loading">正在载入流程概览</div>}><LifecycleGraph graph={graph} mode="process" selected={selected?.id||''} onSelect={setSelected}/></Suspense>:<KnowledgeGraph graph={graph} scene={mode==='consumption'&&trace?'trace:'+trace.run_id:mode+':'+projectKey} selected={selected?.id||''} onSelect={setSelected} onExpand={toggle} activeNodes={mode==='consumption'?trace?.steps[step]?.active_node_ids:undefined} activeEdges={mode==='consumption'?trace?.steps[step]?.active_edge_ids:undefined} animating={playing||isLive}/>}
    {mode==='ontology'&&life?.content_status.some(d=>d.document_id===life.plan.document_id&&!d.saved)&&<p className="trace-note">本次文档尚未建立内容本体：{life.content_status.filter(d=>d.document_id===life.plan.document_id&&!d.saved).map(d=>d.title).join('、')}。开始评审时构建并保存，其他文档不自动重评。</p>}<div className="kg-relation-legend"><span><i/>通用类型关系</span><span><i className="usage"/>原文明示内容关系</span><span><i className="structure"/>文档结构 / 规则挂接</span>{mode==='consumption'&&<span><i className="configuration"/>显式评审配置</span>}<span><i className="aggregation"/>文档挂接汇总</span>{mode==='consumption'&&<span><i className="usage"/>运行输入 / 引用记录</span>}<small>配置和运行连线不是新增的语义关系</small></div>
  </section><aside className="ontology-inspector">{mode==='consumption'&&trace?.stats&&<section className="recall-statistics" aria-label="本次召回与引用统计"><span className="eyebrow">运行统计 · 不作为本体节点</span><h3>本次召回与引用</h3><dl><dt>待审内容块</dt><dd>{trace.stats.target_blocks??'旧记录未保存明细'}</dd><dt>召回原文</dt><dd>{trace.stats.retrieved_blocks} 块</dd><dt>已有关系路径</dt><dd>{trace.stats.existing_paths} 条</dd><dt>输入标准</dt><dd>{trace.stats.standards} 条</dd><dt>被意见引用的标准</dt><dd>{trace.stats.cited_standards} 条</dd></dl><p>输入标准不代表意见已引用。仅对绑定到标准原文的证据建立引用关系。</p>{!trace.stats.configuration_saved&&trace.stats.standards>0&&<p className="trace-note">旧记录没有保存配置版本。图中仅显示当时的标准输入清单，不补造旧配置来源。</p>}</section>}<GraphInspector item={selected} onLocate={onLocate}/>{mode==='ontology'&&selected&&Number(selected.details?.expand_count)>0&&<button className="secondary expand-neighbors" onClick={()=>toggle(selected)}><Expand size={13}/>{expanded.has(selected.id)?'收起':'展开'}关联条款或配置 · {Number(selected.details?.expand_count)}</button>}
    {running&&<div className="live-agent-output"><h3><Bot size={15}/>建图与维度实时输出</h3>{Object.entries(streams).map(([id,s])=><details open key={id}><summary>{s.role==='content'?'文档内容建图':life?.dimensions.find(d=>d.id===s.role)?.name||s.role}</summary><p>{s.text}</p></details>)}{!Object.keys(streams).length&&<p>正在装配本体范围、原文与已有关系；图谱随真实处理记录展开。</p>}</div>}
  </aside></div>;
}
