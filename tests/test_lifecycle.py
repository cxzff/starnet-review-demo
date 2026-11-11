import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessageChunk

from starnet_review import core
from starnet_review.cycles import run_all
from starnet_review.lifecycle import consumption_graph, usage_trace
from starnet_review.ontology import configuration_snapshot, read_profile
from starnet_review.planning import make_plan
from test_review import imported, context


def test_dimensions_are_read_from_ontology_not_fixed_in_code(tmp_path):
    original = Path('resources/review-ontology.ttl').read_text()
    modified = original.replace('rv:dimension review:requirements,', 'rv:dimension review:extra, review:requirements,')
    modified += '''
review:extra a rv:ReviewDimension ; rdfs:label "新增评审维度"@zh ; rv:order 6 ;
  rv:agentName "新增视角智能体" ; rv:scope "自定义的独立视角" ;
  rv:reviewsConcept se:softwareRequirement ; rv:targetDocumentType review:srs ; rv:selection "document" .
'''
    path = tmp_path / 'profile.ttl'
    path.write_text(modified)
    profile = read_profile(path)
    assert len(profile['dimensions']) == 6
    assert profile['dimensions']['extra']['label'] == '新增评审维度'
    assert profile['dependencies']['str'] == ['sdd', 'srs', 'std', 'stp']


def test_plan_covers_every_selected_block_once_even_when_batched(imported):
    plan = make_plan(imported, character_limit=35)
    assert plan['dimension_count'] == len(read_profile()['dimensions'])
    for role in plan['dimensions']:
        expected = [bid for item in role['coverage'] for bid in item['block_ids']]
        actual = [bid for batch in role['batches'] for bid in batch['target_ids']]
        assert expected == actual
        assert len(actual) == len(set(actual))
    assert next(d for d in plan['dimensions'] if d['id'] == 'design')['status'] == 'not_applicable'
    assert next(d for d in plan['dimensions'] if d['id'] == 'testing')['status'] == 'pending'


class OfflineModel:
    def __init__(self):
        self.calls = 0

    def bind(self, **kwargs):
        return self

    async def astream(self, messages):
        self.calls += 1
        await asyncio.sleep(0)
        yield AIMessageChunk(content=json.dumps({'summary': '离线模拟：完成当前视角数据流验证。', 'findings': []}, ensure_ascii=False))
        yield AIMessageChunk(content='', usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})


def test_all_dimensions_run_independently_with_no_extra_summary_calls(imported, monkeypatch):
    from starnet_review import agent

    def retrieve(project, document_id, section, perspective, **options):
        document = next(d for d in project['documents'] if d['id'] == document_id)
        return {'document': document, 'target': [b for b in document['blocks'] if b['id'] in options['target_ids']],
                'role': options['role'], 'paths': [], 'evidence': [], 'standards': [], 'snapshot_id': 'test'}

    monkeypatch.setattr(agent, 'retrieve', retrieve)
    model, events = OfflineModel(), []

    async def emit(value):
        events.append(value)

    cycle = asyncio.run(run_all(imported, emit, model_factory=lambda: model))
    assert cycle['status'] == 'completed'
    assert model.calls == cycle['batch_count'] == cycle['metrics']['model_calls']
    assert cycle['metrics']['total_tokens'] == 15 * model.calls
    assert all(d['status'] in ('completed', 'not_applicable') for d in cycle['dimensions'])
    assert all(r['input_manifest']['dimension_uri'] for r in imported['runs'])
    assert all(r['configuration']['profile']['id'] == cycle['profile']['id'] for r in imported['runs'])
    assert events[-1]['kind'] == 'cycle_done'
    json.dumps(imported)  # Neither run nor cycle events may contain self-references.


def test_usage_graph_does_not_label_injected_context_as_a_cited_fact(imported):
    profile = read_profile()
    ctx = context(imported)
    run = {'id': 'record', 'perspective': 'testing', 'agent': '测试评审智能体', 'status': 'completed',
           'context': {**ctx, 'paths': []}, 'summary': '已查看上下文', 'metrics': {'model_calls': 1}}
    graph = consumption_graph(imported, run, profile)
    assert any(n['kind'] == 'input' for n in graph['nodes'])
    assert not any(e['label'] == '意见实际引用' for e in graph['edges'])
    assert not any(e['kind'] == 'ontology' for e in graph['edges'])
    assert not any(e['label'] == '本体规定范围' for e in graph['edges'])


def test_clause_matching_is_distinct_from_declared_ontology_relationships(imported):
    ctx = context(imported)
    ctx['paths'] = [{'nodes': [{'id': 'clause:a', 'label': '原文'}, {'id': 'type:a', 'label': '需求'},
                             {'id': 'type:b', 'label': '测试'}],
                     'edges': [{'source': 'clause:a', 'target': 'type:a', 'predicate': 'CLASSIFIED_AS'},
                               {'source': 'type:a', 'target': 'type:b', 'predicate': 'https://example.org/relatedTo'}],
                     'target': 'clause:a', 'evidence': 'clause:a'}]
    run = {'id': 'record', 'perspective': 'testing', 'agent': '测试评审智能体', 'status': 'completed',
           'context': ctx, 'summary': '', 'metrics': {}}
    graph = consumption_graph(imported, run, read_profile())
    assert next(e for e in graph['edges'] if e['label'] == 'CLASSIFIED_AS')['kind'] == 'structure'
    assert next(e for e in graph['edges'] if e['label'] == 'relatedTo')['kind'] == 'ontology'


def test_playback_only_reveals_nodes_and_edges_from_the_saved_record(imported):
    ctx = context(imported)
    ctx['paths'] = [{'nodes': [{'id': 'clause:a', 'label': '原文'}, {'id': 'type:a', 'label': '需求'},
                             {'id': 'type:b', 'label': '测试'}],
                     'edges': [{'source': 'clause:a', 'target': 'type:a', 'predicate': 'CLASSIFIED_AS'},
                               {'source': 'type:a', 'target': 'type:b', 'predicate': 'https://example.org/relatedTo'}],
                     'target': 'clause:a', 'evidence': 'clause:a'}]
    run = {'id': 'playback', 'perspective': 'testing', 'agent': '测试评审智能体', 'status': 'completed',
           'context': ctx, 'summary': '保存的分析', 'metrics': {}}
    graph = consumption_graph(imported, run, read_profile())
    trace = usage_trace(run, graph)
    ids = {n['id'] for n in graph['nodes']}
    assert trace['max_step'] == 5  # No adoption is invented.
    assert 'input:playback' not in trace['steps'][0]['visible_node_ids']
    assert 'input:playback' in trace['steps'][4]['visible_node_ids']
    for step in trace['steps']:
        assert set(step['visible_node_ids']) <= ids
        for edge in graph['edges']:
            if edge['id'] in step['visible_edge_ids']:
                assert {edge['source'], edge['target']} <= set(step['visible_node_ids'])
    explored = trace['steps'][3]['active_edge_ids']
    assert any(e['id'] in explored and e['label'] == 'relatedTo' for e in graph['edges'])
    assert not any(e['id'] in explored and e['label'] == 'CLASSIFIED_AS' for e in graph['edges'])
    assert any(e['id'] in trace['steps'][4]['active_edge_ids'] and e['label'] == 'relatedTo' for e in graph['edges'])


def test_failed_retrieval_does_not_create_fake_model_input_nodes(imported):
    run = {'id': 'failed', 'perspective': 'testing', 'agent': '测试评审智能体', 'status': 'failed',
           'stage': 'retrieval', 'summary': '', 'metrics': {}, 'error': '查询未完成'}
    graph = consumption_graph(imported, run, read_profile())
    trace = usage_trace(run, graph)
    assert trace['max_step'] == 2
    assert not any(n['kind'] in ('input', 'finding', 'revision') for n in graph['nodes'])


def standard_context(project):
    return {**context(project), 'standards': [
        {'number': 'M.4', 'title': '标准测试条款', 'text': '判据应清晰且可复核。', 'pages': [99]},
        {'number': 'N.3', 'title': '标准报告条款', 'text': '结论应由实际记录支持。', 'pages': [100]}]}


def historical_run(ctx):
    return {'id': 'standards-record', 'perspective': 'testing', 'agent': '测试评审智能体',
            'document_id': ctx['document']['id'], 'status': 'completed', 'context': ctx,
            'summary': '分析中提到了 M.4 与 N.3，但提及不等于绑定了引用。', 'metrics': {'model_calls': 1}}


def test_legacy_standards_show_saved_input_source_without_fabricating_configuration(imported):
    run = historical_run(standard_context(imported))
    graph = consumption_graph(imported, run, read_profile())
    assert not any(n['kind'] in ('retrieval', 'configuration') for n in graph['nodes'])
    assert not any(n['label'] == '本次原文召回' for n in graph['nodes'])
    assert any(n['kind'] == 'record' and n['layer'] == 'runtime' for n in graph['nodes'])
    assert not any(e['kind'] == 'configuration' for e in graph['edges'])
    trace = usage_trace(run, graph)
    assert trace['stats']['standards'] == 2 and trace['stats']['cited_standards'] == 0
    assert trace['stats']['configuration_saved'] is False
    visible_edges = [e for e in graph['edges'] if e['id'] in trace['steps'][3]['visible_edge_ids']]
    for standard in [n for n in graph['nodes'] if n['kind'] == 'standard']:
        assert any(e['target'] == standard['id'] and e['label'] == '记录保存的标准摘录' for e in visible_edges)


def test_standard_configuration_uses_frozen_record_and_only_declared_references(imported):
    original = read_profile()
    frozen = configuration_snapshot(original, original['dimensions']['testing'])
    frozen['dimension']['standards'] = ['M.4']
    run = {**historical_run(standard_context(imported)), 'configuration': frozen}
    current = read_profile()
    current['id'], current['filename'] = 'different-current-version', 'new-current.ttl'
    current['dimensions']['testing']['standards'] = []
    graph = consumption_graph(imported, run, current)
    config = next(n for n in graph['nodes'] if n['kind'] == 'configuration')
    assert config['details']['sha256'] == original['id']
    assert config['details']['filename'] == original['filename']
    selected = [e for e in graph['edges'] if e['label'] == '配置选取的标准']
    assert [e['target'] for e in selected] == ['standard:M.4']
    assert any(e['target'] == 'standard:N.3' and e['label'] == '记录保存的标准摘录' for e in graph['edges'])
    assert not any(e['kind'] == 'ontology' and e['source'].startswith('standard:') for e in graph['edges'])
    assert usage_trace(run, graph)['stats']['configuration_saved'] is True


def test_standard_citation_edge_requires_bound_evidence_not_a_number_in_the_explanation(imported):
    ctx = standard_context(imported)
    run = historical_run(ctx)
    core.bind_results(imported, ctx, {'summary': '检查标准', 'findings': [{
        'title': '核对测试判据', 'category': '证据不足', 'quote': '判定通过',
        'explanation': '根据 M.4 与 N.3 检查，但仅引用 M.4。',
        'evidence_quotes': ['判据应清晰且可复核。'], 'replacement': ''}]}, run['id'])
    graph = consumption_graph(imported, run, read_profile())
    citations = [e for e in graph['edges'] if e['label'] == '意见实际引用标准']
    assert len(citations) == 1 and citations[0]['source'] == 'standard:M.4'
    assert citations[0]['kind'] == 'usage'
    assert citations[0]['provenance']['quote'] == '判据应清晰且可复核。'
    assert usage_trace(run, graph)['stats']['cited_standards'] == 1
    assert not any(n['kind'] == 'evidence' and n.get('document_id') == 'standard' for n in graph['nodes'])


def test_same_section_number_is_identified_by_its_source_document(imported):
    ctx = context(imported)
    source, target = imported['documents']
    a, b = source['blocks'][-1]['clause_id'], target['blocks'][-1]['clause_id']
    ctx['paths'] = [{'target': b, 'evidence': a,
                     'nodes': [{'id': a, 'label': '5.1 同名章节'}, {'id': 'type:test', 'label': '测试'},
                               {'id': b, 'label': '5.1 同名章节'}],
                     'edges': [{'source': a, 'target': 'type:test', 'predicate': 'CLASSIFIED_AS'},
                               {'source': b, 'target': 'type:test', 'predicate': 'CLASSIFIED_AS'}]}]
    graph = consumption_graph(imported, historical_run(ctx), read_profile())
    chapters = [n for n in graph['nodes'] if n['kind'] == 'clause']
    assert {n['document_id'] for n in chapters} == {source['id'], target['id']}
    assert len({n['document_name'] for n in chapters}) == 2
    assert all(n['label'] == '5.1 同名章节' and n['layer'] == 'document' for n in chapters)
