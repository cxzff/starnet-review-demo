"""ClauseGraph ingestion and existing-relation retrieval for independent review agents."""
import json
import os
import re
import time
import uuid
from pathlib import Path

from clausegraph import build_base, build_route1, build_route2, fuse_graphs, parse_document, publish_graph
from clausegraph.contracts import Clause, ClauseTree, clause_key, fingerprint
from clausegraph.graphs import route_graph
from clausegraph.publishing import neo4j_driver

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data' / 'projects'
ONTOLOGY = ROOT / 'resources' / 'software-engineering-ontology.ttl'
from .ontology import read_profile

ROLES = read_profile()['dimensions']
UPSTREAM = read_profile()['dependencies']
TYPE_NAMES = {'taskbook': '软件研制任务书', 'srs': '软件需求规格说明', 'sdd': '软件设计说明',
              'stp': '测试计划／大纲', 'std': '测试说明／细则', 'str': '测试报告', 'other': '参考文档'}


def key():
    return uuid.uuid4().hex[:16]


def uri():
    return os.getenv('NEO4J_URI', 'bolt://127.0.0.1:7689')


def save(project):
    directory = DATA / project['id']
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / 'project.tmp'
    temporary.write_text(json.dumps(project, ensure_ascii=False), encoding='utf-8')
    temporary.replace(directory / 'project.json')


def load(project_id):
    if not re.fullmatch(r'[0-9a-f]{16}', project_id):
        raise FileNotFoundError('项目不存在')
    return json.loads((DATA / project_id / 'project.json').read_text())


def list_projects():
    return [{'id': p['id'], 'name': p['name'], 'documents': len(p['documents'])}
            for file in sorted(DATA.glob('*/project.json'), key=lambda f: f.stat().st_mtime, reverse=True)
            for p in [json.loads(file.read_text())]]


def detect_type(title):
    for token, kind in [('任务书', 'taskbook'), ('需求', 'srs'), ('设计', 'sdd'), ('报告', 'str'),
                        ('大纲', 'stp'), ('计划', 'stp'), ('细则', 'std'), ('测试说明', 'std')]:
        if token in title:
            return kind
    return 'other'


def split_document(path, source_id, explicit_type=None):
    tree = parse_document(path, source_id)
    parts = [node for node in tree.root.children if re.match(r'第\s*[一二三四五六七八九十\d]+\s*部分', node.title)]
    if not parts:
        return [tree.model_copy(update={'document_type': explicit_type or detect_type(path.stem)})]
    # Each part keeps original DOCX positions; the container order is not the engineering dependency order.
    return [ClauseTree(document_id=f'{source_id}-{index}', source_sha256=tree.source_sha256,
                       parser_version=tree.parser_version, document_type=detect_type(part.title),
                       root=Clause(clause_id=f'root-{index}', title='', level=0, order=0, children=[part]))
            for index, part in enumerate(parts, 1)]


def flatten(tree, filename):
    blocks, sections = [], []

    def visit(node, parents):
        path = [*parents, node.title] if node.title else parents
        cid = clause_key(tree, node)
        if node.location:
            blocks.append({'id': key(), 'clause_id': cid, 'kind': 'heading', 'text': node.title,
                           'level': node.level, 'heading': ' / '.join(path), 'location': node.location.model_dump(), 'row': None})
            sections.append({'id': cid, 'title': node.title, 'level': node.level, 'path': path})
        for block in node.blocks:
            values = [(None, block.text)] if block.kind == 'paragraph' else [(i, ' | '.join(row)) for i, row in enumerate(block.rows, 1)]
            for row, text in values:
                blocks.append({'id': key(), 'clause_id': cid, 'kind': 'table' if row else 'paragraph', 'text': text,
                               'heading': ' / '.join(path), 'location': block.location.model_dump(), 'row': row,
                               'cells': block.rows[row - 1] if row else []})
        for child in node.children:
            visit(child, path)

    visit(tree.root, [])
    return {'id': tree.document_id, 'title': tree.root.children[0].title, 'type': tree.document_type,
            'type_name': TYPE_NAMES[tree.document_type or 'other'], 'filename': filename,
            'sha256': tree.source_sha256, 'blocks': blocks, 'sections': sections, 'tree': tree.model_dump(mode='json')}


def import_files(files, project=None):
    started = time.perf_counter()
    profile = read_profile()
    events = [{'stage': 'ontology', 'title': '加载领域本体与评审本体', 'time': time.time(),
               'profile_id': profile['id'], 'dimensions': len(profile['dimensions'])}]
    project = project or {'id': key(), 'name': re.sub(r'_(软件需求规格说明书|软件设计说明书|集成测试报告|出厂测试大纲|测试细则)$', '', re.sub(r'^\d+_', '', Path(files[0][1]).stem).split('_五类')[0]),
                          'documents': [], 'findings': [], 'runs': [], 'revisions': []}
    directory = DATA / project['id']
    directory.mkdir(parents=True, exist_ok=True)
    previous_documents = len(project['documents'])
    for path, original_name, kind in files:
        source_id = key()
        destination = directory / f'{source_id}{path.suffix.lower()}'
        destination.write_bytes(path.read_bytes())
        for tree in split_document(destination, source_id, kind or detect_type(original_name)):
            doc = flatten(tree, original_name)
            if tree.document_id == source_id:
                doc['title'] = re.sub(r'^\d+_', '', Path(original_name).stem).replace('_', ' · ')
            doc['source_file'] = destination.name
            project['documents'].append(doc)
    if files:
        project['review_document_id'] = project['documents'][previous_documents]['id']
    events.append({'stage': 'alignment', 'title': '解析文档与版本化条款', 'time': time.time(),
                   'documents': len(project['documents'])})
    trees = [ClauseTree.model_validate(d['tree']) for d in project['documents']]
    base = build_base(project['id'], trees)
    second = build_route2(base, ONTOLOGY)
    bindings = [{'clause_id': e.source, 'concept_id': e.target, **e.properties}
                for e in second.edges if e.kind == 'CLASSIFIED_AS']
    events.append({'stage': 'alignment', 'title': '条款挂接已有本体概念', 'time': time.time(),
                   'bindings': len(bindings), 'unmatched': len(second.metadata.get('unmatched_clauses', []))})
    fused = fuse_graphs(base, [build_route1(base, top_k=20), second,
                              route_graph(base, 'route3', {'enabled': False}, status='skipped')])
    publication = publish_graph(fused, uri())
    project['graph'] = {**publication, 'engine': 'ClauseGraph', 'ontology': 'Software Engineering Domain Profile 1.0.0',
                        'bindings': bindings,
                        'concepts': [{'id': n.id, 'label': next((v for v in n.properties.get('labels', []) if re.search(r'[\u4e00-\u9fff]', v)), n.label), 'uri': n.properties['uri']} for n in second.nodes],
                        'relations': [{'source': e.source, 'target': e.target, 'predicate': e.predicate.rsplit('/', 1)[-1]} for e in second.edges if e.kind == 'TYPE_RELATION']}
    events.append({'stage': 'alignment', 'title': '发布可追溯图谱快照', 'time': time.time(), **publication})
    project.setdefault('ingestions', []).append({'snapshot_id': publication['snapshot_id'], 'profile_id': profile['id'],
        'events': events, 'elapsed_seconds': round(time.perf_counter() - started, 2)})
    save(project)
    return project


def section_blocks(document, section_id):
    sections = document['sections']
    section = next((s for s in sections if s['id'] == section_id), None)
    if section is None:
        raise ValueError('请选择原文中的章节')
    allowed = {s['id'] for s in sections if s['path'][:len(section['path'])] == section['path']}
    return [b for b in document['blocks'] if b['clause_id'] in allowed]


def standards(role):
    path = os.getenv('GJB_SOURCE_JSON', '')
    if not path or not Path(path).is_file():
        return []
    data = json.loads(Path(path).read_text())
    wanted = role['standards']
    found = []

    def visit(node):
        if node['id'] in wanted:
            def text(n):
                return '\n'.join([n['title'], *(b['text'] for b in n['blocks']), *(text(c) for c in n['children'])])
            found.append({'title': node['title'], 'number': node['id'], 'pages': node['pdf_pages'], 'text': text(node)})
        else:
            for child in node['children']:
                visit(child)
    for node in data['sections']:
        visit(node)
    return found


def retrieve(project, document_id, section_id, perspective, *, target_ids=None, role=None, dependencies=None):
    document = next(d for d in project['documents'] if d['id'] == document_id)
    target = ([b for b in document['blocks'] if b['id'] in set(target_ids)] if target_ids is not None
              else section_blocks(document, section_id))
    if sum(len(b['text']) for b in target) > 24000:
        raise ValueError('当前范围较大，请选择一个更具体的章节；原文不会被静默截断')
    profile = read_profile()
    role = role or profile['dimensions'][perspective]
    upstream_types = (dependencies or profile['dependencies']).get(document['type'], [])
    allowed_docs = [d['id'] for d in project['documents'] if d['id'] == document_id or d['type'] in upstream_types]
    targets = list(dict.fromkeys(b['clause_id'] for b in target))
    snapshot = project['graph']['snapshot_id']
    with neo4j_driver(uri()) as driver, driver.session() as session:
        candidates = session.run('''
            MATCH (a:Clause {snapshot_id:$snapshot})-[r:SIMILAR_TO]-(b:Clause {snapshot_id:$snapshot})
            WHERE a.id IN $targets AND NOT b.id IN $targets
            MATCH (b)-[:IN_DOCUMENT]->(:Document)-[:REPRESENTS_VERSION]->(v)
            WHERE v.document_id IN $documents
            RETURN b.id AS id, v.document_id AS document_id, min(r.rank) AS rank
            ORDER BY rank, id LIMIT 60
        ''', snapshot=snapshot, targets=targets, documents=allowed_docs).data()
        upstream = [c for c in candidates if c['document_id'] != document_id][:10]
        internal = [c for c in candidates if c['document_id'] == document_id][:4]
        candidates = upstream + internal
        paths = []
        for candidate in candidates:
            rows = session.run('''
                MATCH p=(a:Clause {snapshot_id:$snapshot})-[:CLASSIFIED_AS]->(t:TypeConcept)
                       -[:TYPE_RELATION*0..2]-(u:TypeConcept)<-[:CLASSIFIED_AS]-(b:Clause {snapshot_id:$snapshot,id:$candidate})
                WHERE a.id IN $targets
                  AND all(n IN nodes(p) WHERE n.snapshot_id=$snapshot)
                  AND any(n IN nodes(p) WHERE n.uri IN $focus)
                RETURN a.id AS target, b.id AS evidence,
                       [n IN nodes(p) | {id:n.id,label:n.label,aliases:n.labels}] AS nodes,
                       [r IN relationships(p) | {source:startNode(r).id,target:endNode(r).id,predicate:coalesce(r.predicate,type(r))}] AS edges
                ORDER BY CASE WHEN t=u THEN 1 ELSE 0 END, length(p), target LIMIT 1
            ''', snapshot=snapshot, targets=targets, candidate=candidate['id'],
                focus=['https://example.org/software-engineering/' + s for s in role['concepts']]).data()
            for row in rows:
                for node in row['nodes']:
                    aliases = node.pop('aliases') or []
                    node['label'] = next((label for label in aliases if re.search(r'[\u4e00-\u9fff]', label)), node['label'])
            paths.extend(rows)
    for path in paths:
        path['id'] = fingerprint([snapshot, path['target'], path['evidence'], path['edges']])
    evidence = []
    used = set()
    size = 0
    for candidate in candidates:
        doc = next(d for d in project['documents'] if d['id'] == candidate['document_id'])
        for block in doc['blocks']:
            if block['clause_id'] != candidate['id'] or block['id'] in used:
                continue
            if size + len(block['text']) > 24000:
                continue
            used.add(block['id'])
            size += len(block['text'])
            evidence.append({**block, 'document_id': doc['id'], 'document_title': doc['title'],
                             'relationship': '文档内部关联' if doc['id'] == document_id else '上游文档约束'})
    # Document-level status is part of the evidence scope, not a task-specific prompt assumption.
    overview, seen_text = [], {b['text'] for b in target + evidence}
    for doc in project['documents']:
        if doc['id'] not in allowed_docs:
            continue
        for block in doc['blocks']:
            if block['kind'] == 'heading':
                break
            if block['text'] in seen_text:
                continue
            seen_text.add(block['text'])
            overview.append({**block, 'document_id': doc['id'], 'document_title': doc['title'], 'relationship': '文档范围与状态'})
    return {'target': target, 'evidence': [*overview, *evidence], 'paths': paths, 'standards': standards(role),
            'role': role, 'document': document, 'snapshot_id': snapshot,
            'retrieval': {'candidate_clauses': len(candidates), 'target_clauses': len(targets),
                          'exploration_hops': 2, 'upstream_types': upstream_types}}


def bind_quote(quote, blocks):
    matches = [b for b in blocks if quote.strip() and quote in b['text']]
    # Repeated wording has no unique location. Never select a random occurrence.
    return matches[0] if len(matches) == 1 else None


def bind_results(project, context, output, run_id):
    findings, unmatched = [], []
    sources = [*context['evidence'], *[{'document_id': context['document']['id'],
               'document_title': context['document']['title'], **b} for b in context['target']]]
    sources.extend({'id': 'standard-' + s['number'], 'document_id': 'standard', 'document_title': 'GJB 438C-2021',
                    'heading': s['number'] + ' ' + s['title'], 'text': s['text']} for s in context['standards'])
    for value in output['findings']:
        block = bind_quote(value['quote'], context['target'])
        if block is None:
            unmatched.append(value)
            continue
        evidence = []
        for quote in value['evidence_quotes']:
            for source in sources:
                # Bind either raw source text or the exact program-rendered source entry in older records.
                display = f"【{source['document_title']} / {source['heading']}】\n{source['text']}"
                actual_quote = source['text'] if quote == display else quote
                if actual_quote.strip() and actual_quote in source['text'] and not any(e['block_id'] == source['id'] for e in evidence):
                    evidence.append({'quote': actual_quote, 'block_id': source['id'], 'document_id': source['document_id'],
                                     'document_title': source['document_title'], 'heading': source['heading'],
                                     'clause_id': source.get('clause_id')})
        editable = block['kind'] != 'heading' and (not block.get('row') or
                    sum(value['quote'] in cell for cell in block.get('cells', [])) == 1)
        findings.append({**value, 'id': key(), 'run_id': run_id, 'document_id': block.get('document_id', context['document']['id']),
                         'block_id': block['id'], 'original_block': block['text'], 'evidence': evidence,
                         'clause_id': block['clause_id'], 'perspective': context['role'].get('id'),
                         'path_ids': [p['id'] for p in context['paths'] if p.get('id') and p['target'] == block['clause_id']
                                      and any(e.get('clause_id') == p['evidence'] for e in evidence)],
                         'status': 'open', 'source_sha256': block.get('source_sha256', context['document']['sha256']),
                         'editable': editable, 'replacement': value['replacement']})
    project['findings'].extend(findings)
    return findings, unmatched
