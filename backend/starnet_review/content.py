"""Source-bound document ontologies, built once per version and shared by reviewers."""
import json
import re
import time
from urllib.parse import quote
from urllib.parse import unquote
import unicodedata

from clausegraph import build_base, build_route1, build_route2, fuse_graphs
from clausegraph.contracts import ClauseTree, Evidence, Location, fingerprint
from clausegraph.extraction import Extraction, build_route3
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel, ConfigDict

from . import core


class Concept(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str
    quote: str


class Relation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: str
    relation: str
    object: str
    quote: str


class DocumentContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str
    concepts: list[Concept]
    relations: list[Relation]


def version_key(document):
    return document['id'] + ':' + document['sha256']


def content_identifier(document, label):
    return 'content:' + fingerprint([document['id'], document['sha256'], document['tree']['parser_version'],
                                    unicodedata.normalize('NFKC', label).casefold().strip()])


class DocumentProvider:
    configuration = {'method': 'source_bound_document_content', 'version': '1.0'}

    def __init__(self, project, model, emit, metrics, document_ids):
        self.project, self.model, self.emit, self.metrics = project, model, emit, metrics
        self.document_ids = document_ids

    async def extract(self, text):
        first = re.search(r'\[clause_id=(.*?)\]', text).group(1)
        document = next(d for d in self.project['documents'] if any(b['clause_id'] == first for b in d['blocks']))
        versions = self.project.setdefault('content_ontologies', {})
        identity = version_key(document)
        if identity in versions:
            await self.emit({'kind': 'ontology_reused', 'title': '复用已保存的文档内容本体', 'detail': document['title']})
            return Extraction.model_validate(versions[identity]['extraction'])
        if document['id'] not in self.document_ids:
            return Extraction(concepts=[], mentions=[], relations=[])
        await self.emit({'kind': 'ontology_growth', 'title': '构建文档内容本体', 'detail': document['title']})
        bound = self.model.bind(response_format={'type': 'json_schema', 'json_schema': {
            'name': 'document_content', 'strict': True, 'schema': DocumentContent.model_json_schema()}})
        messages = [SystemMessage(content=(
            '从当前文档全文构建内容本体，用简体中文描述业务对象、功能、接口、指标、条件和明确关系。'
            '概念和关系名称可随文档扩展，不限于固定词表。每个概念和关系都附一段逐字原文quote。'
            '关系两端使用本次concepts中的名称；只提取原文明示关系，不补写隐含关系或GJB写作要求。'
            'summary简述本份文档规定的实际内容。')),
            HumanMessage(content=json.dumps({'文档': document['title'], '全文': [
                {'章节': b['heading'], '原文': b['text']} for b in document['blocks'] if b['text'].strip()]}, ensure_ascii=False))]
        self.metrics['model_calls'] += 1
        raw, summary = '', ''
        async for chunk in bound.astream(messages):
            if isinstance(chunk.content, str):
                raw += chunk.content
                try:
                    partial = parse_partial_json(raw)
                except (ValueError, TypeError):
                    partial = None
                value = partial.get('summary', '') if isinstance(partial, dict) else ''
                if value and value != summary:
                    summary = value
                    await self.emit({'kind': 'text', 'run_id': 'content:' + document['id'],
                                     'perspective': 'content', 'text': document['title'] + '\n' + summary})
            if chunk.usage_metadata:
                for name in ('input_tokens', 'output_tokens'):
                    self.metrics[name] += chunk.usage_metadata.get(name, 0)
        result = DocumentContent.model_validate_json(raw).model_dump()
        labels, mentions, unresolved, grounded_concepts, grounded_relations = set(), [], [], [], []
        for concept in result['concepts']:
            matches = [b for b in document['blocks'] if concept['quote'].strip() and concept['quote'] in b['text']]
            if len(matches) != 1:
                unresolved.append(concept)
                continue
            labels.add(concept['label'])
            mentions.append({'concept': concept['label'], 'clause_id': matches[0]['clause_id']})
            grounded_concepts.append({**concept, 'block_id': matches[0]['id'], 'clause_id': matches[0]['clause_id']})
        relations = []
        for relation in result['relations']:
            if (relation['subject'] in labels and relation['object'] in labels and relation['quote'].strip()
                    and any(relation['quote'] in b['text'] for b in document['blocks'])):
                relations.append({'subject': relation['subject'], 'object': relation['object'],
                                  'predicate': 'https://starnet.local/content/' + quote(relation['relation'], safe=''),
                                  'quote': relation['quote']})
                grounded_relations.append(relation)
            else:
                unresolved.append(relation)
        extraction = Extraction(concepts=sorted(labels), mentions=mentions, relations=relations)
        versions[identity] = {'document_id': document['id'], 'source_sha256': document['sha256'],
                              'created_at': time.time(), 'summary': result['summary'], 'draft': result,
                              'grounded': {'concepts': grounded_concepts, 'relations': grounded_relations},
                              'unresolved': unresolved, 'extraction': extraction.model_dump(mode='json')}
        core.save(self.project)
        await self.emit({'kind': 'ontology_saved', 'title': '文档内容本体已保存',
                         'detail': f"{document['title']}：{len(labels)} 个概念、{len(relations)} 条关系，{len(unresolved)} 项未绑定；语义仍待核查"})
        return extraction


async def ensure_content_ontologies(project, model, emit, metrics, document_ids=None):
    document_ids = set(document_ids or [d['id'] for d in project['documents']])
    ready = project.get('content_ontologies', {})
    versions = [version_key(d) for d in project['documents'] if version_key(d) in ready]
    selected_ready = all(version_key(d) in ready for d in project['documents'] if d['id'] in document_ids)
    if selected_ready and project['graph'].get('content_versions') == versions:
        await emit({'kind': 'ontology_reused', 'title': '复用已保存的全部文档内容本体'})
        return
    base = build_base(project['id'], [ClauseTree.model_validate(d['tree']) for d in project['documents']])
    third = await build_route3(base, DocumentProvider(project, model, emit, metrics, document_ids))
    if third.status != 'ready':
        raise ValueError('文档内容本体尚未完成，成功文档已保存；未开始维度评审：' + json.dumps(third.metadata.get('failed_units', []), ensure_ascii=False))
    third.metadata['unselected_documents'] = [d['id'] for d in project['documents']
        if version_key(d) not in project.get('content_ontologies', {})]
    third.metadata['completed_units'] = len(project['documents']) - len(third.metadata['unselected_documents'])
    for edge in third.edges:
        if edge.kind != 'MENTIONS':
            continue
        record = next(c for c in project['content_ontologies'].values()
                      if any(m['clause_id'] == edge.source for m in c['extraction']['mentions']))
        document = next(d for d in project['documents'] if d['id'] == record['document_id'])
        edge.evidence = [Evidence(clause_id=edge.source, location=Location.model_validate(b['location']),
                                 row=b['row'], excerpt=c['quote'])
                         for c in record['grounded']['concepts'] for b in document['blocks']
                         if c['label'] == edge.properties['label'] and c['clause_id'] == edge.source and c['block_id'] == b['id']]
        edge.properties['method'] = 'program_bound_exact_quote'
    graph = fuse_graphs(base, [build_route1(base, top_k=20), build_route2(base, core.ONTOLOGY), third])
    project['graph'].update(core.publish_graph(graph, core.uri()))
    project['graph']['content_versions'] = [version_key(d) for d in project['documents']
                                           if version_key(d) in project['content_ontologies']]
    core.save(project)
    await emit({'kind': 'ontology_ready', 'title': '文档内容图谱已发布，各维度共享同一快照'})


def dimension_context(project, role, coverage, dependencies):
    documents = {d['id']: d for d in project['documents']}
    selected = {item['document_id']: set(item['block_ids']) for item in coverage}
    target = [{**b, 'document_id': d['id'], 'document_title': d['title'], 'source_sha256': d['sha256']}
              for d in documents.values() for b in d['blocks'] if b['id'] in selected.get(d['id'], set())]
    upstream_types = {kind for identifier in selected for kind in dependencies.get(documents[identifier]['type'], [])}
    upstream = [d for d in documents.values() if d['type'] in upstream_types and d['id'] not in selected]
    evidence = [{**b, 'document_id': d['id'], 'document_title': d['title']} for d in upstream for b in d['blocks']]
    contents = [(d, project['content_ontologies'][version_key(d)]) for d in documents.values()
                if (d['id'] in selected or d in upstream) and version_key(d) in project.get('content_ontologies', {})]
    business = [{'文档': d['title'], '概念': list(dict.fromkeys(v['label'] for v in c['grounded']['concepts'])),
                 '明确关系': [{k: v for k, v in r.items() if k != 'quote'} for r in c['grounded']['relations']]} for d, c in contents]
    upstream_status = [{'文档': d['title'], '内容本体': '已保存，可复用' if version_key(d) in project.get('content_ontologies', {})
                        else '尚未建立，本轮仅提供已上传原文'} for d in upstream]
    paths = []
    for document, content in contents:
        for relation in content['extraction']['relations']:
            for block in document['blocks']:
                if not relation['quote'] or relation['quote'] not in block['text']:
                    continue
                left, right = content_identifier(document, relation['subject']), content_identifier(document, relation['object'])
                paths.append({'id': fingerprint([version_key(document), relation, block['id']]),
                    'target': block['clause_id'], 'evidence': block['clause_id'],
                    'nodes': [{'id': left, 'label': relation['subject'], 'document_id': document['id']},
                              {'id': right, 'label': relation['object'], 'document_id': document['id']}],
                    'quote': relation['quote'],
                    'edges': [{'source': left, 'target': right, 'predicate': unquote(relation['predicate'])}]})
    return {'document': documents[coverage[0]['document_id']], 'role': role, 'target': target, 'evidence': evidence,
            'paths': paths, 'standards': core.standards(role), 'snapshot_id': project['graph']['snapshot_id'],
            'content_ontologies': business, 'upstream_status': upstream_status,
            'retrieval': {'scope': '当前文档的完整维度原文及适用上游文档，共享版本化内容图谱'}}
