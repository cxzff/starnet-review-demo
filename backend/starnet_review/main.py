import asyncio
import json
import os
from typing import Literal
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .core import DATA, ROOT, ROLES, import_files, list_projects, load, save, uri
from .revisions import accept
from .agent import run_review
from .cycles import run_all
from .ontology import read_profile
from .lifecycle import lifecycle

load_dotenv(ROOT / '.env')
app = FastAPI(title='本体驱动文档内容评审 Demo', version='0.1.0')
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5180', 'http://localhost:5180'],
                   allow_methods=['*'], allow_headers=['*'])
running = set()


@app.exception_handler(FileNotFoundError)
async def not_found(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404, content={'detail': '项目或文件不存在'})


@app.exception_handler(ValueError)
async def invalid_input(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={'detail': str(exc)})


@app.get('/api/config')
def config():
    return {'agents': read_profile()['dimensions'], 'model': os.getenv('LLM_MODEL_NAME', 'qwen3.8-flash'),
            'model_configured': bool(os.getenv('LLM_API_KEY')), 'provided_document': bool(os.getenv('DEMO_DOCUMENT')),
            'provided_sources': [{'id': i, 'name': Path(os.getenv(k)).stem} for i, k in [(1, 'DEMO_DOCUMENT'), (2, 'DEMO_DOCUMENT_2')] if os.getenv(k)]}


@app.get('/api/health')
def health():
    from clausegraph.publishing import neo4j_driver
    with neo4j_driver(uri()) as driver:
        driver.verify_connectivity()
    return {'status': 'ok', 'graph': 'Neo4j', 'engine': 'ClauseGraph'}


@app.get('/api/projects')
def projects():
    return list_projects()


@app.get('/api/projects/{project_id}')
def project(project_id: str):
    return load(project_id)


@app.get('/api/projects/{project_id}/lifecycle')
async def ontology_lifecycle(project_id: str, run_id: str | None = None):
    return await asyncio.to_thread(lifecycle, load(project_id), run_id)


@app.post('/api/import-provided')
async def provided(sample: int = 1):
    if sample not in (1, 2):
        raise HTTPException(404, '样例不存在')
    path = Path(os.getenv('DEMO_DOCUMENT' if sample == 1 else 'DEMO_DOCUMENT_2', ''))
    if not path.exists() or str(path) == '.':
        raise HTTPException(404, '尚未配置提供的合册，请上传文档')
    paths = sorted(path.glob('*.docx')) if path.is_dir() else [path]
    if not paths:
        raise HTTPException(404, '目录中没有 DOCX 文档')
    return await asyncio.to_thread(import_files, [(p, p.name, None) for p in paths])


@app.post('/api/import')
async def upload(documents: list[UploadFile] = File(...), project_id: str = Form(''), document_type: str = Form('')):
    if project_id in running:
        raise HTTPException(409, '请先停止正在进行的评审')
    with TemporaryDirectory() as directory:
        files = []
        for index, document in enumerate(documents):
            suffix = Path(document.filename or '').suffix.lower()
            if suffix not in {'.docx', '.md', '.markdown'}:
                raise HTTPException(422, 'V1 支持 DOCX 与 Markdown，合册会按部分拆分')
            path = Path(directory) / f'{index}{suffix}'
            data = await document.read()
            if len(data) > 32 * 1024 * 1024:
                raise HTTPException(413, '单个文档不能超过 32MB')
            path.write_bytes(data)
            files.append((path, document.filename, document_type or None))
        return await asyncio.to_thread(import_files, files, load(project_id) if project_id else None)


class ReviewRequest(BaseModel):
    document_id: str
    section_id: str
    perspective: str
    focus: str = ''


class FullReviewRequest(BaseModel):
    focus: str = ''


@app.post('/api/projects/{project_id}/review-all')
async def review_all(project_id: str, request: FullReviewRequest):
    project = load(project_id)
    if project_id in running:
        raise HTTPException(409, '此项目已有评审正在进行')
    running.add(project_id)
    queue = asyncio.Queue()

    async def produce():
        try:
            await run_all(project, queue.put, request.focus)
        except Exception as exc:
            await queue.put({'kind': 'error', 'message': f'{type(exc).__name__}: {str(exc)[:250]}'})
        finally:
            running.discard(project_id)
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + '\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-cache'})


@app.post('/api/projects/{project_id}/review')
async def review(project_id: str, request: ReviewRequest):
    project = load(project_id)
    if request.perspective not in ROLES or request.document_id not in {d['id'] for d in project['documents']}:
        raise HTTPException(422, '请选择已有文档及评审视角')
    if project_id in running:
        raise HTTPException(409, '此项目已有评审正在进行')
    running.add(project_id)
    queue = asyncio.Queue()

    async def produce():
        try:
            await run_review(project, request.model_dump(), queue.put)
        finally:
            running.discard(project_id)
            await queue.put(None)

    async def stream():
        task = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + '\n'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(stream(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-cache'})


class ApplyRequest(BaseModel):
    replacement: str
    category: Literal['内容冲突', '覆盖不足', '证据不足', '改进建议'] | None = None


@app.post('/api/projects/{project_id}/findings/{finding_id}/accept')
async def apply(project_id: str, finding_id: str, request: ApplyRequest):
    if project_id in running:
        raise HTTPException(409, '请先停止评审再应用修改')
    try:
        return await asyncio.to_thread(accept, load(project_id), finding_id, request.replacement, request.category)
    except (ValueError, StopIteration) as exc:
        raise HTTPException(409, str(exc) or '意见不存在') from exc


@app.post('/api/projects/{project_id}/findings/{finding_id}/dismiss')
def dismiss(project_id: str, finding_id: str):
    if project_id in running:
        raise HTTPException(409, '请先停止评审')
    project = load(project_id)
    finding = next((f for f in project['findings'] if f['id'] == finding_id), None)
    if not finding:
        raise HTTPException(404, '意见不存在')
    finding['status'] = 'dismissed'
    save(project)
    return project


@app.get('/api/projects/{project_id}/documents/{document_id}/download')
def download(project_id: str, document_id: str):
    project = load(project_id)
    document = next(d for d in project['documents'] if d['id'] == document_id)
    return FileResponse(DATA / project_id / document['source_file'], filename='评审修订_' + document['filename'])
