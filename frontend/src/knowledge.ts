import type { GraphData, GraphEdge, GraphItem, GraphTrace } from './types';

export const relationNames:Record<string,string>={relatedTo:'关联',partOf:'属于',contains:'包含',CONTAINS:'包含条款',CLASSIFIED_AS:'归入概念'};
export const nodeKinds:Record<string,{label:string;color:string;radius:number}>={
  concept:{label:'概念',color:'#627fc6',radius:17},dimension:{label:'评审维度',color:'#9b79bd',radius:22},
  document:{label:'文档',color:'#4b9e99',radius:22},clause:{label:'条款',color:'#8eabc3',radius:11},
  standard:{label:'标准原文',color:'#c1a161',radius:15},input:{label:'输入',color:'#63a88c',radius:19},
  agent:{label:'智能体',color:'#6f8bae',radius:21},finding:{label:'意见',color:'#cf8b72',radius:16},
  evidence:{label:'引用',color:'#8dac80',radius:12},revision:{label:'修订',color:'#639e81',radius:16},
  configuration:{label:'评审配置',color:'#a68ab9',radius:21},record:{label:'运行记录',color:'#83a69c',radius:21},doctype:{label:'文档类型',color:'#89aaa8',radius:16},
};

export const layerNames:Record<string,string>={ontology:'领域本体',document:'文档与标准原文',configuration:'显式评审配置',runtime:'运行记录'};
export function nodeCaption(n:GraphItem){return n.document_name?`${n.document_name} · ${n.label}`:n.label;}

export function relationItem(edge:GraphEdge,graph:GraphData):GraphItem {
  const source=graph.nodes.find(n=>n.id===edge.source),target=graph.nodes.find(n=>n.id===edge.target);
  return {id:edge.id,kind:'relationship',label:relationNames[edge.label]||edge.label,
    stage:edge.kind==='ontology'?'exploration':edge.label==='CLASSIFIED_AS'||edge.kind==='aggregation'?'alignment':target?.stage||'consumption',
    layer:edge.kind==='ontology'?'ontology':edge.kind==='configuration'?'configuration':edge.kind==='usage'?'runtime':'document',
    details:{source_name:source?nodeCaption(source):'',target_name:target?nodeCaption(target):'',
      relationship_kind:edge.kind,assertions:edge.details||[],clause_ids:edge.clause_ids||[],
      provenance:edge.provenance,
      description:edge.kind==='aggregation'?`这是 ${edge.clause_ids?.length||0} 个真实挂接条款的显示汇总，展开文档或概念可查看各条原始关系；不是新增的本体关系。`:''}};
}

/** Display aggregation keeps the native clause IDs; it does not create ontology facts. */
export function ontologyNetwork(source:GraphData,expanded:Set<string>,allClauses=false):GraphData {
  const byId=new Map(source.nodes.map(n=>[n.id,n]));
  const documents=new Map(source.nodes.filter(n=>n.kind==='document').map(n=>[n.document_id,n]));
  const clauses=new Set<string>();
  for(const n of source.nodes)if(n.kind==='clause'&&(allClauses||expanded.has(n.id)||expanded.has(documents.get(n.document_id)?.id||'')))clauses.add(n.id);
  for(const e of source.edges)if(e.label==='CLASSIFIED_AS'&&expanded.has(e.target))clauses.add(e.source);
  const ids=new Set(source.nodes.filter(n=>['concept','dimension','document'].includes(n.kind)||clauses.has(n.id)).map(n=>n.id));
  for(const e of source.edges)if(expanded.has(e.source)&&['standard','doctype'].includes(byId.get(e.target)?.kind||''))ids.add(e.target);
  const aggregates=new Map<string,GraphEdge>();
  for(const e of source.edges){
    if(e.label!=='CLASSIFIED_AS'||clauses.has(e.source))continue;
    const clause=byId.get(e.source),doc=documents.get(clause?.document_id);
    if(!doc)continue;
    const key='aggregate:'+doc.id+':'+e.target;
    const value=aggregates.get(key)||{id:key,source:doc.id,target:e.target,label:'条款挂接汇总',kind:'aggregation',clause_ids:[]};
    if(!value.clause_ids!.includes(e.source))value.clause_ids!.push(e.source);
    aggregates.set(key,value);
  }
  const nodes=source.nodes.filter(n=>ids.has(n.id)).map(n=>{
    const count=n.kind==='document'?source.nodes.filter(c=>c.kind==='clause'&&c.document_id===n.document_id).length:
      n.kind==='concept'?source.edges.filter(e=>e.target===n.id&&e.label==='CLASSIFIED_AS').length:
      n.kind==='dimension'?source.edges.filter(e=>e.source===n.id&&['standard','doctype'].includes(byId.get(e.target)?.kind||'')).length:0;
    return {...n,details:{...n.details,expand_count:count}};
  });
  return {nodes,edges:[...source.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)),...aggregates.values()]};
}

export function traceNetwork(graph:GraphData,trace:GraphTrace|null,step:number):GraphData {
  if(!trace)return {nodes:[],edges:[]};
  const phase=trace.steps[step],nodes=new Set(phase.visible_node_ids),edges=new Set(phase.visible_edge_ids);
  return {nodes:graph.nodes.filter(n=>nodes.has(n.id)),edges:graph.edges.filter(e=>edges.has(e.id))};
}
