import { useState } from 'react';
import { ChevronRight, Quote, Check, CircleCheck } from 'lucide-react';
import type { Finding } from './types';
const status: Record<string,string> = {open:'待处理',accepted:'已采纳',dismissed:'已驳回',outdated:'原文已更新'};
export function SourceText({text, findings}: {text:string;findings:Finding[]}) {
  const ranges=findings.filter(f=>f.status!=='accepted'&&text.includes(f.quote)).map(f=>({start:text.indexOf(f.quote),end:text.indexOf(f.quote)+f.quote.length})).sort((a,b)=>a.start-b.start);
  const segments=[];let offset=0;
  for(const range of ranges){if(range.start<offset)continue;segments.push(text.slice(offset,range.start));segments.push(<mark key={range.start}>{text.slice(range.start,range.end)}</mark>);offset=range.end;}
  segments.push(text.slice(offset));return <>{segments}</>;
}
export function FindingCard({finding, active, busy, onLocate, onApply, onDismiss}: {finding: Finding; active: boolean; busy: boolean; onLocate: (block: string, document?: string) => void; onApply: (id: string, text: string, category: string) => void; onDismiss: (id: string) => void}) {
  const [replacement, setReplacement] = useState(finding.replacement);
  const [edit, setEdit] = useState(false);
  const [category, setCategory] = useState(finding.category);
  return <article className={`finding ${active ? 'focused' : ''} ${finding.status === 'accepted' ? 'accepted' : ''}`}>
    <div className="finding-head"><span className={`category ${finding.category === '内容冲突' ? 'conflict' : ''}`}>{finding.category}</span><small>{status[finding.status]}</small></div>
    <button className="finding-title" onClick={() => onLocate(finding.block_id)}>{finding.title}<ChevronRight size={16}/></button>
    <blockquote onClick={() => onLocate(finding.block_id)}>{finding.quote}</blockquote>
    <p>{finding.explanation}</p>
    <details><summary><Quote size={14}/> 查看原文依据 · {finding.evidence.length}处</summary>
      {finding.evidence.map((e, i) => <button className="evidence-link" key={i} onClick={() => onLocate(e.block_id, e.document_id)}><b>{e.document_title}</b><small>{e.heading}</small><span>{e.quote}</span></button>)}
      {!finding.evidence.length && <p>未绑定到唯一的上游摘录，请结合下方召回原文人工核对。</p>}
    </details>
    {finding.status === 'open' ? <>
      <div className="revision-preview"><small>建议修改</small>{edit ? <><select aria-label="意见类别" value={category} onChange={e => setCategory(e.target.value)}>{['内容冲突','覆盖不足','证据不足','改进建议'].map(c => <option key={c}>{c}</option>)}</select><textarea value={replacement} onChange={e => setReplacement(e.target.value)}/></> : <p>{replacement || '需补充实际信息后形成修改文字。'}</p>}</div>
      {finding.editable === false && <small>此意见涉及多个单元格，请在文档中逐格修改。</small>}<div className="finding-actions"><button disabled={busy || !replacement.trim() || finding.editable === false} onClick={() => onApply(finding.id, replacement, category)}><Check size={14}/>采纳修改</button><button className="quiet" onClick={() => setEdit(!edit)}>编辑建议</button><button className="quiet" disabled={busy} onClick={() => onDismiss(finding.id)}>驳回</button></div>
    </> : finding.status === 'accepted' && <div className="accepted-note"><CircleCheck size={14}/>已生成修订版本，可下载合册</div>}
  </article>;
}
