"""Run every ontology-defined dimension independently; aggregate state without another model."""
import asyncio
import time

from .agent import create_model, run_review
from .core import key, save
from .planning import make_plan


async def run_all(project, emit, focus='', model_factory=None, plan=None):
    started = time.perf_counter()
    plan = plan or await asyncio.to_thread(make_plan, project)
    cycle = {'id': key(), **plan, 'status': 'running', 'started_at': time.time(), 'run_ids': [], 'events': [], 'metrics': {}}
    project.setdefault('cycles', []).append(cycle)
    slots = asyncio.Semaphore(2)
    shared_model = None

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
                    'target_ids': batch['target_ids'], 'cycle_id': cycle['id'], 'batch_id': batch['id'], 'focus': focus},
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
        await event('plan', title=f"按评审本体自动安排 {plan['dimension_count']} 个维度", cycle=cycle)
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
        for name in ['model_calls', 'input_tokens', 'output_tokens', 'total_tokens']:
            cycle['metrics'][name] = sum(r['metrics'].get(name, 0) for r in runs)
        cycle['metrics']['elapsed_seconds'] = round(time.perf_counter() - started, 2)
        cycle['metrics']['completed_batches'] = sum(b['status'] == 'completed' for d in cycle['dimensions'] for b in d['batches'])
        save(project)
        if shared_model is not None and model_factory is None:
            await shared_model.http_async_client.aclose()
            shared_model.http_client.close()
    await event('cycle_done', cycle=cycle, title='全维度评审已结束')
    return cycle
