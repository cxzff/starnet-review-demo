"""Generate complete independent review work from explicit ontology dimensions."""
from .core import key, uri
from .ontology import read_profile
from clausegraph.publishing import neo4j_driver

def bindings(project):
    if 'bindings' in project['graph']:
        return project['graph']['bindings']
    with neo4j_driver(uri()) as driver, driver.session() as session:
        return session.run('''
            MATCH (c:Clause {snapshot_id:$snapshot})-[r:CLASSIFIED_AS]->(t:TypeConcept)
            RETURN c.id AS clause_id, t.id AS concept_id
        ''', snapshot=project['graph']['snapshot_id']).data()


def make_plan(project, profile=None, document_id=None):
    profile = profile or read_profile()
    document_id = document_id or project.get('review_document_id') or project['documents'][0]['id']
    document = next((d for d in project['documents'] if d['id'] == document_id), None)
    if document is None:
        raise ValueError('请选择当前项目中的一份待评审文档')
    mounts = bindings(project)
    dimensions = []
    for role in profile['dimensions'].values():
        matching = {m['clause_id'] for m in mounts if m['concept_id'] in {'type:' + v for v in role['concept_uris']}}
        batches, coverage = [], []
        for document in project['documents']:
            if document['id'] != document_id:
                continue
            if document['type'] not in role['target_types']:
                continue
            selected = set(matching)
            if role['selection'] == 'concept':
                for parent in document['sections']:
                    if parent['id'] in matching:
                        selected.update(s['id'] for s in document['sections']
                                        if s['path'][:len(parent['path'])] == parent['path'])
            target = [b for b in document['blocks'] if b['text'].strip()
                      and (role['selection'] == 'document' or b['clause_id'] in selected)]
            if not target:
                continue
            coverage.append({'document_id': document['id'], 'document_title': document['title'],
                             'block_ids': [b['id'] for b in target], 'characters': sum(len(b['text']) for b in target)})
        if coverage:
            batches.append({'id': key(), 'document_id': coverage[0]['document_id'], 'section_id': '',
                            'title': role['label'], 'document_title': '、'.join(c['document_title'] for c in coverage),
                            'coverage': coverage, 'target_ids': [bid for c in coverage for bid in c['block_ids']],
                            'characters': sum(c['characters'] for c in coverage), 'status': 'pending'})
        dimensions.append({**role, 'status': 'pending' if batches else 'not_applicable', 'batches': batches,
                           'coverage': coverage, 'block_count': sum(len(c['block_ids']) for c in coverage),
                           'matched_clauses': len(matching),
                           'reason': '' if batches else '当前材料未匹配到本维度声明的文档类型或本体概念；未据此判断项目存在缺项'})
    return {'profile': profile, 'snapshot_id': project['graph']['snapshot_id'], 'dimensions': dimensions,
            'strategy': 'incremental_document_review', 'document_id': document_id,
            'document_title': next(d['title'] for d in project['documents'] if d['id'] == document_id),
            'dimension_count': len(dimensions), 'applicable_count': sum(bool(d['batches']) for d in dimensions),
            'batch_count': sum(len(d['batches']) for d in dimensions)}
