import { ArrowRight, FileText, Network, Quote } from 'lucide-react';
import type { GraphItem } from './types';
import { layerNames } from './knowledge';

function asText(value:unknown) { return typeof value==='string' ? value : ''; }
const stageLabels:Record<string,string>={ontology:'本体加载',alignment:'条款挂接',retrieval:'节点与原文召回',exploration:'已有关系探索',reasoning:'约束判断',consumption:'意见引用与定位',revision:'采纳与版本更新'};
const relationshipDescriptions:Record<string,string>={content:'来自当前文档原文明示内容的抽取候选，保留出处，语义待核查。',ontology:'来自领域本体已经声明的关系，不是模型推断的新关系。',configuration:'来自已保存的显式评审配置，不表示已建立标准与项目条款的语义约束。',structure:'来自文档结构或规则挂接，不等于领域概念之间的本体关系。',aggregation:'这是原始挂接边的显示汇总，可展开查看对应条款。',usage:'这是已保存的运行输入或引用记录，不是本体语义关系。'};

export function GraphInspector({item,onLocate}: {item:GraphItem|null;onLocate:(block:string,document?:string)=>void}) {
  if(!item)return <div className="inspector-empty"><Network size={32}/><h3>点击概念、条款或关系</h3><p>查看定义和关系来源，双击概念或文档可展开关联条款。“评审动态”可回放本体被使用的真实轨迹。</p></div>;
  const d=item.details || {};
  const array=(value:unknown)=>Array.isArray(value)?value:[];
  const manifest=d.manifest && typeof d.manifest==='object' ? d.manifest as Record<string,unknown> : null;
  const provenance=d.provenance&&typeof d.provenance==='object'?d.provenance as Record<string,unknown>:null;
  return <div className="graph-inspector-content"><span className="eyebrow">{layerNames[item.layer||'']||'节点详情'} · {stageLabels[item.stage]||item.stage}</span><h2>{item.label}</h2>{item.subtitle&&<p className="inspector-subtitle">{item.subtitle}</p>}
    {item.document_title&&<div className="inspector-section"><b>来源文档</b><p>{item.document_title}</p>{asText(d.source_file)&&<small>{asText(d.source_file)}</small>}</div>}
    {asText(d.scope)&&<div className="inspector-section"><b>本体规定的评审范围</b><p>{asText(d.scope)}</p><p>覆盖 {Number(d.block_count || 0)} 个内容块，安排 {array(d.batches).length} 个维度任务。</p><p>当前直接挂接命中 {Number(d.matched_clauses || 0)} 个条款；文档范围按评审本体声明执行。</p></div>}
    {asText(d.description)&&<p>{asText(d.description)}</p>}
    {item.kind==='stage'&&typeof d.completed==='number'&&<p>本轮已完成 {d.completed} / {Number(d.total)} 个维度任务。</p>}
    {asText(d.source_name)&&<div className="inspector-section"><b>关系方向</b><p>{asText(d.source_name)} → {item.label} → {asText(d.target_name)}</p><small>{relationshipDescriptions[asText(d.relationship_kind)]}</small></div>}
    {provenance&&<div className="inspector-section"><b>可追溯记录</b><code>{asText(provenance.source)}</code>{asText(provenance.number)&&<p>标准条款 {asText(provenance.number)}</p>}{asText(provenance.quote)&&<blockquote>{asText(provenance.quote)}</blockquote>}<small>{asText(d.relationship_kind)==='content'?'这是带原文出处的内容抽取候选，不能自动视为已确认事实。':'这是配置、输入或引用记录，不表示标准与项目条款存在已建模的语义约束。'}</small></div>}
    {array(d.assertions).map((a,i)=><div className="inspector-section" key={i}><b>挂接依据 · {a.route==='route2'?'ClauseGraph 规则匹配':a.route}</b>{a.details?.rule_score!==undefined&&<p>匹配分数 {a.details.rule_score} · {a.details.review_status==='review_required'?'候选挂接，待人工确认':a.details.review_status||'规则已匹配'}</p>}{a.details?.matched_cues&&Object.entries(a.details.matched_cues).map(([k,v])=><p key={k}>{({body:'正文命中词',header:'标题命中词',identifier:'编号命中词'} as Record<string,string>)[k]||k}：{(v as string[]).join('、')}</p>)}<small>匹配分数反映规则命中，不代表内容已满足要求。</small></div>)}
    {asText(d.source)&&<div className="inspector-section"><b>来源</b><p>{asText(d.source)}</p></div>}
    {asText(d.filename)&&<div className="inspector-section"><b>本体文件</b><p>{asText(d.filename)}</p><p>版本 {asText(d.version)}</p></div>}
    {!!array(d.concept_uris).length&&<div className="inspector-section"><b>关联概念</b>{array(d.concept_uris).map((v,i)=><code key={i}>{String(v).split('/').at(-1)}</code>)}</div>}
    {!!array(d.standards).length&&<div className="inspector-section"><b>适用标准</b>{array(d.standards).map((s,i)=>typeof s==='string'?<code key={i}>{s}</code>:<details className="inspector-source" key={i}><summary>{s.number} {s.title}</summary><p>{s.text}</p><small>原 PDF 第 {(s.pages||[]).join('、')} 页</small></details>)}</div>}
    {manifest&&<div className="inspector-section"><b>实际提交模型的输入清单</b><dl><dt>待审内容</dt><dd>{array(manifest.target_ids).length} 块</dd><dt>召回原文</dt><dd>{array(manifest.evidence_ids).length} 块</dd><dt>已有路径</dt><dd>{array(manifest.path_ids).length} 条</dd><dt>标准条款</dt><dd>{array(manifest.standard_clauses).join('、') || '无'}</dd></dl><small>“送入输入”与“被意见引用”分开记录，不把召回结果自动当作已采用的依据。</small></div>}
    {d.historical===true&&<p className="trace-note">这份旧记录保存了上下文，未保存新版完整输入清单；没有补造输入哈希或调用记录。</p>}
    {['target','evidence'].map(key=>!!array(d[key]).length&&<div className="inspector-section" key={key}><b>{key==='target'?'实际待审原文':'实际召回原文'} · {array(d[key]).length} 块</b>{array(d[key]).map((b,i)=><details className="inspector-source" key={i}><summary>{b.document_title || '待审文档'} · {b.heading || '文档说明'}</summary><p>{b.text}</p>{b.document_id&&<button className="text-button" onClick={()=>onLocate(b.id,b.document_id)}>在原文中查看</button>}</details>)}</div>)}
    {!!array(d.paths).length&&<div className="inspector-section"><b>实际查询到的已有路径</b>{array(d.paths).map((path,i)=><details className="inspector-source" key={i}><summary>路径 {i+1} · {path.nodes.map((n:{label:string})=>n.label).join(' → ')}</summary>{path.edges.map((e:{source:string;predicate:string;target:string},j:number)=>{const names=Object.fromEntries(path.nodes.map((n:{id:string;label:string})=>[n.id,n.label]));return <p key={j}>{names[e.source]} → {e.predicate.split('/').at(-1)} → {names[e.target]}</p>;})}</details>)}</div>}
    {asText(d.summary)&&<div className="inspector-section"><b>实际评审分析</b><p className="inspector-analysis">{asText(d.summary)}</p></div>}
    {asText(d.quote)&&<div className="inspector-section"><b><Quote size={13}/> 原文摘录</b><blockquote>{asText(d.quote)}</blockquote></div>}
    {asText(d.explanation)&&<div className="inspector-section"><b>意见的判断依据</b><p>{asText(d.explanation)}</p><small>引用 {array(d.evidence).length} 处原文，关联 {array(d.path_ids).length} 条已有路径。</small></div>}
    {!!array(d.blocks).length&&<div className="inspector-section"><b>条款中的原文</b>{array(d.blocks).map((b,i)=><p className="inspector-excerpt" key={i}>{String(b.text)}</p>)}</div>}
    {asText(d.text)&&<div className="inspector-section"><b>标准原文</b><p className="inspector-analysis">{asText(d.text)}</p>{!!array(d.pages).length&&<small>原 PDF 第 {array(d.pages).join('、')} 页</small>}</div>}
    {asText(d.replacement)&&<div className="inspector-section"><b>{item.status==='accepted'?'采纳后的文字':'建议替换文字'}</b><p>{asText(d.replacement)}</p><small>由用户确认采纳后应用到副本，原件保留。</small></div>}
    {asText(d.before_snapshot)&&<div className="inspector-section"><b>版本更新链</b><code>{asText(d.before_snapshot).slice(0,12)}</code><ArrowRight size={14}/><code>{asText(d.after_snapshot).slice(0,12) || '尚未发布'}</code></div>}
    {item.block_id&&item.document_id&&<button className="secondary" onClick={()=>onLocate(item.block_id!,item.document_id)}><FileText size={14}/>在原文中查看</button>}
    <details className="inspector-identifiers"><summary>溯源标识</summary><code>{item.id}</code>{asText(d.uri)&&<code>{asText(d.uri)}</code>}{asText(d.snapshot_id)&&<code>快照 {asText(d.snapshot_id)}</code>}{asText(d.sha256)&&<code>SHA256 {asText(d.sha256)}</code>}{manifest&&<code>输入 SHA256 {asText(manifest.prompt_sha256)}</code>}</details>
  </div>;
}
