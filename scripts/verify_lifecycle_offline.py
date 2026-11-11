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


class OfflineModel:
    def __init__(self):
        self.calls = 0

    def bind(self, **kwargs):
        assert kwargs['response_format']['json_schema']['strict'] is True
        return self

    async def astream(self, messages):
        self.calls += 1
        assert len(messages) == 2
        yield AIMessageChunk(content='{"summary":"离线数据流验证，不代表真实内容评审结果。","findings":[]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    original = core.DATA / args.project_id / 'project.json'
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    project = core.load(args.project_id)
    project['runs'], project['findings'], project['cycles'] = [], [], []
    events, model = [], OfflineModel()

    async def emit(value):
        events.append(value)

    with TemporaryDirectory() as directory:
        core.DATA = Path(directory)
        cycle = asyncio.run(run_all(project, emit, model_factory=lambda: model))
        assert cycle['status'] == 'completed', cycle.get('error')
        assert model.calls == cycle['batch_count']
        for dimension in cycle['dimensions']:
            expected = [bid for item in dimension['coverage'] for bid in item['block_ids']]
            actual = [bid for batch in dimension['batches'] for bid in batch['target_ids']]
            assert actual == expected and len(actual) == len(set(actual))
        for run in project['runs']:
            context, manifest = run['context'], run['input_manifest']
            assert manifest['evidence_ids'] == [b['id'] for b in context['evidence']]
            assert manifest['path_ids'] == [p['id'] for p in context['paths']]
        json.dumps(project)
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    result = {'mode': 'offline_with_real_neo4j', 'project_id': args.project_id,
              'status': cycle['status'], 'dimensions': cycle['dimension_count'],
              'batches': cycle['batch_count'], 'simulated_model_calls': model.calls,
              'real_model_calls': 0, 'original_project_unchanged': True,
              'runs': [{'perspective': r['perspective'], 'targets': len(r['context']['target']),
                        'evidence': len(r['context']['evidence']), 'paths': len(r['context']['paths'])}
                       for r in project['runs']]}
    output = ROOT / 'data/acceptance/lifecycle-offline.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
