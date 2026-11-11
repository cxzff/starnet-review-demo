"""Exercise a real project's Neo4j retrieval and full lifecycle without any model API."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from dotenv import load_dotenv
from langchain_core.messages import AIMessageChunk
from starnet_review import core
from starnet_review.cycles import run_all
from starnet_review.lifecycle import lifecycle, read_snapshot


class OfflineModel:
    def __init__(self):
        self.calls = 0

    def bind(self, **kwargs):
        assert kwargs['response_format']['json_schema']['strict'] is True
        self.schema = kwargs['response_format']['json_schema']['name']
        return self

    async def astream(self, messages):
        self.calls += 1
        assert len(messages) == 2
        if self.schema == 'document_content':
            source = json.loads(messages[1].content)['全文'][-1]['原文'][:200]
            output = {'summary': '离线结构验证，不代表真实内容抽取。',
                      'concepts': [{'label': '离线来源片段', 'quote': source}, {'label': source, 'quote': source}],
                      'relations': [{'subject': '离线来源片段', 'relation': '离线结构验证', 'object': source, 'quote': source}]}
        else:
            output = {'summary': '离线数据流验证，不代表真实内容评审结果。', 'findings': []}
        yield AIMessageChunk(content=json.dumps(output, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    original = core.DATA / args.project_id / 'project.json'
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    project = core.load(args.project_id)
    project['id'] = 'offline-' + core.key()
    project.pop('content_ontologies', None)
    project['graph'].pop('content_versions', None)
    project['runs'], project['findings'], project['cycles'] = [], [], []
    events, model = [], OfflineModel()

    async def emit(value):
        events.append(value)

    with TemporaryDirectory() as directory:
        core.DATA = Path(directory)
        order = ['taskbook', 'srs', 'sdd', 'stp', 'std', 'str', 'other']
        rounds = []
        for document in sorted(project['documents'], key=lambda d: order.index(d['type'])):
            previous_runs = len(project['runs'])
            previous_ontologies = json.dumps(project.get('content_ontologies', {}), sort_keys=True)
            cycle = asyncio.run(run_all(project, emit, model_factory=lambda: model, document_id=document['id']))
            assert cycle['status'] == 'completed', cycle.get('error')
            assert cycle['metrics']['construction_calls'] == 1
            assert all(r['document_id'] == document['id'] for r in project['runs'][previous_runs:])
            before_ontologies = json.loads(previous_ontologies)
            assert all(project['content_ontologies'][k] == v for k, v in before_ontologies.items())
            for dimension in cycle['dimensions']:
                assert all(item['document_id'] == document['id'] for item in dimension['coverage'])
                expected = [bid for item in dimension['coverage'] for bid in item['block_ids']]
                actual = [bid for batch in dimension['batches'] for bid in batch['target_ids']]
                assert actual == expected and len(actual) == len(set(actual))
            rounds.append({'document_type': document['type'], 'tasks': cycle['applicable_count'], 'status': cycle['status']})
        for run in project['runs']:
            context, manifest = run['context'], run['input_manifest']
            assert manifest['evidence_ids'] == [b['id'] for b in context['evidence']]
            assert manifest['path_ids'] == [p['id'] for p in context['paths']]
        native, _ = read_snapshot(project)
        assert all(n['document_id'] in {d['id'] for d in project['documents']} for n in native if n['kind'] == 'ContentConcept')
        for document in project['documents']:
            view = lifecycle(project, document_id=document['id'])
            assert view['cycle_current'], '添加下游文档不应重置已完成的上游评审'
            assert view['plan']['document_id'] == document['id']
            assert not any(n['kind'] in ('agent', 'input', 'dimension') for n in view['ontology_graph']['nodes'])
        json.dumps(project)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    result = {'mode': 'offline_with_real_neo4j', 'project_id': args.project_id,
              'status': cycle['status'], 'rounds': rounds,
              'simulated_model_calls': model.calls,
              'simulated_construction_calls': sum(c['metrics']['construction_calls'] for c in project['cycles']),
              'simulated_review_calls': sum(c['metrics']['review_calls'] for c in project['cycles']),
              'real_model_calls': 0, 'original_project_unchanged': True,
              'runs': [{'perspective': r['perspective'], 'targets': len(r['context']['target']),
                        'evidence': len(r['context']['evidence']), 'paths': len(r['context']['paths'])}
                       for r in project['runs']]}
    output = ROOT / 'data/acceptance/content-growth-offline.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
