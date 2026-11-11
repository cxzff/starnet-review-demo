import { memo, useEffect, useMemo, useRef } from 'react';
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, MarkerType, useNodesState, useEdgesState } from '@xyflow/react';
import type { Edge, Node, NodeProps } from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import { Network, FileText, Bot, Quote, Layers, GitBranch, CircleCheck, Clock3, LoaderCircle } from 'lucide-react';
import type { GraphData, GraphItem } from './types';
import '@xyflow/react/dist/style.css';

type FlowNode = Node<GraphItem & Record<string,unknown>, 'ontology'>;
const statuses: Record<string,string> = {completed:'已完成',running:'进行中',pending:'待处理',not_applicable:'已检查适用性',waiting_user:'待用户采纳',accepted:'已采纳',open:'待确认',partial:'部分完成',stopped:'已停止',failed:'未完成'};
const relations: Record<string,string> = {relatedTo:'关联',partOf:'属于',contains:'包含',CONTAINS:'包含条款',CLASSIFIED_AS:'归入概念'};
const colors: Record<string,string> = {concept:'#8a73bf',ontology:'#6e78ba',profile:'#6e78ba',dimension:'#526ac5',document:'#6e9cad',clause:'#89a4b9',standard:'#af9364',doctype:'#6c9c94',input:'#5c9b93',agent:'#6179bf',finding:'#c09d5c',evidence:'#7b9f96',revision:'#659a84',stage:'#7186b5'};

const OntologyNode = memo(function OntologyNode({data, selected}: NodeProps<FlowNode>) {
  const Icon = ['ontology','profile','concept','standard','doctype'].includes(data.kind) ? Network : data.kind === 'agent' ? Bot : data.kind === 'finding' ? Quote : data.kind === 'dimension' ? Layers : data.kind === 'stage' ? GitBranch : FileText;
  return <div className={`ontology-node kind-${data.kind} ${selected ? 'is-selected' : ''} state-${data.status || 'pending'}`}>
    <Handle type="target" position={Position.Left} id="left"/>
    <Handle type="target" position={Position.Top} id="top"/>
    <div className="ontology-node-top"><span style={{background:colors[data.kind]+'18',color:colors[data.kind]}}><Icon size={16}/></span><b>{data.label}</b></div>
    {data.subtitle && <p>{data.subtitle}</p>}
    <div className="ontology-node-state">{data.status === 'running' ? <LoaderCircle className="spin" size={11}/> : ['completed','accepted'].includes(data.status || '') ? <CircleCheck size={11}/> : <Clock3 size={11}/>} {statuses[data.status || ''] || '已有记录'}</div>
    <Handle type="source" position={Position.Right} id="right"/>
    <Handle type="source" position={Position.Bottom} id="bottom"/>
  </div>;
});
const nodeTypes = {ontology: OntologyNode};

export function LifecycleGraph({graph, mode, selected, onSelect}: {graph:GraphData;mode:string;selected:string;onSelect:(n:GraphItem)=>void}) {
  const [nodes,setNodes,onNodesChange] = useNodesState<FlowNode>([]);
  const [edges,setEdges,onEdgesChange] = useEdgesState<Edge>([]);
  const saved = useRef(new Map<string,{x:number;y:number}>());
  const previousMode = useRef(mode);
  const topology = graph.nodes.map(n=>n.id).join('|')+graph.edges.map(e=>e.id).join('|');
  const positions = useMemo(()=>{
    if(mode==='process'){
      const stagePositions:Record<string,{x:number;y:number}>={ontology:{x:0,y:0},alignment:{x:270,y:0},retrieval:{x:540,y:0},exploration:{x:810,y:0},reasoning:{x:270,y:380},consumption:{x:540,y:380},revision:{x:810,y:380}};
      const dimensions=graph.nodes.filter(n=>n.kind==='dimension');
      return new Map(graph.nodes.map(n=>[n.id,n.kind==='dimension'?{x:dimensions.findIndex(d=>d.id===n.id)*225,y:190}:stagePositions[n.stage]]));
    }
    const g=new dagre.graphlib.Graph();g.setGraph({rankdir:'LR',nodesep:35,ranksep:95,marginx:40,marginy:40});g.setDefaultEdgeLabel(()=>({}));
    for(const n of graph.nodes)g.setNode(n.id,{width:220,height:120});
    for(const e of graph.edges)g.setEdge(e.source,e.target);
    dagre.layout(g);
    return new Map(graph.nodes.map(n=>{const p=g.node(n.id);return [n.id,{x:p.x-110,y:p.y-60}]}));
  },[topology,mode]);
  useEffect(()=>{
    const sameMode = previousMode.current === mode;
    previousMode.current = mode;
    setNodes(previous=>{
      const old = new Map(previous.map(n=>[n.id,n]));
      return graph.nodes.map(n=>({id:n.id,type:'ontology',data:n,position:saved.current.get(mode+':'+n.id) || (sameMode ? old.get(n.id)?.position : undefined) || positions.get(n.id)!,selected:n.id===selected}));
    });
    setEdges(graph.edges.map(e=>({id:e.id,source:e.source,target:e.target,
      sourceHandle:mode==='process'&&positions.get(e.source)?.y!==positions.get(e.target)?.y?'bottom':'right',
      targetHandle:mode==='process'&&positions.get(e.source)?.y!==positions.get(e.target)?.y?'top':'left',type:'default',label:relations[e.label] || e.label,
      animated:false,style:{stroke: e.kind==='ontology' ? '#a592c7' : ['usage','trace'].includes(e.kind) ? '#7aa89c' : e.kind==='configuration' ? '#a8afd0' : '#afbeca',strokeWidth:1.3,strokeDasharray:['usage','trace','configuration'].includes(e.kind)?'5 4':undefined},
      labelStyle:{fill:'#8a94a7',fontSize:10},labelBgStyle:{fill:'#fafbfe'},labelBgPadding:[5,3] as [number,number],
      markerEnd:{type:MarkerType.ArrowClosed,width:12,height:12,color:'#a0afc3'}})));
  },[graph,positions,selected,mode,setNodes,setEdges]);
  return <div className={'lifecycle-canvas mode-'+mode}><ReactFlow key={mode} nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={(_,n)=>onSelect(n.data)} onNodeDragStop={(_,n)=>saved.current.set(mode+':'+n.id,n.position)}
      onEdgeClick={(_,e)=>{const source=graph.edges.find(r=>r.id===e.id)!;onSelect({id:e.id,kind:'relationship',label:relations[source.label]||source.label,stage:source.kind==='ontology'?'exploration':source.label==='CLASSIFIED_AS'?'alignment':'consumption',details:{source_name:graph.nodes.find(n=>n.id===source.source)?.label,target_name:graph.nodes.find(n=>n.id===source.target)?.label,relationship_kind:source.kind,assertions:source.details||[]}});}}
      fitView minZoom={.12} maxZoom={1.7} nodesConnectable={false} proOptions={{hideAttribution:true}}>
      <Background gap={22} color="#dce2ef"/><Controls showInteractive={false}/>{mode!=='process'&&<MiniMap pannable zoomable nodeColor={n=>colors[String(n.data.kind)] || '#9dacc2'}/>}
    </ReactFlow><div className="graph-hint">拖动与缩放 · 点击节点或连线查看依据</div></div>;
}
