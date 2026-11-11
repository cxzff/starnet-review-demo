import asyncio
import json

from langchain_core.messages import AIMessageChunk

from starnet_review import core
from starnet_review.content import ensure_content_ontologies, dimension_context, version_key
from starnet_review.cycles import run_all
from starnet_review.ontology import read_profile
from starnet_review.planning import make_plan
from test_review import imported
from starnet_review.lifecycle import document_content_graph, standard_graph


class Model:
    def __init__(self, bad_quote=False):
        self.calls, self.prompts, self.bad_quote = 0, [], bad_quote

    def bind(self, **options):
        owner, name = self, options['response_format']['json_schema']['name']

        class Bound:
            async def astream(self, messages):
                owner.calls += 1
                owner.prompts.append(messages)
                if name == 'document_content':
                    text = json.loads(messages[1].content)['全文'][-1]['原文']
                    source = '原文不存在的内容' if owner.bad_quote else text
                    value = {'summary': '文档规定了响应时间。',
                             'concepts': [{'label': '项目特有响应指标', 'quote': source}, {'label': text, 'quote': source}],
                             'relations': [{'subject': '项目特有响应指标', 'relation': '规定或记录', 'object': text, 'quote': source}]}
                else:
                    value = {'summary': '已对照实际内容本体和原文。', 'findings': []}
                raw = json.dumps(value, ensure_ascii=False)
                for start in range(0, len(raw), 37):
                    yield AIMessageChunk(content=raw[start:start+37])
                yield AIMessageChunk(content='', usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
        return Bound()


def metrics():
    return {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}


def test_document_growth_is_source_bound_published_and_reused_across_dimensions(imported, monkeypatch):
    publications, events, model = [], [], Model()
    monkeypatch.setattr(core, 'publish_graph', lambda graph, uri: publications.append(graph) or
                        {'snapshot_id': 'grown', 'nodes': len(graph.nodes), 'edges': len(graph.edges)})

    async def emit(value):
        events.append(value)

    counted = metrics()
    asyncio.run(ensure_content_ontologies(imported, model, emit, counted))
    assert model.calls == len(imported['documents']) == counted['model_calls']
    assert len(publications) == 1
    graph = publications[0]
    assert any(n.kind == 'ContentConcept' and n.label == '项目特有响应指标' for n in graph.nodes)
    assert any(e.kind == 'CONTENT_RELATION' for e in graph.edges)
    assert all('响应时间' in e.evidence[0].excerpt for e in graph.edges if e.kind == 'MENTIONS')
    assert len({n.id for n in graph.nodes if n.kind == 'ContentConcept' and n.label == '项目特有响应指标'}) == 2
    asyncio.run(ensure_content_ontologies(imported, model, emit, counted))
    assert model.calls == 2 and len(publications) == 1
    assert any(e['kind'] == 'ontology_reused' for e in events)
    assert all('clause_id' not in p[1].content for p in model.prompts)


def test_unlocated_extraction_is_not_promoted_to_content_fact(imported):
    model = Model(bad_quote=True)

    async def emit(value):
        pass

    asyncio.run(ensure_content_ontologies(imported, model, emit, metrics()))
    for record in imported['content_ontologies'].values():
        assert record['unresolved']
        assert not record['grounded']['concepts'] and not record['grounded']['relations']
        assert not record['extraction']['concepts']


def test_one_document_with_many_blocks_still_has_one_task_per_perspective(imported):
    imported['documents'][0]['blocks'][-1]['text'] *= 2000
    plan = make_plan(imported)
    for role in plan['dimensions']:
        assert len(role['batches']) == bool(role['coverage'])
        assert [bid for c in role['coverage'] for bid in c['block_ids']] == [bid for b in role['batches'] for bid in b['target_ids']]
    quality = next(d for d in plan['dimensions'] if d['id'] == 'quality')
    assert len(quality['coverage']) == 1 and len(quality['batches']) == 1
    assert quality['coverage'][0]['document_id'] == imported['documents'][0]['id']


def test_shared_growth_and_one_call_per_dimension_are_accounted_separately(imported):
    model, events = Model(), []

    async def emit(value):
        events.append(value)

    cycle = asyncio.run(run_all(imported, emit, model_factory=lambda: model))
    assert cycle['status'] == 'completed'
    assert cycle['metrics']['construction_calls'] == 1
    assert cycle['metrics']['review_calls'] == cycle['applicable_count']
    assert model.calls == cycle['metrics']['model_calls'] == cycle['applicable_count'] + 1
    assert cycle['metrics']['total_tokens'] == model.calls * 15
    for role in cycle['dimensions']:
        if not role['coverage']:
            continue
        context = dimension_context(imported, role, role['coverage'], read_profile()['dependencies'])
        assert context['content_ontologies']
        assert all(p['edges'][0]['predicate'].endswith('规定或记录') for p in context['paths'])
    assert len(imported['content_ontologies']) == 1
    repeated = asyncio.run(run_all(imported, emit, model_factory=lambda: model))
    assert repeated['metrics']['construction_calls'] == 0


def test_opinion_binds_to_actual_document_in_multi_document_dimension(imported):
    first, second = imported['documents']
    block = {**second['blocks'][-1], 'document_id': second['id'], 'document_title': second['title'], 'source_sha256': second['sha256']}
    ctx = {'document': first, 'role': {'id': 'quality'}, 'target': [block], 'evidence': [], 'standards': [], 'paths': []}
    found, unresolved = core.bind_results(imported, ctx, {'findings': [{
        'quote': block['text'], 'title': '判定需核对', 'category': '内容冲突', 'explanation': '原文判据不一致',
        'evidence_quotes': [block['text']], 'replacement': ''}]}, 'offline')
    assert not unresolved and found[0]['document_id'] == second['id']
    assert found[0]['source_sha256'] == second['sha256']
    assert found[0]['evidence'][0]['document_id'] == second['id']


def test_saved_growth_is_visible_before_publication_without_runtime_nodes(imported):
    async def emit(value):
        pass
    asyncio.run(ensure_content_ontologies(imported, Model(), emit, metrics()))
    imported['graph'].pop('content_versions')
    graph = document_content_graph(imported)
    assert graph['nodes'] and graph['edges']
    assert all(n['kind'] == 'content' and not n['details']['published'] for n in graph['nodes'])
    assert all(n['layer'] == 'document' and n['details']['quote'] for n in graph['nodes'])
    assert not any(n['kind'] in ('agent', 'input', 'dimension') for n in graph['nodes'])
    ids = {n['id'] for n in graph['nodes']} | {b['clause_id'] for d in imported['documents'] for b in d['blocks']}
    assert all(e['source'] in ids and e['target'] in ids for e in graph['edges'])


def test_standard_tree_preserves_normative_wording_and_source(tmp_path, monkeypatch):
    source = tmp_path / 'standard.json'
    data = {'sections': [{'id': 'J', 'title': '需求说明', 'pdf_pages': [1], 'blocks': [], 'children': [
        {'id': 'J.3', 'title': '性能要求', 'pdf_pages': [2], 'blocks': [{'text': '应描述性能指标及适用条件。'}], 'children': []}]}]}
    source.write_text(json.dumps(data, ensure_ascii=False))
    original = source.read_bytes()
    monkeypatch.setenv('GJB_SOURCE_JSON', str(source))
    graph = standard_graph()
    assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
    assert graph['nodes'][1]['details']['text'] == '应描述性能指标及适用条件。'
    assert graph['nodes'][1]['details']['pages'] == [2]
    assert source.read_bytes() == original


def test_sequential_documents_keep_upstream_ontology_and_prior_reviews(imported, tmp_path, monkeypatch):
    from starnet_review import lifecycle as module
    model, events, publications = Model(), [], []
    monkeypatch.setattr(core, 'publish_graph', lambda graph, uri: publications.append(graph) or
                        {'snapshot_id': 'snapshot-' + str(len(publications)), 'nodes': len(graph.nodes), 'edges': len(graph.edges)})

    async def emit(value):
        events.append(value)

    requirements, report = imported['documents']
    first = asyncio.run(run_all(imported, emit, document_id=requirements['id'], model_factory=lambda: model))
    assert first['status'] == 'completed' and first['applicable_count'] == 3
    assert first['metrics']['construction_calls'] == 1
    assert all(r['document_id'] == requirements['id'] for r in imported['runs'])
    assert version_key(report) not in imported['content_ontologies']
    original = json.dumps(imported['content_ontologies'][version_key(requirements)], sort_keys=True)
    prior_ids = [r['id'] for r in imported['runs']]

    path = tmp_path / '设计说明书.md'
    path.write_text('# 软件设计说明书\n\n## 接口设计\n\n设计中的响应时间不超过2秒。\n')
    core.import_files([(path, path.name, 'sdd')], project=imported)
    design = imported['documents'][-1]
    assert imported['review_document_id'] == design['id']
    second = asyncio.run(run_all(imported, emit, model_factory=lambda: model))
    assert second['status'] == 'completed' and second['applicable_count'] == 2
    assert second['metrics']['construction_calls'] == 1
    assert [r['id'] for r in imported['runs'][:len(prior_ids)]] == prior_ids
    assert all(r['document_id'] == design['id'] for r in imported['runs'][len(prior_ids):])
    assert json.dumps(imported['content_ontologies'][version_key(requirements)], sort_keys=True) == original
    assert version_key(report) not in imported['content_ontologies']
    assert any(c['文档'] == requirements['title'] for c in imported['runs'][-1]['context']['content_ontologies'])

    monkeypatch.setattr(module, 'publish_profile', lambda *args: None)
    monkeypatch.setattr(module, 'read_snapshot', lambda *args: ([], []))
    view = module.lifecycle(imported, document_id=requirements['id'])
    assert view['cycle']['id'] == first['id'] and view['cycle_current']
    assert all(r['status'] in ('completed', 'not_applicable') for r in view['dimensions'])
    assert all(n.get('document_id') != design['id'] for n in view['ontology_graph']['nodes'])


def test_only_current_document_is_target_and_missing_upstream_ontology_is_explicit(imported):
    report = imported['documents'][1]
    async def emit(value):
        pass
    asyncio.run(ensure_content_ontologies(imported, Model(), emit, metrics(), document_ids=[report['id']]))
    plan = make_plan(imported, document_id=report['id'])
    role = next(r for r in plan['dimensions'] if r['id'] == 'testing')
    context = dimension_context(imported, role, role['coverage'], read_profile()['dependencies'])
    assert {b['document_id'] for b in context['target']} == {report['id']}
    assert len(context['content_ontologies']) == 1
    assert context['upstream_status'][0]['内容本体'] == '尚未建立，本轮仅提供已上传原文'
