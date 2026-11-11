"""Project a truthful ontology/usage graph from Neo4j and persisted execution records."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote

from clausegraph.contracts import fingerprint
from clausegraph.publishing import neo4j_driver

from .core import ONTOLOGY, standards, uri
from .ontology import configuration_snapshot, read_profile
from .planning import make_plan
from .content import content_identifier, version_key

STAGES = [
    ('ontology', '本体加载', '领域本体与评审本体的版本、概念和维度'),
    ('alignment', '条款挂接', '文档条款如何归入本体概念'),
    ('retrieval', '节点与原文召回', '按维度范围查询适用的上下游内容'),
    ('exploration', '已有关系探索', '沿既有类型关系查找关联路径'),
    ('reasoning', '约束判断', '本体路径和原文进入模型进行内容判断'),
    ('consumption', '意见引用与定位', '意见引用了哪些实际原文和已有路径'),
    ('revision', '采纳与版本更新', '用户确认修改，生成新文档和图谱快照'),
]


def edge(source, target, label, kind='trace', **data):
    return {'id': fingerprint([source, target, label, kind]), 'source': source, 'target': target,
            'label': label, 'kind': kind, **data}


def node(identifier, kind, label, stage, **data):
    layer = ('ontology' if kind in ('concept', 'ontology') else
             'configuration' if kind in ('dimension', 'profile', 'doctype', 'configuration') else
             'document' if kind in ('clause', 'document', 'standard', 'content') else 'runtime')
    return {'id': identifier, 'kind': kind, 'label': label, 'stage': stage, 'layer': layer, **data}


def document_identity(document, title=None):
    title = title or document['title']
    return {'document_id': document['id'], 'document_title': title,
            'document_name': title.rsplit(' · ', 1)[-1]}


def run_configuration(project, run):
    if run.get('configuration'):
        return run['configuration']
    cycle = next((c for c in project.get('cycles', []) if c['id'] == run.get('cycle_id')), None)
    if cycle:
        return configuration_snapshot(cycle['profile'], cycle['profile']['dimensions'][run['perspective']])
    return None


def read_snapshot(project):
    snapshot = project['graph']['snapshot_id']
    with neo4j_driver(uri()) as driver, driver.session() as session:
        nodes = session.run('''
            MATCH (:ClauseGraphSnapshot {id:$snapshot})-[:HAS_NODE]->(n)
            OPTIONAL MATCH (n)-[:REPRESENTS_VERSION]->(dv)
            OPTIONAL MATCH (n)-[:IN_DOCUMENT]->(:Document)-[:REPRESENTS_VERSION]->(ov)
            RETURN n.id AS id, n.kind AS kind, n.label AS label, n.labels AS aliases, n.uri AS uri,
                   coalesce(n.document_id,dv.document_id,ov.document_id) AS document_id
        ''', snapshot=snapshot).data()
        edges = session.run('''
            MATCH (:ClauseGraphSnapshot {id:$snapshot})-[:HAS_NODE]->(a)-[r]->(b:ClauseGraphNode {snapshot_id:$snapshot})
            WHERE type(r) IN ['CONTAINS','CLASSIFIED_AS','TYPE_RELATION','MENTIONS','CONTENT_RELATION']
            RETURN a.id AS source,b.id AS target,type(r) AS relation,r.predicate AS predicate,r.assertions_json AS assertions
        ''', snapshot=snapshot).data()
    return nodes, edges


def publish_profile(profile, snapshot):
    """Only explicit RDF configuration is added; no model-proposed ontology edges."""
    rows = list(profile['dimensions'].values())
    with neo4j_driver(uri()) as driver, driver.session() as session:
        session.run('CREATE CONSTRAINT review_profile_id IF NOT EXISTS FOR (p:ReviewProfile) REQUIRE p.id IS UNIQUE').consume()
        session.run('CREATE CONSTRAINT review_dimension_key IF NOT EXISTS FOR (d:ReviewDimension) REQUIRE d.key IS UNIQUE').consume()
        session.run('''
            MERGE (p:ReviewProfile {id:$id}) SET p.title=$title,p.version=$version,p.filename=$filename
            WITH p UNWIND $rows AS row
            MERGE (d:ReviewDimension {key:$id + ':' + row.uri})
            SET d.profile_id=$id,d.uri=row.uri,d.label=row.label,d.agent=row.name,d.scope=row.scope
            MERGE (p)-[:DECLARES_DIMENSION]->(d)
            WITH d,row UNWIND row.concept_uris AS concept
            MATCH (t:TypeConcept {snapshot_id:$snapshot,uri:concept})
            MERGE (d)-[:REVIEWS_CONCEPT {snapshot_id:$snapshot}]->(t)
        ''', id=profile['id'], title=profile['title'], version=profile['version'], filename=profile['filename'],
            rows=rows, snapshot=snapshot).consume()


def consumption_graph(project, run, profile):
    if not run:
        return {'nodes': [], 'edges': []}
    nodes, edges = {}, []

    def add(value):
        nodes[value['id']] = value
        return value['id']

    configuration = run_configuration(project, run)
    has_manifest = bool(run.get('input_manifest'))
    dimension_id = add(node('use-dim:' + run['perspective'], 'dimension',
                            configuration['dimension']['label'] if configuration else run['agent'] + '（历史视角）',
                            'ontology' if configuration else 'reasoning', status='completed',
                            layer='configuration' if configuration else 'runtime'))
    context = run.get('context', {})
    if not context:
        record = add(node('run:' + run['id'], 'agent', run['agent'], 'retrieval', status=run['status'],
                          subtitle='尚未保存有效召回结果', details={'error': run.get('error')}))
        return {'nodes': list(nodes.values()), 'edges': [edge(dimension_id, record, '本次评审视角', 'usage')]}
    source_blocks = context.get('target', []) + context.get('evidence', [])
    documents = {d['id']: d for d in project['documents']}
    origins = {s['id']: document_identity(d) for d in documents.values() for s in d['sections']}
    for block in context.get('evidence', []):
        if block.get('clause_id') and block.get('document_id') in documents:
            origins[block['clause_id']] = document_identity(documents[block['document_id']], block.get('document_title'))
    for path in context.get('paths', []):
        if path['target'] not in origins and run.get('document_id') in documents:
            origins[path['target']] = document_identity(documents[run['document_id']])
    for path in context.get('paths', []):
        for part in path['nodes']:
            is_concept = part['id'].startswith(('type:', 'content:'))
            blocks = [b for b in source_blocks if b.get('clause_id') == part['id']]
            add(node(part['id'], 'content' if part['id'].startswith('content:') else 'concept' if is_concept else 'clause', part['label'] or '文档说明',
                     'ontology' if is_concept else 'alignment', status='completed',
                     **origins.get(part['id'], document_identity(documents[part['document_id']])
                                   if part.get('document_id') in documents else {}),
                     block_id=blocks[0]['id'] if blocks else None,
                     details={'snapshot_id': context.get('snapshot_id'), 'blocks': blocks}))
        for rel in path['edges']:
            predicate = rel['predicate'].rsplit('/', 1)[-1]
            edges.append(edge(rel['source'], rel['target'], predicate,
                              'structure' if predicate == 'CLASSIFIED_AS' else 'content'
                              if rel['source'].startswith('content:') else 'ontology'))
    input_id = add(node('input:' + run['id'], 'input', '本次模型输入', 'reasoning',
        subtitle=f"{len(context.get('evidence', []))} 处召回原文 · {len(context.get('paths', []))} 条路径",
        details={'manifest': run.get('input_manifest'), 'historical': not has_manifest and not configuration,
                 'configuration': configuration,
                 'target': context.get('target', []), 'evidence': context.get('evidence', []),
                 'paths': context.get('paths', []), 'standards': context.get('standards', [])},
        status='completed' if run.get('input_manifest') or run['status'] == 'completed' else 'pending'))
    standard_nodes = {}
    for standard in context.get('standards', []):
        configured = configuration and standard['number'] in configuration['dimension']['standards']
        source_id = ('standard-config:' if configured else 'standard-record:') + run['id']
        config_field = ('run.configuration.dimension.standards' if run.get('configuration') else
                        f"cycle.profile.dimensions.{run['perspective']}.standards")
        if source_id not in nodes:
            if configured:
                source = configuration['profile']
                add(node(source_id, 'configuration', configuration['dimension']['label'] + ' · 标准配置', 'ontology',
                    status='completed', subtitle=source['filename'],
                    details={'source': config_field, 'filename': source['filename'], 'version': source['version'],
                             'sha256': source['id'], 'standards': configuration['dimension']['standards'],
                             'description': '来源为评审维度的 rv:standardClause 声明，仅表示标准选择配置，不表示标准已与具体项目条款建立语义约束。'}))
            else:
                add(node(source_id, 'record', '历史标准输入清单' if not configuration else '已保存的标准输入清单',
                    'ontology', status='completed', subtitle='没有可追溯的配置声明',
                    details={'source': 'run.context.standards',
                        'description': '只展示保存的标准摘录清单；没有可追溯的旧配置声明，不以当前配置补造来源。'}))
        sid = add(node('standard:' + standard['number'], 'standard', 'GJB ' + standard['number'], 'ontology',
                       subtitle=standard['title'], details=standard, status='completed'))
        standard_nodes['standard-' + standard['number']] = (sid, standard)
        edges.append(edge(source_id, sid, '配置选取的标准' if configured else '记录保存的标准摘录',
                          'configuration' if configured else 'usage',
                          provenance={'source': config_field if configured else 'run.context.standards',
                                      'number': standard['number']}))
        edges.append(edge(sid, input_id, '标准摘录进入输入', 'usage',
                          provenance={'source': 'run.context.standards', 'number': standard['number']}))
    for path in context.get('paths', []):
        edges.append(edge(path['evidence'], input_id, '路径与原文进入上下文', 'usage'))
    run_id = add(node('run:' + run['id'], 'agent', run['agent'], 'reasoning', status=run['status'],
                      subtitle='独立视角内容判断', details={'summary': run['summary'], 'metrics': run['metrics'], 'error': run.get('error')}))
    edges += [edge(dimension_id, input_id, '本体规定范围' if configuration else '旧记录的评审视角',
                   'configuration' if configuration else 'usage'), edge(input_id, run_id, '用于评审请求', 'usage')]
    for finding in project['findings']:
        if finding['run_id'] != run['id']:
            continue
        fid = add(node('finding:' + finding['id'], 'finding', finding['title'], 'consumption',
                       status=finding['status'], finding_id=finding['id'], document_id=finding['document_id'],
                       block_id=finding['block_id'], subtitle=finding['category'], details=finding))
        edges.append(edge(run_id, fid, '产出评审意见', 'usage'))
        for evidence in finding['evidence']:
            if evidence['document_id'] == 'standard':
                saved = standard_nodes.get(evidence['block_id'])
                if saved and evidence['quote'] and evidence['quote'] in saved[1]['text']:
                    edges.append(edge(saved[0], fid, '意见实际引用标准', 'usage',
                        provenance={'source': 'finding.evidence', 'quote': evidence['quote'],
                                    'block_id': evidence['block_id'], 'number': saved[1]['number']}))
                continue
            eid = 'evidence:' + evidence['block_id']
            origin = (document_identity(documents[evidence['document_id']], evidence['document_title'])
                      if evidence['document_id'] in documents else
                      {'document_id': evidence['document_id'], 'document_title': evidence['document_title'],
                       'document_name': evidence['document_title'].rsplit(' · ', 1)[-1]})
            add(node(eid, 'evidence', evidence['heading'] or '文档说明', 'consumption',
                     **origin, block_id=evidence['block_id'], subtitle=evidence['document_title'],
                     details={'quote': evidence['quote']}, status='completed'))
            edges.append(edge(eid, fid, '意见实际引用', 'usage'))
        for revision in project['revisions']:
            if revision['finding_id'] == finding['id']:
                rid = add(node('revision:' + revision['id'], 'revision', '用户采纳的修订版本', 'revision',
                               status='completed', details=revision, subtitle=revision['sha256'][:10]))
                edges.append(edge(fid, rid, '确认并采纳', 'usage'))
    return {'nodes': list(nodes.values()), 'edges': list({e['id']: e for e in edges}.values())}


def usage_trace(run, graph):
    """Playback phases select actual saved objects; they never invent domain relations or times."""
    if not run:
        return None
    order = {identifier: i for i, (identifier, _, _) in enumerate(STAGES)}
    context = run.get('context', {})
    steps = []
    for index, (identifier, title, description) in enumerate(STAGES):
        visible = {n['id'] for n in graph['nodes'] if order[n['stage']] <= index}
        active = {n['id'] for n in graph['nodes'] if n['stage'] == identifier}
        if identifier in ('exploration', 'reasoning'):
            active.update(n['id'] for p in context.get('paths', []) for n in p['nodes'])
        if identifier == 'retrieval':
            active.update(n['id'] for n in graph['nodes'] if n['kind'] == 'clause')
        visible_edges = [e['id'] for e in graph['edges'] if e['source'] in visible and e['target'] in visible]
        active_edges = [e['id'] for e in graph['edges'] if e['id'] in visible_edges
                        and ((identifier in ('exploration', 'reasoning') and e['kind'] == 'ontology')
                             or (identifier == 'alignment' and e['label'] == 'CLASSIFIED_AS')
                             or (identifier not in ('exploration', 'alignment') and e['kind'] != 'ontology'
                                 and (e['source'] in active or e['target'] in active)))]
        steps.append({'id': identifier, 'label': title, 'description': description,
                      'visible_node_ids': sorted(visible), 'visible_edge_ids': visible_edges,
                      'active_node_ids': sorted(active), 'active_edge_ids': active_edges})
    max_step = (6 if any(n['kind'] == 'revision' for n in graph['nodes']) else
                5 if run['status'] == 'completed' or 'findings' in run else
                4 if run.get('input_manifest') else 3 if context else 2)
    live_step = min(order.get(run.get('stage'), 0), max_step) if run['status'] == 'running' else max_step
    stats = {'target_blocks': len(context['target']) if 'target' in context else None,
             'retrieved_blocks': len(context.get('evidence', [])), 'existing_paths': len(context.get('paths', [])),
             'standards': len(context.get('standards', [])),
             'cited_standards': len({e['source'] for e in graph['edges'] if e['label'] == '意见实际引用标准'}),
             'configuration_saved': bool(run.get('configuration')) or any(
                 n['kind'] == 'input' and n['details'].get('configuration') for n in graph['nodes'])}
    return {'run_id': run['id'], 'status': run['status'], 'agent': run['agent'], 'stats': stats,
            'snapshot_id': context.get('snapshot_id'), 'live_step': live_step, 'max_step': max_step, 'steps': steps,
            'source': 'saved_context_and_findings', 'has_input_manifest': bool(run.get('input_manifest'))}


def lifecycle(project, run_id=None, document_id=None):
    profile = read_profile()
    plan = make_plan(project, profile, document_id)
    current_document = next(d for d in project['documents'] if d['id'] == plan['document_id'])
    upstream_types = profile['dependencies'].get(current_document['type'], [])
    scoped_document_ids = {d['id'] for d in project['documents']
                           if d['id'] == current_document['id'] or d['type'] in upstream_types}
    snapshot = project['graph']['snapshot_id']
    publish_profile(profile, snapshot)
    base_nodes, base_edges = read_snapshot(project)
    documents = {d['id']: d for d in project['documents']}
    cycles = project.get('cycles', [])
    cycle = next((c for c in reversed(cycles) if c.get('document_id') == plan['document_id']), None)
    upstream_versions = {d['id']: d['sha256'] for d in project['documents'] if d['type'] in upstream_types}
    cycle_current = bool(cycle and cycle.get('document_sha256') == current_document['sha256']
                         and cycle.get('upstream_versions') == upstream_versions and cycle['profile']['id'] == profile['id']
                         and cycle.get('strategy') == plan.get('strategy'))
    roles = cycle['dimensions'] if cycle_current else plan['dimensions']
    nodes, edges = {}, []
    domain_id = 'ontology:' + hashlib.sha256(ONTOLOGY.read_bytes()).hexdigest()
    profile_id = 'profile:' + profile['id']
    nodes[domain_id] = node(domain_id, 'ontology', 'ClauseGraph 领域本体', 'ontology', status='completed',
                            subtitle=ONTOLOGY.name, details={'source': '同门公开原版', 'sha256': domain_id.split(':', 1)[1]})
    nodes[profile_id] = node(profile_id, 'profile', profile['title'], 'ontology', status='completed',
                             subtitle='评审应用本体 · ' + profile['version'], details=profile)
    for value in base_nodes:
        identifier = value['id']
        label = next((v for v in value.get('aliases') or [] if any('\u4e00' <= c <= '\u9fff' for c in v)), value['label'])
        doc = documents.get(value['document_id'])
        is_concept = value['kind'] == 'TypeConcept'
        is_content = value['kind'] == 'ContentConcept'
        blocks = [b for b in doc['blocks'] if b['clause_id'] == identifier] if doc else []
        if is_content and doc:
            record = project.get('content_ontologies', {}).get(doc['id'] + ':' + doc['sha256'], {})
            clauses = {m['clause_id'] for m in record.get('extraction', {}).get('mentions', []) if m['concept'] == value['label']}
            blocks = [b for b in doc['blocks'] if b['clause_id'] in clauses]
        nodes[identifier] = node(identifier, 'concept' if is_concept else 'content' if is_content else value['kind'].lower(),
            doc['type_name'] if value['kind'] == 'Document' and doc else label or '文档说明',
            'ontology' if is_concept else 'alignment', status='completed',
            document_id=value['document_id'], block_id=next((b['id'] for b in blocks if b['kind'] != 'heading'), None),
            document_name=doc['title'].rsplit(' · ', 1)[-1] if doc and value['kind'] == 'Clause' else None,
            document_title=doc['title'] if doc else None,
            subtitle=doc['title'] if doc and value['kind'] == 'Clause' else '',
            details={'uri': value['uri'], 'blocks': blocks, 'snapshot_id': snapshot,
                     'source_file': doc['filename'] if doc else None})
        if is_concept:
            edges.append(edge(domain_id, identifier, '定义概念', 'configuration'))
    for value in base_edges:
        kind = 'content' if value['relation'] == 'CONTENT_RELATION' else 'ontology' if value['relation'] == 'TYPE_RELATION' else 'structure'
        edges.append(edge(value['source'], value['target'], unquote(value['predicate'].rsplit('/', 1)[-1]) if value['predicate'] else value['relation'], kind,
                          details=json.loads(value['assertions']) if value['assertions'] else []))
    for role in roles:
        identifier = 'dimension:' + role['id']
        nodes[identifier] = node(identifier, 'dimension', role['label'], 'ontology', status=role['status'],
            subtitle=f"1 个维度任务 · {role['block_count']} 个内容块" if role['batches'] else '无适用材料', details=role)
        edges.append(edge(profile_id, identifier, '声明评审维度', 'configuration'))
        for concept in role['concept_uris']:
            target = 'type:' + concept
            if target in nodes:
                edges.append(edge(identifier, target, '本维度评审概念', 'configuration'))
        for standard in standards(role):
            sid = 'standard:' + standard['number']
            nodes[sid] = node(sid, 'standard', 'GJB ' + standard['number'], 'ontology', status='completed',
                              subtitle=standard['title'], details=standard)
            edges.append(edge(identifier, sid, '适用标准条款', 'configuration'))
    for kind, title in profile['document_types'].items():
        identifier = 'doctype:' + kind
        nodes[identifier] = node(identifier, 'doctype', title, 'ontology', status='completed')
        for parent in profile['dependencies'][kind]:
            edges.append(edge('doctype:' + parent, identifier, '上游内容约束', 'configuration'))
    for role in roles:
        for kind in role['target_types']:
            edges.append(edge('dimension:' + role['id'], 'doctype:' + kind, '适用文档类型', 'configuration'))

    document_runs = [r for r in project['runs'] if r.get('document_id') == plan['document_id']]
    selected = next((r for r in project['runs'] if r['id'] == run_id), document_runs[-1] if document_runs else None)
    visible_runs = document_runs + ([selected] if selected and selected not in document_runs else [])
    current_runs = [r for r in project['runs'] if cycle_current and r.get('cycle_id') == cycle['id']]
    current_findings = {f['id'] for f in project['findings'] if cycle_current and f.get('cycle_id') == cycle['id']}
    current_revisions = [r for r in project['revisions'] if r['finding_id'] in current_findings]
    total = cycle['batch_count'] if cycle_current else plan['batch_count']
    stages = []
    for identifier, title, detail in STAGES:
        completed = sum(bool(r.get('context')) for r in current_runs)
        if identifier == 'reasoning':
            completed = sum(r['status'] == 'completed' for r in current_runs)
        if identifier == 'consumption':
            completed = sum('findings' in r for r in current_runs)
        state = 'completed' if identifier in ('ontology', 'alignment') else (
            'completed' if total and completed == total else 'running' if cycle_current and cycle['status'] == 'running' else 'pending')
        if identifier == 'revision':
            state = 'completed' if current_revisions else 'waiting_user' if current_runs else 'pending'
        facts = {'scope': '当前文档版本、评审本体及本轮全维度计划', 'snapshot_id': snapshot,
                 'completed': completed, 'total': total}
        if identifier == 'ontology':
            facts = {'filename': profile['filename'], 'version': profile['version'],
                     'source': f"评审本体声明 {len(roles)} 个维度；领域本体来自 {ONTOLOGY.name}",
                     'sha256': profile['id']}
        elif identifier == 'alignment':
            facts['description'] = f"当前快照包含 {len(base_nodes)} 个原生节点和 {sum(e['relation'] == 'CLASSIFIED_AS' for e in base_edges)} 条规则挂接；候选关系可点击连线查看命中依据。挂接完成不代表内容满足要求。"
        elif identifier == 'revision':
            facts['description'] = f"本轮已采纳 {len(current_revisions)} 处；项目共保存 {len(project['revisions'])} 个修订。修订后重新发布图谱快照，旧记录保留原版本。"
        stages.append({'id': identifier, 'label': title, 'description': detail, 'status': state,
                       'completed': completed if identifier not in ('ontology', 'alignment', 'revision') else None,
                       'total': total, 'facts': facts})
    process_nodes = [node('stage:' + s['id'], 'stage', s['label'], s['id'], status=s['status'],
                         subtitle=s['description'], details=s['facts']) for s in stages]
    process_edges = [edge('stage:' + left[0], 'stage:' + right[0], '进入下一环节', 'flow')
                     for left, right in zip(STAGES, STAGES[1:]) if left[0] != 'exploration']
    for role in roles:
        dnode = nodes['dimension:' + role['id']]
        process_nodes.append({**dnode, 'stage': 'reasoning'})
        process_edges.append(edge('stage:exploration', dnode['id'], '按本体范围', 'configuration'))
        process_edges.append(edge(dnode['id'], 'stage:reasoning', '独立视角评审', 'flow'))
    used_graph = consumption_graph(project, selected, profile)
    growing = document_content_graph(project)
    nodes.update({n['id']: n for n in growing['nodes']})
    edges = list({e['id']: e for e in edges + growing['edges']}.values())
    scoped_clauses = {n['id'] for n in nodes.values() if n['kind'] == 'clause' and n.get('document_id') in scoped_document_ids}
    mapped_types = {e['target'] for e in edges if e['label'] == 'CLASSIFIED_AS' and e['source'] in scoped_clauses}
    business_ids = {n['id'] for n in nodes.values() if n['kind'] in ('document', 'clause', 'content') and n.get('document_id') in scoped_document_ids
                    or n['kind'] == 'concept' and n['id'] in mapped_types}
    business = {'nodes': [n for n in nodes.values() if n['id'] in business_ids],
                'edges': [e for e in edges if e['source'] in business_ids and e['target'] in business_ids]}
    static = standard_graph()
    static['nodes'].extend(n for n in nodes.values() if n['kind'] == 'concept')
    static['edges'].extend(e for e in edges if e['kind'] == 'ontology'
                           and nodes[e['source']]['kind'] == nodes[e['target']]['kind'] == 'concept')
    content_ready = version_key(current_document) in project.get('content_ontologies', {})
    process_nodes.append(node('stage:content_growth', 'stage', '文档内容本体生长与保存', 'alignment',
                              status='completed' if content_ready else 'pending',
                              subtitle='每文档版本构建一次，各维度复用；不是维度切批'))
    process_edges = [e for e in process_edges if not (e['source'] == 'stage:alignment' and e['target'] == 'stage:retrieval')]
    process_edges.extend([edge('stage:alignment', 'stage:content_growth', '按文档构建', 'flow'),
                          edge('stage:content_growth', 'stage:retrieval', '共享内容图谱', 'flow')])
    return {'profile': profile, 'plan': plan, 'dimensions': roles, 'stages': stages,
            'cycle': cycle, 'cycle_current': cycle_current, 'selected_run': selected['id'] if selected else None,
            'runs': [{'id': r['id'], 'agent': r['agent'], 'status': r['status'], 'perspective': r['perspective'],
                      'cycle_id': r.get('cycle_id'), 'scope': r.get('review_scope', 'historical'), 'metrics': r['metrics']} for r in visible_runs],
            'ontology_graph': business, 'static_graph': static,
            'content_status': [{'document_id': d['id'], 'title': d['title'],
                                'saved': d['id'] + ':' + d['sha256'] in project.get('content_ontologies', {})}
                               for d in project['documents'] if d['id'] in scoped_document_ids],
            'process_graph': {'nodes': process_nodes, 'edges': process_edges},
            'consumption_graph': used_graph, 'trace': usage_trace(selected, used_graph),
            'statistics': {'concepts': sum(n['kind'] == 'concept' for n in nodes.values()),
                           'clauses': sum(n['kind'] == 'clause' for n in nodes.values()),
                           'bindings': sum(e['label'] == 'CLASSIFIED_AS' for e in edges), 'dimensions': len(roles)},
            'ingestions': project.get('ingestions', [])}


def document_content_graph(project):
    """Show saved per-document growth immediately, before the combined snapshot is published."""
    nodes, edges = {}, []
    for document in project['documents']:
        record = project.get('content_ontologies', {}).get(version_key(document))
        if not record:
            continue
        for concept in record['grounded']['concepts']:
            identifier = content_identifier(document, concept['label'])
            block = next(b for b in document['blocks'] if b['id'] == concept['block_id'])
            nodes[identifier] = node(identifier, 'content', concept['label'], 'alignment', layer='document',
                status='completed', document_id=document['id'], document_title=document['title'],
                document_name=document['type_name'], block_id=block['id'], subtitle='原文已绑定，语义待核查',
                details={'quote': concept['quote'], 'blocks': [block], 'sha256': document['sha256'],
                         'source': '已保存的文档内容抽取；不是GJB规范',
                         'published': version_key(document) in project['graph'].get('content_versions', [])})
            edges.append(edge(block['clause_id'], identifier, 'MENTIONS', 'structure',
                              provenance={'source': document['filename'], 'quote': concept['quote']}))
        for relation in record['grounded']['relations']:
            edges.append(edge(content_identifier(document, relation['subject']), content_identifier(document, relation['object']),
                              relation['relation'], 'content', provenance={'source': document['filename'], 'quote': relation['quote']}))
    return {'nodes': list(nodes.values()), 'edges': edges}


def standard_graph():
    """Project the supplied standard tree as immutable normative text, not project facts."""
    path = Path(os.getenv('GJB_SOURCE_JSON', ''))
    if not path.is_file():
        return {'nodes': [], 'edges': []}
    data = json.loads(path.read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    nodes, edges = [], []

    def visit(section, parent=None):
        identifier = 'norm:' + digest + ':' + section['id']
        nodes.append(node(identifier, 'standard', 'GJB ' + section['id'] + ' ' + section['title'], 'ontology',
                          layer='ontology', details={'number': section['id'], 'source': path.name,
                              'sha256': digest, 'pages': section['pdf_pages'],
                              'text': '\n'.join(b['text'] for b in section['blocks'])}))
        if parent:
            edges.append(edge(parent, identifier, '包含标准条款', 'structure'))
        for child in section['children']:
            visit(child, identifier)
    for section in data['sections']:
        visit(section)
    return {'nodes': nodes, 'edges': edges}
