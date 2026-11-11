"""A single perspective agent: retrieve existing paths, review once, bind opinions to source."""
import asyncio
import json
import os
import time
from typing import Literal, TypedDict

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.json import parse_partial_json
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from .core import bind_results, key, retrieve, save
from .ontology import configuration_snapshot, read_profile
from clausegraph.contracts import fingerprint


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str
    category: Literal['内容冲突', '覆盖不足', '证据不足', '改进建议']
    quote: str = Field(description='逐字复制一处原文文字，不包含文档名和章节标签；表格选择单个单元格内的文字')
    explanation: str
    evidence_quotes: list[str] = Field(description='从已提供的原文内容中逐字摘录依据；不包含文档名和展示标签')
    replacement: str


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str
    findings: list[Finding]


class ReviewState(TypedDict, total=False):
    context: dict
    result: dict


def create_model():
    return ChatOpenAI(model=os.getenv('LLM_MODEL_NAME', 'qwen3.8-flash'),
                      api_key=os.getenv('LLM_API_KEY', ''), base_url=os.getenv('LLM_API_BASE'),
                      temperature=0, max_retries=0, streaming=True, stream_usage=True,
                      extra_body={'enable_thinking': False},
                      http_client=httpx.Client(trust_env=False, timeout=90),
                      http_async_client=httpx.AsyncClient(trust_env=False, timeout=90))


def messages(context, focus):
    def render(blocks):
        return json.dumps([{'文档': b.get('document_title', context['document']['title']), '章节': b['heading'], '原文': b['text']}
                           for b in blocks], ensure_ascii=False)
    role = context['role']
    system = (f"你是{role['name']}，只评审{role['scope']}。用简体中文输出。"
              "依据给定原文与已有本体路径判断，不生成新关系。相关关系不等于已满足。"
              "区分同一对象和条件下的冲突、合理细化、覆盖不足与证据不足；材料一致时允许没有问题。"
              "本次审查内容一致性；明确标注的样例或待执行状态本身不算缺陷。仅措辞优化归为改进建议。"
              "quote和evidence_quotes逐字摘录，不填内部编号。每条意见定位在待审原文，解释具体依据。"
              "replacement仅给替换quote的局部文字；缺少事实时留空，不编造事实，不修改实测结果使其看似达标。"
              "summary先写简洁的内容评审分析，不能只是准备做什么。")
    paths = []
    for path in context['paths']:
        names = {n['id']: n['label'] for n in path['nodes']}
        paths.append({'nodes': list(names.values()), 'existing_relations': [
            {'source': names[r['source']], 'relation': r['predicate'].rsplit('/', 1)[-1], 'target': names[r['target']]}
            for r in path['edges']]})
    prompt = (f"本次视角：{role['scope']}\n用户关注：{focus or '按本视角正常评审'}\n"
              f"上游资料状态：{json.dumps(context.get('upstream_status', []), ensure_ascii=False)}\n"
              f"本项目已保存的文档内容本体（原文抽取候选，不是规范要求；须对照原文判断）：\n{json.dumps(context.get('content_ontologies', []), ensure_ascii=False)}\n\n"
              f"当前待审原文：\n{render(context['target'])}\n\n"
              f"已召回的上游及内部原文（这是检索范围，未召回不代表全文没有）：\n{render(context['evidence'])}\n\n"
              f"实际查询到的已有本体路径：\n{json.dumps(paths, ensure_ascii=False)}\n\n"
              f"适用标准摘录：\n{json.dumps(context['standards'], ensure_ascii=False)}")
    return [SystemMessage(content=system), HumanMessage(content=prompt)]


async def run_review(project, request, emit, model=None):
    started = time.perf_counter()
    profile = request.get('profile') or read_profile()
    role = request.get('role') or profile['dimensions'][request['perspective']]
    run = {'id': key(), 'perspective': request['perspective'], 'agent': role['name'],
           'review_scope': 'dimension' if request.get('coverage') else 'historical',
           'document_id': request['document_id'], 'section_id': request['section_id'], 'status': 'running',
           'cycle_id': request.get('cycle_id'), 'batch_id': request.get('batch_id'),
           'configuration': configuration_snapshot(profile, role),
           'events': [], 'summary': '', 'metrics': {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}}
    project['runs'].append(run)
    owned_model = model is None

    async def event(kind, **data):
        value = {'kind': kind, 'time': time.time(), 'run_id': run['id'], 'perspective': request['perspective'],
                 'batch_id': request.get('batch_id'), **data}
        if data.get('stage'):
            run['stage'] = data['stage']
        if kind not in ('text', 'done'):
            run['events'].append({k: v for k, v in value.items() if k not in ('evidence', 'paths', 'findings', 'standards')})
        await emit(value)

    async def prepare(state):
        await event('stage', stage='retrieval', title='召回本体节点与原文', detail='查询当前快照中的上下游及文档内部证据')
        if request.get('coverage'):
            from .content import dimension_context
            context = await asyncio.to_thread(dimension_context, project, role, request['coverage'], request['dependencies'])
        else:
            options = ({'target_ids': request['target_ids'], 'role': role, 'dependencies': request['dependencies']}
                       if request.get('target_ids') is not None else {})
            context = await asyncio.to_thread(retrieve, project, request['document_id'], request['section_id'], request['perspective'], **options)
        run['context'] = {k: context[k] for k in ['paths', 'evidence', 'standards', 'snapshot_id']}
        run['context']['target'] = context['target']
        run['context']['retrieval'] = context.get('retrieval', {})
        run['context']['content_ontologies'] = context.get('content_ontologies', [])
        await event('context', stage='exploration', paths=context['paths'], evidence=context['evidence'], standards=context['standards'],
                    snapshot_id=context['snapshot_id'],
                    title=f"召回 {len(context['evidence'])} 处原文、{len(context['paths'])} 条已有关系路径")
        save(project)
        return {'context': context}

    async def review(state):
        nonlocal model
        context = state['context']
        await event('stage', stage='reasoning', title='对照已有关系进行约束判断', detail=f"{run['agent']}独立评审，不生成新本体关系")
        model = model or create_model()
        bound = model.bind(response_format={'type': 'json_schema', 'json_schema': {
            'name': 'source_review', 'strict': True, 'schema': ReviewOutput.model_json_schema()}})
        raw, summary = '', ''
        input_messages = messages(context, request.get('focus', ''))
        run['input_manifest'] = {
            'snapshot_id': context['snapshot_id'], 'dimension_uri': role.get('uri'),
            'target_ids': [b['id'] for b in context['target']],
            'evidence_ids': [b['id'] for b in context['evidence']],
            'path_ids': [p.get('id') for p in context['paths']],
            'concept_ids': sorted({n['id'] for p in context['paths'] for n in p['nodes'] if n['id'].startswith(('type:', 'content:'))}),
            'standard_clauses': [s['number'] for s in context['standards']],
            'prompt_sha256': fingerprint([m.content for m in input_messages]),
        }
        save(project)
        await event('input', stage='reasoning', title='本体路径与原文已装配为模型输入', manifest=run['input_manifest'])
        run['metrics']['model_calls'] += 1
        async for chunk in bound.astream(input_messages):
            if isinstance(chunk.content, str):
                raw += chunk.content
                try:
                    partial = parse_partial_json(raw)
                except (ValueError, TypeError):
                    partial = None
                text = partial.get('summary', '') if isinstance(partial, dict) else ''
                if text and text != summary:
                    summary = text
                    run['summary'] = summary
                    await event('text', text=summary)
            if chunk.usage_metadata:
                for field in ['input_tokens', 'output_tokens']:
                    run['metrics'][field] += chunk.usage_metadata.get(field, 0)
        result = ReviewOutput.model_validate_json(raw).model_dump()
        return {'result': result}

    async def locate(state):
        findings, unmatched = bind_results(project, state['context'], state['result'], run['id'])
        run['summary'] = state['result']['summary']
        run['findings'] = [f['id'] for f in findings]
        run['unlocated'] = unmatched
        for finding in findings:
            finding['cycle_id'] = request.get('cycle_id')
        await event('opinions', stage='consumption', findings=findings, unlocated_count=len(unmatched),
                    title=f"已定位 {len(findings)} 条评审意见" if findings else '本次未发现可定位的问题')
        if unmatched:
            await event('notice', title=f'{len(unmatched)} 条意见的摘录未能唯一定位，保留在执行记录中，未执行修改')
        return {}

    graph = StateGraph(ReviewState)
    graph.add_node('retrieve', prepare)
    graph.add_node('review', review)
    graph.add_node('locate', locate)
    graph.add_edge(START, 'retrieve')
    graph.add_edge('retrieve', 'review')
    graph.add_edge('review', 'locate')
    graph.add_edge('locate', END)
    try:
        await graph.compile().ainvoke({})
        run['status'] = 'completed'
    except asyncio.CancelledError:
        run['status'] = 'stopped'
        raise
    except Exception as exc:
        run['status'] = 'failed'
        run['error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
        await event('error', message=run['error'])
    finally:
        run['metrics']['elapsed_seconds'] = round(time.perf_counter() - started, 2)
        run['metrics']['total_tokens'] = run['metrics']['input_tokens'] + run['metrics']['output_tokens']
        save(project)
        if owned_model and model is not None:
            await model.http_async_client.aclose()
            model.http_client.close()
    await event('done', run=run)
    return run
