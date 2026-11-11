import { memo, useEffect, useRef, useState } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCollide, forceX, forceY } from 'd3-force';
import type { Simulation, SimulationNodeDatum, SimulationLinkDatum } from 'd3-force';
import { Expand, Minus, Plus, RotateCcw } from 'lucide-react';
import { nodeCaption, nodeKinds, relationItem, relationNames } from './knowledge';
import type { GraphData, GraphItem } from './types';
import './knowledge.css';

type Point=SimulationNodeDatum&{id:string;kind:string};
type Link=SimulationLinkDatum<Point>&{id:string};
type View={x:number;y:number;k:number};
type Props={graph:GraphData;scene:string;selected:string;activeNodes?:string[];activeEdges?:string[];animating?:boolean;onSelect:(n:GraphItem)=>void;onExpand:(n:GraphItem)=>void};
const edgeColor=(kind:string)=>kind==='ontology'?'#91a3c6':kind==='configuration'?'#b6a2cb':kind==='aggregation'?'#a8c5c2':kind==='structure'?'#c0ccda':'#88b7a3';
const seed=(id:string)=>Array.from(id).reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,7);
const labelLines=(label:string)=>{const chars=Array.from(label.split(' / ').at(-1)||label);return chars.length<=12?[label.split(' / ').at(-1)||label]:[chars.slice(0,12).join(''),chars.slice(12,23).join('')+(chars.length>23?'…':'')];};

export const KnowledgeGraph=memo(function KnowledgeGraph({graph,scene,selected,activeNodes=[],activeEdges=[],animating=false,onSelect,onExpand}:Props){
  const container=useRef<HTMLDivElement>(null),svg=useRef<SVGSVGElement>(null);
  const simulation=useRef<Simulation<Point,Link>|null>(null);
  const positions=useRef(new Map<string,Map<string,Point>>());
  const [size,setSize]=useState({width:1000,height:620});
  const [view,setView]=useState<View>({x:0,y:0,k:1});
  const viewRef=useRef(view);viewRef.current=view;
  const manualView=useRef(false),drag=useRef<{id?:string;x:number;y:number;origin:View;moved:boolean}|null>(null);
  const [hover,setHover]=useState(''),[showLabels,setShowLabels]=useState(false);
  const [,redraw]=useState(0);
  const currentGraph=useRef(graph);currentGraph.current=graph;
  const topology=graph.nodes.map(n=>n.id).join('|')+';'+graph.edges.map(e=>e.id).join('|');
  const currentPoints=positions.current.get(scene)||new Map<string,Point>();

  useEffect(()=>{
    const observer=new ResizeObserver(entries=>{const rect=entries[0].contentRect;setSize({width:rect.width,height:rect.height});});
    if(container.current)observer.observe(container.current);
    return()=>observer.disconnect();
  },[]);
  useEffect(()=>{manualView.current=false;setView({x:0,y:0,k:1});setHover('');},[scene]);
  useEffect(()=>{
    const element=svg.current;if(!element)return;
    const wheel=(event:WheelEvent)=>{event.preventDefault();manualView.current=true;const rect=element.getBoundingClientRect(),v=viewRef.current,k=Math.max(.12,Math.min(3,v.k*Math.exp(-event.deltaY*.0015))),x=event.clientX-rect.left,y=event.clientY-rect.top;setView({k,x:x-(x-v.x)*k/v.k,y:y-(y-v.y)*k/v.k});};
    element.addEventListener('wheel',wheel,{passive:false});return()=>element.removeEventListener('wheel',wheel);
  },[]);

  function fit(){
    const map=positions.current.get(scene),points=graph.nodes.map(n=>map?.get(n.id)).filter(Boolean) as Point[];
    if(!points.length)return;
    const xs=points.map(p=>p.x||0),ys=points.map(p=>p.y||0);
    const left=Math.min(...xs)-90,right=Math.max(...xs)+90,top=Math.min(...ys)-55,bottom=Math.max(...ys)+75;
    const k=Math.max(.16,Math.min(1.4,size.width/(right-left),size.height/(bottom-top)));
    setView({k,x:size.width/2-(left+right)/2*k,y:size.height/2-(top+bottom)/2*k});
  }
  useEffect(()=>{
    const map=positions.current.get(scene)||new Map<string,Point>(),initial=map.size===0;positions.current.set(scene,map);
    const nodes=graph.nodes.map((n,i)=>{
      if(map.has(n.id))return map.get(n.id)!;
      const related=graph.edges.find(e=>(e.source===n.id&&map.has(e.target))||(e.target===n.id&&map.has(e.source)));
      const near=related?map.get(related.source===n.id?related.target:related.source):null;
      const angle=(seed(n.id)%360)*Math.PI/180,r=70+Math.sqrt(i)*30;
      const point:Point={id:n.id,kind:n.kind,x:(near?.x??size.width/2)+Math.cos(angle)*(near?45:r),y:(near?.y??size.height/2)+Math.sin(angle)*(near?45:r)};
      map.set(n.id,point);return point;
    });
    const links:Link[]=graph.edges.map(e=>({id:e.id,source:e.source,target:e.target}));
    const sim=forceSimulation<Point>(nodes)
      .force('link',forceLink<Point,Link>(links).id(n=>n.id).distance(l=>graph.edges.find(e=>e.id===l.id)?.kind==='ontology'?104:124).strength(.28))
      .force('charge',forceManyBody<Point>().strength(-230))
      .force('collide',forceCollide<Point>().radius(n=>n.kind==='clause'?42:46).iterations(2))
      .force('x',forceX<Point>(size.width/2).strength(.018))
      .force('y',forceY<Point>(size.height/2).strength(.2))
      .alpha(.65).alphaDecay(.035).velocityDecay(.48);
    simulation.current=sim;
    if(initial){sim.tick(110);fit();sim.alpha(.18);}
    let last=0;
    sim.on('tick',()=>{const now=performance.now();if(now-last>28){last=now;redraw(n=>n+1);}});
    sim.on('end',()=>{redraw(n=>n+1);if(!manualView.current)fit();});
    const timer=setTimeout(()=>{if(!manualView.current)fit();},1000);
    redraw(n=>n+1);
    return()=>{clearTimeout(timer);sim.stop();};
  },[topology,scene,size.width,size.height]);

  function zoom(factor:number){manualView.current=true;setView(v=>{const k=Math.max(.12,Math.min(3,v.k*factor));return {k,x:size.width/2-(size.width/2-v.x)*k/v.k,y:size.height/2-(size.height/2-v.y)*k/v.k};});}
  function world(event:{clientX:number;clientY:number}){const rect=svg.current!.getBoundingClientRect(),v=viewRef.current;return {x:(event.clientX-rect.left-v.x)/v.k,y:(event.clientY-rect.top-v.y)/v.k};}
  function start(event:React.PointerEvent,id?:string){
    if(event.button!==0)return;
    event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);
    drag.current={id,x:event.clientX,y:event.clientY,origin:viewRef.current,moved:false};
  }
  function move(event:React.PointerEvent){
    const state=drag.current;if(!state)return;
    if(Math.hypot(event.clientX-state.x,event.clientY-state.y)>3)state.moved=true;
    if(!state.moved)return;
    if(state.id){const point=positions.current.get(scene)?.get(state.id);if(point){const pos=world(event);point.fx=point.x=pos.x;point.fy=point.y=pos.y;simulation.current?.alphaTarget(.16).restart();redraw(n=>n+1);}}
    else{manualView.current=true;setView({...state.origin,x:state.origin.x+event.clientX-state.x,y:state.origin.y+event.clientY-state.y});}
  }
  function end(){const state=drag.current;if(!state)return;if(state.id&&!state.moved){const item=currentGraph.current.nodes.find(n=>n.id===state.id);if(item)onSelect(item);}simulation.current?.alphaTarget(0);drag.current=null;}

  const candidate=hover||selected,focus=graph.nodes.some(n=>n.id===candidate)||graph.edges.some(e=>e.id===candidate)?candidate:'',neighbors=new Set<string>([focus]),nearEdges=new Set<string>();
  for(const e of graph.edges)if(e.source===focus||e.target===focus||e.id===focus){neighbors.add(e.source);neighbors.add(e.target);nearEdges.add(e.id);}
  const active=new Set(activeNodes),activeLines=new Set(activeEdges);
  const edgePositions=(source:string,target:string)=>{const a=currentPoints.get(source),b=currentPoints.get(target);if(!a||!b)return null;const dx=(b.x||0)-(a.x||0),dy=(b.y||0)-(a.y||0),distance=Math.hypot(dx,dy)||1;const r1=nodeKinds[a.kind]?.radius||16,r2=(nodeKinds[b.kind]?.radius||16)+5;return {x1:(a.x||0)+dx/distance*r1,y1:(a.y||0)+dy/distance*r1,x2:(b.x||0)-dx/distance*r2,y2:(b.y||0)-dy/distance*r2};};
  return <div ref={container} className={'knowledge-canvas '+(animating?'is-animating':'')}>
    <svg ref={svg} className="knowledge-svg" aria-label="本体知识图谱" onPointerDown={e=>start(e)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
      <defs><pattern id="kg-dots" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".8" fill="#dfe7f2"/></pattern><marker id="kg-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 1 L 9 5 L 0 9 z" fill="#9aadc3"/></marker></defs>
      <rect width="100%" height="100%" fill="url(#kg-dots)"/>
      <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
        {graph.edges.map(e=>{const p=edgePositions(e.source,e.target);if(!p)return null;const hot=activeLines.has(e.id),near=nearEdges.has(e.id),dim=!!focus&&!near;
          return <g key={e.id} className={'kg-edge '+(hot?'active ':'')+(near?'near':'')} data-edge-id={e.id} data-edge-kind={e.kind} opacity={dim?.12:hot||near?1:.55} onPointerDown={ev=>ev.stopPropagation()} onClick={()=>onSelect(relationItem(e,graph))} onMouseEnter={()=>setHover(e.id)} onMouseLeave={()=>setHover('')}>
            <line {...p} stroke="transparent" strokeWidth={13}/><line {...p} className="kg-edge-line" stroke={hot?'#58a88e':near?'#7289b4':edgeColor(e.kind)} strokeWidth={hot?2.1:near?1.7:1.1} strokeDasharray={e.kind==='configuration'?'5 5':e.kind==='aggregation'?'2 5':e.kind==='usage'?'8 4':undefined} markerEnd={hot||near?'url(#kg-arrow)':undefined}/>
            {(near||showLabels||(hot&&e.kind==='ontology'))&&<text x={(p.x1+p.x2)/2} y={(p.y1+p.y2)/2-5} className="kg-edge-label">{relationNames[e.label]||e.label}{e.clause_ids?' · '+e.clause_ids.length:''}</text>}
            <title>{relationNames[e.label]||e.label}{e.clause_ids?` · ${e.clause_ids.length} 个真实条款`:''}</title>
          </g>;})}
        {graph.nodes.map(n=>{const p=currentPoints.get(n.id);if(!p)return null;const kind=nodeKinds[n.kind]||nodeKinds.concept,r=kind.radius,hot=active.has(n.id),isSelected=selected===n.id,dim=!!focus&&!neighbors.has(n.id);const lines=labelLines(n.label);
          return <g key={n.id} data-node-id={n.id} data-kind={n.kind} data-layer={n.layer} className={'kg-node '+(hot?'active ':'')+(isSelected?'selected':'')} transform={`translate(${p.x||0},${p.y||0})`} opacity={dim?.2:1}
            role="button" tabIndex={0} aria-label={kind.label+'：'+nodeCaption(n)} onKeyDown={e=>{if(e.key==='Enter')onSelect(n);}}
            onPointerDown={e=>start(e,n.id)} onDoubleClick={()=>onExpand(n)} onMouseEnter={()=>setHover(n.id)} onMouseLeave={()=>setHover('')}>
            {(hot||isSelected)&&<circle className="kg-node-ring" r={r+7} fill="none" stroke={hot?'#65b29c':kind.color} strokeWidth={hot?2:1.5}/>}
            <circle className="kg-node-core" r={r} fill={kind.color} stroke="white" strokeWidth={2.5}/>
            {n.layer==='runtime'&&<circle r={r+3} className="kg-runtime-ring" fill="none" stroke={kind.color} strokeDasharray="3 3"/>}
            {n.kind!=='clause'&&<text className="kg-node-symbol" textAnchor="middle" y="4">{n.kind==='concept'?'':kind.label.slice(0,1)}</text>}
            {p.fx!=null&&<circle cx={r*.65} cy={-r*.65} r={3} fill="#425a7d" stroke="white"/>}
            {n.document_name&&<text className="kg-node-document" textAnchor="middle" y={r+14}>{n.document_name}</text>}
            <text className="kg-node-label" textAnchor="middle" y={r+(n.document_name?29:16)}>{lines.map((line,i)=><tspan key={i} x="0" dy={i?15:0}>{line}</tspan>)}</text>
            <title>{kind.label} · {nodeCaption(n)}{n.document_title?'\n'+n.document_title:''}{n.subtitle?'\n'+n.subtitle:''}{Number(n.details?.expand_count)>0?'\n双击展开关联条款或配置':''}</title>
          </g>;})}
      </g>
    </svg>
    {!graph.nodes.length&&<div className="kg-empty"><p>当前阶段没有可展示的节点</p><small>有真实召回与评审记录后，图谱会随过程展开。</small></div>}
    <div className="kg-overview"><b>{graph.nodes.length}</b> 节点 <i/> <b>{graph.edges.length}</b> 关系</div>
    <label className="kg-label-toggle"><input type="checkbox" checked={showLabels} onChange={e=>setShowLabels(e.target.checked)}/>显示全部关系名</label>
    <div className="kg-controls"><button aria-label="放大图谱" onClick={()=>zoom(1.25)}><Plus size={16}/></button><button aria-label="缩小图谱" onClick={()=>zoom(.8)}><Minus size={16}/></button><button aria-label="适应图谱" onClick={()=>{manualView.current=true;fit();}}><Expand size={16}/></button><button aria-label="释放固定节点，重新布局" onClick={()=>{for(const p of currentPoints.values()){p.fx=null;p.fy=null;}simulation.current?.alpha(.5).restart();}}><RotateCcw size={15}/></button></div>
    <div className="kg-hint">拖动节点 · 双击展开 · 滚轮缩放 · 点击关系查看依据</div>
  </div>;
});
