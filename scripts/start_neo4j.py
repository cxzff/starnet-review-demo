"""Start an isolated local Neo4j instance, reusing an installed distribution."""
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
data = root / 'data'
configuration = data / 'neo4j-conf'
configuration.mkdir(parents=True, exist_ok=True)
for name in ['neo4j-data', 'neo4j-logs', 'neo4j-run']:
    (data / name).mkdir(exist_ok=True)
(configuration / 'neo4j.conf').write_text(f'''server.default_listen_address=127.0.0.1
server.bolt.enabled=true
server.bolt.listen_address=:7689
server.http.enabled=true
server.http.listen_address=:7476
server.https.enabled=false
dbms.security.auth_enabled=false
server.memory.heap.initial_size=256m
server.memory.heap.max_size=512m
server.memory.pagecache.size=128m
server.directories.data={data}/neo4j-data
server.directories.logs={data}/neo4j-logs
server.directories.run={data}/neo4j-run
''')
executable = os.getenv('NEO4J_BIN') or shutil.which('neo4j')
if not executable:
    raise SystemExit('请安装 Neo4j Community，并通过 NEO4J_BIN 指向 bin/neo4j')
subprocess.run([executable, 'start'], env={**os.environ, 'NEO4J_CONF': str(configuration)}, check=True)
