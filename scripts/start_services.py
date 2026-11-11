"""Local desktop launcher; all processes use this project's environment and data."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

root = Path(__file__).resolve().parents[1]
logs = root / 'data' / 'service-logs'
logs.mkdir(parents=True, exist_ok=True)


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def launch(name, command, directory):
    with (logs / (name + '.log')).open('ab') as stream:
        return subprocess.Popen(command, cwd=directory, stdout=stream, stderr=stream, start_new_session=True)


if not listening(7689):
    subprocess.run([sys.executable, str(root / 'scripts/start_neo4j.py')], cwd=root, check=True)
if not listening(8010):
    launch('backend', [str(root / '.venv/bin/python'), '-m', 'uvicorn', 'starnet_review.main:app',
                       '--app-dir', str(root / 'backend'), '--host', '127.0.0.1', '--port', '8010'], root)
if not listening(5180):
    npm = shutil.which('npm')
    if not npm:
        raise SystemExit('找不到 npm，请先安装 Node.js 并执行 frontend 的依赖安装')
    launch('frontend', [npm, 'run', 'dev'], root / 'frontend')

for _ in range(40):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=2) as response:
            ready = response.status == 200 and listening(5180)
        if ready:
            print('已启动：http://127.0.0.1:5180/')
            print('日志目录：' + str(logs))
            if '--no-open' not in sys.argv:
                webbrowser.open('http://127.0.0.1:5180/')
            break
    except OSError:
        pass
    time.sleep(.5)
else:
    raise SystemExit('服务尚未就绪，请查看日志：' + str(logs))
