import asyncio
import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from langchain_core.messages import AIMessageChunk

from starnet_review import agent, core
from starnet_review.revisions import accept, patch_docx

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def document(path):
    with ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', f'''<w:document xmlns:w="{W}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14"><w:body>
          <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第1部分 软件需求规格说明书</w:t></w:r></w:p>
          <w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>性能需求</w:t></w:r></w:p>
          <w:p><w:r><w:rPr><w:b/></w:rPr><w:t>响应时间应不超过</w:t></w:r><w:r><w:t>2秒。</w:t></w:r></w:p>
          <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第2部分 集成测试报告</w:t></w:r></w:p>
          <w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>性能测试</w:t></w:r></w:p>
          <w:p><w:r><w:t>相同条件下响应时间为3秒，判定通过。</w:t></w:r></w:p>
        </w:body></w:document>''')
        z.writestr('word/styles.xml', '<styles/>')
        z.writestr('keep.bin', b'unchanged-package-part')


@pytest.fixture
def imported(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path / 'data')
    import starnet_review.revisions as revisions
    monkeypatch.setattr(revisions, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(core, 'publish_graph', lambda graph, uri: {'snapshot_id': 'test', 'nodes': len(graph.nodes), 'edges': len(graph.edges)})
    source = tmp_path / 'sample.docx'
    document(source)
    return core.import_files([(source, source.name, None)])


def test_combined_document_keeps_original_source_positions(imported):
    first, second = imported['documents']
    assert first['type'] == 'srs' and second['type'] == 'str'
    assert first['blocks'][-1]['location']['start'] == 3
    assert second['blocks'][-1]['location']['start'] == 6
    assert 'srs' in core.UPSTREAM['str']
    assert imported['graph']['engine'] == 'ClauseGraph'
    assert imported['graph']['relations']


def test_quote_binding_never_guesses_identifiers_or_ambiguous_text():
    blocks = [{'id': 'real', 'text': '实测响应时间为3秒。'}]
    assert core.bind_quote('响应时间为3秒', blocks)['id'] == 'real'
    assert core.bind_quote('响应时间为2秒', blocks) is None
    assert core.bind_quote('响应时间', [*blocks, {'id': 'other', 'text': '响应时间要求'}]) is None


def context(project):
    source, target = project['documents']
    return {'document': target, 'target': target['blocks'], 'role': core.ROLES['testing'], 'paths': [], 'standards': [],
            'evidence': [{**b, 'document_id': source['id'], 'document_title': source['title']} for b in source['blocks']]}


def output():
    return {'summary': '测试实测3秒与需求不超过2秒不一致，当前结果不能支持通过。', 'findings': [{
        'title': '测试通过判定与上游要求不一致', 'category': '内容冲突', 'quote': '判定通过',
        'explanation': '需求不超过2秒，实测3秒。', 'evidence_quotes': ['响应时间应不超过2秒。'],
        'replacement': '本次结果未达到要求，应整改后复测'}]}


def test_bind_and_accept_changes_only_selected_text_in_copy(imported):
    ctx = context(imported)
    findings, unmatched = core.bind_results(imported, ctx, output(), 'offline-run')
    assert len(findings) == 1 and not unmatched
    assert findings[0]['evidence'][0]['document_id'] == imported['documents'][0]['id']
    path = core.DATA / imported['id'] / ctx['document']['source_file']
    original = path.read_bytes()
    project = accept(imported, findings[0]['id'], findings[0]['replacement'])
    assert path.read_bytes() == original
    revised = core.DATA / project['id'] / project['documents'][1]['source_file']
    assert revised != path
    with ZipFile(revised) as z:
        xml = z.read('word/document.xml').decode()
        assert '响应时间为3秒' in xml and '本次结果未达到要求' in xml
        assert 'mc:Ignorable="w14"' in xml and 'xmlns:w14=' in xml
        assert z.read('keep.bin') == b'unchanged-package-part'
    assert project['findings'][0]['status'] == 'accepted'
    assert len(project['revisions']) == 1


def test_docx_replacement_across_runs_preserves_run_properties(imported):
    doc = imported['documents'][0]
    block = doc['blocks'][-1]
    path = core.DATA / imported['id'] / doc['source_file']
    patched = patch_docx(path.read_bytes(), block, '不超过2秒', '小于等于2秒')
    with ZipFile(BytesIO(patched)) as z:
        text = z.read('word/document.xml').decode()
        assert '<w:b/>' in text
        assert '小于等于2秒' in text


class FakeModel:
    def __init__(self):
        self.calls = 0
        self.prompts = []

    def bind(self, **kwargs):
        assert kwargs['response_format']['json_schema']['strict']
        return self

    async def astream(self, messages):
        self.calls += 1
        self.prompts.append(messages)
        raw = json.dumps(output(), ensure_ascii=False)
        for i in range(0, len(raw), 18):
            yield AIMessageChunk(content=raw[i:i+18])
        yield AIMessageChunk(content='', usage_metadata={'input_tokens': 200, 'output_tokens': 80, 'total_tokens': 280})


def test_single_perspective_graph_streams_and_uses_one_model_call(imported, monkeypatch):
    monkeypatch.setattr(agent, 'retrieve', lambda *args: {**context(imported), 'snapshot_id': 'test'})
    model, events = FakeModel(), []

    async def emit(value):
        events.append(value)

    request = {'document_id': imported['documents'][1]['id'], 'section_id': 'selected', 'perspective': 'testing', 'focus': ''}
    run = asyncio.run(agent.run_review(imported, request, emit, model))
    assert run['status'] == 'completed'
    assert model.calls == 1 and run['metrics']['total_tokens'] == 280
    assert any(e['kind'] == 'text' for e in events)
    assert events[-1]['kind'] == 'done'
    assert '不生成新关系' in model.prompts[0][0].content
    assert 'source_sha256' not in model.prompts[0][1].content


def test_unmatched_opinion_is_retained_without_fabricating_source(imported):
    value = output()
    value['findings'][0]['quote'] = '原文不存在的句子'
    findings, unmatched = core.bind_results(imported, context(imported), value, 'offline-run')
    assert not findings and len(unmatched) == 1


def test_all_presented_sources_can_be_bound_including_target_and_display_entry(imported):
    ctx = context(imported)
    value = output()
    source = ctx['evidence'][-1]
    target = ctx['target'][-1]
    value['findings'][0]['evidence_quotes'] = [
        f"【{source['document_title']} / {source['heading']}】\n{source['text']}", target['text']]
    findings, _ = core.bind_results(imported, ctx, value, 'offline-run')
    assert len(findings[0]['evidence']) == 2
    assert findings[0]['evidence'][0]['quote'] == source['text']
    assert findings[0]['evidence'][1]['block_id'] == target['id']


def test_independent_file_type_comes_from_original_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(core, 'publish_graph', lambda graph, uri: {'snapshot_id': 'test', 'nodes': len(graph.nodes), 'edges': len(graph.edges)})
    path = tmp_path / '临时上传名称.md'
    path.write_text('# 性能要求\n\n响应时间不超过2秒。\n')
    project = core.import_files([(path, '软件需求规格说明书.md', None)])
    assert project['documents'][0]['type'] == 'srs'
