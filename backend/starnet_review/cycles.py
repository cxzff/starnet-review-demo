"""Run every ontology-defined dimension independently; aggregate state without another model."""
import asyncio
import time

from .agent import create_model, run_review
from .core import key, save
from .planning import make_plan
from .content import ensure_content_ontologies


async def run_all(project, emit, focus='', model_factory=None, plan=None, content_builder=ensure_content_ontologies,
                  document_id=None):
    started = time.perf_counter()
    plan = plan or await asyncio.to_thread(make_plan, project, document_id=document_id)
    cycle = {'id': key(), **plan, 'status': 'running', 'started_at': time.time(), 'run_ids': [], 'events': [], 'metrics': {}}
    project.setdefault('cycles', []).append(cycle)
    project['review_document_id'] = plan['document_id']
    current_document = next(d for d in project['documents'] if d['id'] == plan['document_id'])
    cycle['document_sha256'] = current_document['sha256']
    upstream_types = plan['profile']['dependencies'].get(current_document['type'], [])
    cycle['upstream_versions'] = {d['id']: d['sha256'] for d in project['documents'] if d['type'] in upstream_types}
    slots = asyncio.Semaphore(2)
    shared_model = None
    construction_metrics = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}
    cycle['construction_metrics'] = construction_metrics

    async def event(kind, **values):
        value = {'kind': kind, 'cycle_id': cycle['id'], 'time': time.time(), **values}
        cycle['events'].append({k: v for k, v in value.items() if k != 'cycle'})
        await emit(value)

    async def forward(value):
        await emit({**value, 'cycle_id': cycle['id']})

    async def dimension(role):
        nonlocal shared_model
        if not role['batches']:
            await event('dimension', perspective=role['id'], status=role['status'], title=role['label'], detail=role['reason'])
            return
        for batch in role['batches']:
            async with slots:
                role['status'] = batch['status'] = 'running'
                await event('dimension', perspective=role['id'], status='running', title=role['label'], batch_id=batch['id'])
                save(project)
                if shared_model is None:
                    shared_model = model_factory() if model_factory else create_model()
                run = await run_review(project, {'document_id': batch['document_id'], 'section_id': batch['section_id'],
                    'perspective': role['id'], 'role': role, 'profile': plan['profile'], 'dependencies': plan['profile']['dependencies'],
                    'target_ids': batch['target_ids'], 'coverage': batch.get('coverage'),
                    'cycle_id': cycle['id'], 'batch_id': batch['id'], 'focus': focus},
                    forward, shared_model)
                cycle['run_ids'].append(run['id'])
                batch['status'], batch['run_id'] = run['status'], run['id']
                await event('batch_done', perspective=role['id'], batch_id=batch['id'], run_id=run['id'], status=run['status'])
                if run['status'] != 'completed':
                    role['status'] = 'partial'
                    for pending in role['batches']:
                        if pending['status'] == 'pending':
                            pending['status'] = 'not_run'
                    break
        else:
            role['status'] = 'completed'
        await event('dimension', perspective=role['id'], status=role['status'], title=role['label'])
        save(project)

    workers = []
    try:
        shared_model = model_factory() if model_factory else create_model()
        await content_builder(project, shared_model, forward, construction_metrics, document_ids=[plan['document_id']])
        cycle['snapshot_id'] = project['graph']['snapshot_id']
        await event('plan', title=f"仅评审当前文档，启用 {plan['applicable_count']} 个适用视角", cycle=cycle)
        save(project)
        workers = [asyncio.create_task(dimension(role)) for role in cycle['dimensions']]
        await asyncio.gather(*workers)
        cycle['status'] = ('completed' if all(d['status'] in ('completed', 'not_applicable') for d in cycle['dimensions']) else 'partial')
    except asyncio.CancelledError:
        cycle['status'] = 'stopped'
        raise
    except Exception as exc:
        cycle['status'] = 'failed'
        cycle['error'] = f'{type(exc).__name__}: {str(exc)[:250]}'
        await event('error', message=cycle['error'])
    finally:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for role in cycle['dimensions']:
            if role['status'] == 'running' or (role['status'] == 'pending' and cycle['status'] in ('failed', 'stopped')):
                role['status'] = 'stopped'
            for batch in role['batches']:
                if batch['status'] == 'running':
                    batch['status'] = 'stopped'
                elif batch['status'] == 'pending' and cycle['status'] in ('failed', 'stopped'):
                    batch['status'] = 'not_run'
        runs = [r for r in project['runs'] if r.get('cycle_id') == cycle['id']]
        construction_metrics['total_tokens'] = construction_metrics['input_tokens'] + construction_metrics['output_tokens']
        cycle['metrics']['review_calls'] = sum(r['metrics'].get('model_calls', 0) for r in runs)
        cycle['metrics']['construction_calls'] = construction_metrics['model_calls']
        for name in ['model_calls', 'input_tokens', 'output_tokens', 'total_tokens']:
            cycle['metrics'][name] = sum(r['metrics'].get(name, 0) for r in runs)
            cycle['metrics'][name] += construction_metrics.get(name, 0)
        cycle['metrics']['elapsed_seconds'] = round(time.perf_counter() - started, 2)
        cycle['metrics']['completed_batches'] = sum(b['status'] == 'completed' for d in cycle['dimensions'] for b in d['batches'])
        save(project)
        if shared_model is not None and model_factory is None:
            await shared_model.http_async_client.aclose()
            shared_model.http_client.close()
    await event('cycle_done', cycle=cycle, title='当前文档的适用维度评审已结束')
    return cycle
