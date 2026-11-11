import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Network, Upload, BookOpen, FileText, Layers, Play, Square, Check, LoaderCircle, Download, ChevronRight, ArrowRight, X, CircleCheck, History, Search } from 'lucide-react';
import { FindingCard, SourceText } from './ReviewDocument';
import { GraphWorkspace } from './GraphWorkspace';
import type { Event, GraphItem, Lifecycle, Project } from './types';
import './style.css';
import './lifecycle.css';

async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const response=await fetch('/api'+path,init);
  if(!response.ok){const body=await response.json().catch(()=>({detail:response.statusText}));throw new Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));}
  return response.json();
}
const json=(body:unknown):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const statuses:Record<string,string>={pending:'待评审',running:'进行中',completed:'已完成',partial:'部分完成',not_applicable:'已检查适用性',stopped:'已停止',failed:'未完成',waiting_user:'待用户确认'};
type Config={model:string;provided_sources:{id:number;name:string}[]};

function App(){
  const [config,setConfig]=useState<Config|null>(null);
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]);
  const [project,setProject]=useState<Project|null>(null);
  const [life,setLife]=useState<Lifecycle|null>(null);
  const [mode,setMode]=useState('ontology');
  const graphMode=useRef(mode);graphMode.current=mode;
  const [followLive,setFollowLive]=useState(true);
  const followRun=useRef(true);
  const [workspace,setWorkspace]=useState('graph');
  const [selected,setSelected]=useState<GraphItem|null>(null);
  const [selectedRun,setSelectedRun]=useState('');
  const [documentId,setDocumentId]=useState('');
  const [reviewDocumentId,setReviewDocumentId]=useState('');
  const reviewScope=useRef('');
  const [sectionId,setSectionId]=useState('');
  const [sourceReturn,setSourceReturn]=useState<{documentId:string;sectionId:string}|null>(null);
  const [activeBlock,setActiveBlock]=useState('');
  const [filter,setFilter]=useState('');
  const [focus,setFocus]=useState('');
  const [busy,setBusy]=useState(false);
  const [running,setRunning]=useState(false);
  const [error,setError]=useState('');
  const [sample,setSample]=useState(2);
  const [connected,setConnected]=useState(false);
  const [events,setEvents]=useState<Event[]>([]);
  const [streams,setStreams]=useState<Record<string,{role:string;text:string}>>({});
  const input=useRef<HTMLInputElement>(null);
  const controller=useRef<AbortController|null>(null);
  const currentProject=useRef('');
  const currentRun=useRef('');
  const refreshTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const refreshBusy=useRef(false);
  const rerunRefresh=useRef(false);
  const document=project?.documents.find(d=>d.id===documentId);
  const section=document?.sections.find(s=>s.id===sectionId);
  const blocks=document?.blocks.filter(b=>{
    const own=document.sections.find(s=>s.id===b.clause_id);
    return section?own?.path.slice(0,section.path.length).join('\n')===section.path.join('\n'):b.clause_id===sectionId;
  })||[];
  const findings=project?.findings.filter(f=>f.document_id===(sourceReturn?.documentId||documentId))||[];
  const dimensions=life?.dimensions||[];
  const stats=life?.statistics;

  async function action(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function loadLife(id:string, run=currentRun.current, target=reviewScope.current){
    const query=new URLSearchParams();if(run)query.set('run_id',run);if(target)query.set('document_id',target);
    const data=await api<Lifecycle>(`/projects/${id}/lifecycle?${query}`);
    if(currentProject.current===id){setLife(data);setSelected(old=>{
      if(!old)return null;
      const active=graphMode.current==='consumption'?data.consumption_graph:graphMode.current==='process'?data.process_graph:graphMode.current==='static'?data.static_graph:data.ontology_graph;
      return active.nodes.find(n=>n.id===old.id)||old;
    });}
  }
  function scheduleLife(){
    if(refreshTimer.current)clearTimeout(refreshTimer.current);
    refreshTimer.current=setTimeout(async()=>{
      if(refreshBusy.current){rerunRefresh.current=true;return;}
      refreshBusy.current=true;
      try{if(currentProject.current)await loadLife(currentProject.current);}catch(e){setError((e as Error).message);}
      finally{refreshBusy.current=false;if(rerunRefresh.current){rerunRefresh.current=false;scheduleLife();}}
    },350);
  }
  function chooseDocument(id:string,p=project){
    setSourceReturn(null);setDocumentId(id);setActiveBlock('');
    const d=p?.documents.find(d=>d.id===id);
    const s=d?.sections.find(s=>/^5\.1\s/.test(s.title))||d?.sections.find(s=>/^3\.[12]\s/.test(s.title))||d?.sections[0];
    setSectionId(s?.id||'');
  }
  async function openProject(p:Project){
    currentProject.current=p.id;currentRun.current='';setProject(p);setLife(null);setSelected(null);setWorkspace('graph');setMode('ontology');setStreams({});
    const target=p.review_document_id||p.documents[0]?.id||'';
    reviewScope.current=target;setReviewDocumentId(target);chooseDocument(target,p);
    localStorage.setItem('starnet-project',p.id);
    const last=p.runs.filter(r=>r.document_id===target).at(-1);setSelectedRun(last?.id||'');currentRun.current=last?.id||'';setEvents(last?.events||[]);
    await loadLife(p.id,last?.id||'');
  }
  async function chooseReviewDocument(id:string){
    reviewScope.current=id;setReviewDocumentId(id);currentRun.current='';setSelectedRun('');setSelected(null);setStreams({});setEvents([]);
    chooseDocument(id);
    if(project)await action(()=>loadLife(project.id,'',id));
  }
  async function refreshProjects(){setProjects(await api('/projects'));}
  useEffect(()=>{void action(async()=>{
    const c=await api<Config>('/config');setConfig(c);setSample(c.provided_sources.find(s=>/需求规格/.test(s.name))?.id||c.provided_sources[0]?.id||1);await refreshProjects();
    try{await api('/health');setConnected(true);}catch{setConnected(false);}
    const id=localStorage.getItem('starnet-project');if(id){try{await openProject(await api('/projects/'+id));}catch{localStorage.removeItem('starnet-project');}}
  });return()=>{if(refreshTimer.current)clearTimeout(refreshTimer.current);};},[]);

  async function upload(files:FileList|null){if(!files?.length)return;await action(async()=>{
    const body=new FormData();for(const file of files)body.append('documents',file);if(project)body.append('project_id',project.id);
    await openProject(await api('/import',{method:'POST',body}));await refreshProjects();if(input.current)input.current.value='';
  });}
  async function reviewAll(){
    if(!project||!life)return;
    setRunning(true);setError('');setStreams({});setEvents([]);setMode('ontology');setWorkspace('graph');setSelected(null);setSelectedRun('');currentRun.current='';followRun.current=true;setFollowLive(true);
    const id=project.id;controller.current=new AbortController();
    try{
      const response=await fetch(`/api/projects/${id}/review-document`,{...json({focus,document_id:reviewDocumentId}),signal:controller.current.signal});
      if(!response.ok)throw new Error((await response.json()).detail);
      const reader=response.body!.getReader(),decoder=new TextDecoder();let buffer='';
      while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const lines=buffer.split('\n');buffer=lines.pop()||'';
        for(const line of lines){if(!line.trim())continue;const e=JSON.parse(line) as Event;
          if(e.kind==='text'&&e.run_id){setStreams(s=>({...s,[e.run_id!]:{role:e.perspective||'',text:e.text||''}}));}
          else{setEvents(old=>[...old,e]);scheduleLife();}
          if(['stage','context','input'].includes(e.kind)&&e.run_id&&followRun.current){currentRun.current=e.run_id;setSelectedRun(e.run_id);}
          if(e.kind==='opinions')setProject(p=>p?{...p,findings:[...p.findings,...(e.findings||[])]}:p);
          if(e.kind==='error')setError(e.message||'本轮未完成，已保留成功结果');
        }
      }
    }catch(e){if((e as Error).name!=='AbortError')setError((e as Error).message);}
    finally{setRunning(false);controller.current=null;setProject(await api('/projects/'+id));await loadLife(id);}
  }
  function locate(blockId:string,docId=sourceReturn?.documentId||documentId){
    if(docId==='standard'){setWorkspace('graph');setMode('ontology');return;}
    const d=project?.documents.find(d=>d.id===docId),block=d?.blocks.find(b=>b.id===blockId);if(!block)return;
    if(docId!==documentId&&!sourceReturn)setSourceReturn({documentId,sectionId});if(sourceReturn?.documentId===docId)setSourceReturn(null);
    setDocumentId(docId);setSectionId(block.clause_id);setActiveBlock(blockId);setWorkspace('document');
    setTimeout(()=>window.document.getElementById('block-'+blockId)?.scrollIntoView({behavior:'smooth',block:'center'}),80);
  }
  async function apply(id:string,text:string,category:string){await action(async()=>{
    const title=section?.title;const p=await api<Project>(`/projects/${project!.id}/findings/${id}/accept`,json({replacement:text,category}));
    setProject(p);setSectionId(p.documents.find(d=>d.id===documentId)?.sections.find(s=>s.title===title)?.id||'');await loadLife(p.id);
  });}
  async function showUsage(runId:string){if(!runId)return;followRun.current=false;setFollowLive(false);setSelectedRun(runId);currentRun.current=runId;setWorkspace('graph');setMode('consumption');setSelected(null);await action(()=>loadLife(project!.id,runId));}

  const latestCycle=life?.cycle_current?life.cycle:null;
  const completed=dimensions.filter(d=>d.status==='completed').length;

  return <div className="app ontology-app"><header><div className="brand"><div className="brand-icon"><Layers size={23}/></div><div><b>星网 · 本体驱动文档评审</b><small>已有关系探索 · 全维度独立评审 · 原文修订</small></div><span className="version">DEMO V1</span></div><div className="header-right"><span className="connection"><i style={{background:connected?'#43a885':'#d0a162'}}/>{connected?'Neo4j 已连接':'Neo4j 待连接'}</span><span className="model">{config?.model||'连接中'}</span></div></header>
    <div className="toolbar"><div className="breadcrumb"><BookOpen size={17}/><select aria-label="当前项目" value={project?.id||''} disabled={busy||running} onChange={e=>void action(async()=>openProject(await api('/projects/'+e.target.value)))}><option value="">选择项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></div><div className="toolbar-actions"><input hidden type="file" accept=".docx,.md,.markdown" ref={input} onChange={e=>void upload(e.target.files)}/><button className="secondary" disabled={busy||running} onClick={()=>input.current?.click()}><Upload size={15}/>导入文档</button><button className="secondary" disabled={busy||running} onClick={()=>{currentProject.current='';setProject(null);setLife(null);localStorage.removeItem('starnet-project');}}>新建项目</button></div></div>
    {error&&<div className="error"><span>{error}</span><button onClick={()=>setError('')}><X size={15}/></button></div>}
    {!project?<main className="welcome"><div className="welcome-icon"><Network size={36}/></div><span className="eyebrow">让本体参与的每一步，都看得见</span><h1>从本体关系<br/>走向有依据的评审意见</h1><p>按顺序导入需求、设计或测试文档。每次只评一份，由多个适用视角独立评审；保存的上游内容本体为后续文档提供约束。</p>{!!config?.provided_sources.length&&<select value={sample} onChange={e=>setSample(Number(e.target.value))} style={{maxWidth:540,marginTop:24,width:'100%'}}>{config.provided_sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select>}<div className="welcome-buttons"><button disabled={busy} onClick={()=>input.current?.click()}><Upload size={17}/>导入项目文档</button>{!!config?.provided_sources.length&&<button className="secondary" disabled={busy} onClick={()=>void action(async()=>{await openProject(await api('/import-provided?sample='+sample,{method:'POST'}));await refreshProjects();})}>{busy?<LoaderCircle className="spin" size={16}/>:<FileText size={16}/>}加载已有样例</button>}</div></main>:<>
      <div className="project-summary"><div><span className="eyebrow">文档级增量 · 多视角评审</span><h1>{project.name}</h1><p>{project.documents.length} 份文档 · {stats?.concepts||'—'} 个领域概念 · {stats?.bindings||'—'} 条实际挂接 · {project.revisions.length} 个修订版本</p></div><div className="graph-stat"><Network size={21}/><div><b>{life?.plan.applicable_count??'—'} <small>本次评审视角</small> / {completed} <small>已完成</small></b><span>按当前文档类型启用适用专家</span></div></div></div>
      <section className="automatic-review"><div className="automatic-review-copy"><label>本次待评审文档 <select aria-label="本次待评审文档" value={reviewDocumentId} disabled={busy||running} onChange={e=>void chooseReviewDocument(e.target.value)}>{project.documents.map(d=><option key={d.id} value={d.id}>{d.title}</option>)}</select></label><b><Layers size={16}/>当前文档 · 多视角评审</b><p>{life?`${life.plan.applicable_count} 个适用视角，各一个专家任务；本轮只处理当前文档，复用已保存的上游内容本体。`:'正在读取评审本体与项目挂接关系…'}</p><small>只构建当前文档的新版本；已保存的上游本体与历史评审保留，不自动重评。</small></div><div className="automatic-controls"><input aria-label="补充关注点" value={focus} onChange={e=>setFocus(e.target.value)} placeholder="补充关注点（可选）" disabled={running}/><button disabled={busy||running||!life||!life.plan.batch_count} onClick={()=>void reviewAll()}>{running?<LoaderCircle className="spin" size={16}/>:<Play size={16}/>}建立本体并评审当前文档</button>{running&&<button className="secondary" onClick={()=>controller.current?.abort()}><Square size={13}/>停止</button>}</div></section>
      <div className="dimension-statuses">{dimensions.filter(d=>d.batches.length).map(d=><div className={'dimension-status state-'+d.status} key={d.id}><span className="dimension-dot"/><div><b>{d.label}</b><small>{statuses[d.status]||d.status} · {d.batches.length} 个任务 / {d.block_count} 块</small></div>{d.status==='completed'&&<Check size={14}/>}</div>)}</div>
      <nav className="workspace-tabs"><div>{[['graph','本体知识图谱'],['document','原文与评审意见'],['activity','执行记录']].map(([id,label])=><button key={id} className={workspace===id?'active':''} onClick={()=>setWorkspace(id)}>{id==='graph'?<Network size={15}/>:id==='document'?<FileText size={15}/>:<History size={15}/>} {label}</button>)}</div>{latestCycle&&<span>{statuses[latestCycle.status]} · {latestCycle.metrics.model_calls||0} 次调用 · {(latestCycle.metrics.total_tokens||0).toLocaleString()} Token</span>}</nav>
      {workspace==='graph'?<GraphWorkspace projectKey={project.id} life={life} mode={mode} setMode={setMode} selected={selected} setSelected={setSelected} selectedRun={selectedRun} onRun={id=>void showUsage(id)} running={running} followLive={followLive} onFollow={value=>{followRun.current=value;setFollowLive(value);}} streams={streams} onLocate={locate}/>:workspace==='document'?<div className="workspace source-workspace"><aside className="navigator"><div className="nav-title"><FileText size={17}/><b>项目文档</b></div><div className="documents">{project.documents.map((d,i)=><button key={d.id} className={d.id===documentId?'selected':''} onClick={()=>chooseDocument(d.id)}><span className="doc-number">0{i+1}</span><div><b>{d.type_name}</b><small>{d.title}</small></div><ChevronRight size={14}/></button>)}</div><div className="nav-title chapters"><b>章节定位</b><span>{document?.sections.length}</span></div><label className="search"><Search size={14}/><input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="查找章节"/></label><div className="section-list">{document?.sections.filter(s=>s.title.includes(filter)).map(s=><button key={s.id} className={s.id===sectionId?'selected':''} style={{paddingLeft:12+Math.min(3,s.level-1)*9}} onClick={()=>{setSectionId(s.id);setActiveBlock('');}}>{s.title}</button>)}</div></aside><section className="source-panel"><div className="panel-heading"><div><span className="eyebrow">原文内容</span><h2>{document?.type_name}</h2></div><a className="download" href={`/api/projects/${project.id}/documents/${documentId}/download`}><Download size={15}/>下载当前版本</a></div><div className="source-meta"><span>{document?.filename}</span><small>版本 {document?.sha256.slice(0,10)}</small></div>{sourceReturn&&<button className="text-button" onClick={()=>{setDocumentId(sourceReturn.documentId);setSectionId(sourceReturn.sectionId);setSourceReturn(null);}}>返回待审原文<ArrowRight size={14}/></button>}<div className="source-body">{blocks.map(block=>{const notes=findings.filter(f=>f.block_id===block.id&&f.status!=='dismissed');return <div id={'block-'+block.id} key={block.id} className={`source-block ${block.kind} ${notes.length?'annotated':''} ${activeBlock===block.id?'active-block':''}`} onClick={()=>setActiveBlock(block.id)}>{block.kind==='heading'?<h3>{block.text}</h3>:<p><SourceText text={block.text} findings={notes}/></p>}{!!notes.length&&<span className="note-marker">{notes.every(f=>f.status==='accepted')?<Check size={13}/>:notes.length}</span>}</div>;})}</div><footer className="source-footer"><span>原件保留，意见可回查</span><span>{blocks.length} 个内容块</span></footer></section><section className="review-panel"><div className="panel-heading"><div><span className="eyebrow">全部适用维度的结果</span><h2>原文评审意见</h2></div><span className="opinion-count">{findings.length}</span></div><div className="review-body">{findings.map(f=><div key={f.id}><div className="finding-dimension"><Layers size={12}/>{dimensions.find(d=>d.id===f.perspective)?.label||'历史评审记录'}<button onClick={()=>void showUsage(f.run_id)}><Network size={12}/>查看消费链路</button></div><FindingCard finding={f} active={activeBlock===f.block_id} busy={busy||running||!!sourceReturn} onLocate={locate} onApply={(id,text,category)=>void apply(id,text,category)} onDismiss={id=>void action(async()=>{setProject(await api(`/projects/${project.id}/findings/${id}/dismiss`,{method:'POST'}));await loadLife(project.id);})}/></div>)}{!findings.length&&<div className="empty-review"><CircleCheck size={30}/><h3>当前文档尚无定位意见</h3><p>全维度评审结束后，意见将自动归到对应原文；没有问题时保留实际评审分析与依据。</p></div>}</div></section></div>:<section className="all-activity"><h2>实际执行记录</h2>{events.map((e,i)=><div className="event" key={i}><i/><div><b>{e.title||e.message||e.kind}</b>{e.detail&&<p>{e.detail}</p>}<small>{e.perspective&&dimensions.find(d=>d.id===e.perspective)?.label} {e.time&&new Date(e.time*1000).toLocaleTimeString('zh-CN',{hour12:false})}</small></div></div>)}{latestCycle&&<div className="metrics"><b>{statuses[latestCycle.status]}</b><span>{latestCycle.metrics.elapsed_seconds||0} 秒</span><span>{latestCycle.metrics.model_calls||0} 次调用（建图 {latestCycle.metrics.construction_calls||0} · 评审 {latestCycle.metrics.review_calls??latestCycle.metrics.model_calls??0}）</span><span>{(latestCycle.metrics.total_tokens||0).toLocaleString()} Token</span></div>}{!events.length&&<p>全维度评审尚未启动。当前计划由本体和实际文档生成，未发生模型调用。</p>}</section>}
    </>}
    <footer className="app-footer"><span>领域关系来自 Neo4j · 维度与约束来自评审本体 · 使用与采纳来自实际记录</span><span>独立项目 / 内容优先</span></footer>
  </div>;
}
createRoot(window.document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
