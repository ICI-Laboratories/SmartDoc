"""Disposable browser-test backend, never used by the application deployment."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    if not urlparse(os.environ.get('SARA_DATABASE_URL', '')).path.endswith('_test'):
        raise SystemExit('Set SARA_E2E_DATABASE_URL to a disposable PostgreSQL database ending in _test.')
    import uvicorn
    from library_service.db import make_pool, migrate
    with make_pool() as pool:
        migrate(pool)
    with tempfile.TemporaryDirectory(prefix='sara-docreader-browser-') as storage:
        os.environ.update(SARA_STORAGE=storage,SARA_LLM_URL='',SARA_LLM_MODEL='',SARA_EMBEDDING_MODEL='')
        worker=subprocess.Popen([sys.executable,'-m','library_service.worker'],cwd=Path(__file__).resolve().parents[1])
        try:
            uvicorn.run('library_service.api:app',host='127.0.0.1',port=8046,log_level='warning')
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill();worker.wait()
