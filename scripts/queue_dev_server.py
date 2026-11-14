"""Shared Vite lifecycle; each launcher only stops its own child process group."""
from contextlib import contextmanager
import os
import signal
import subprocess
import time


@contextmanager
def frontend_server(root, *, backend_port, frontend_port, enabled=True):
    process=None
    try:
        if enabled:
            environment=dict(os.environ,VITE_API_BASE_URL=f'http://localhost:{backend_port}',
                             VITE_PROXY_TARGET=f'http://127.0.0.1:{backend_port}')
            process=subprocess.Popen(['npm','run','dev','--','--host','localhost','--port',str(frontend_port),'--strictPort'],
                cwd=root/'frontend/courses-front',env=environment,start_new_session=os.name=='posix')
            time.sleep(1)
            if process.poll() is not None:
                raise RuntimeError(f'Frontend не запустился. Проверь, свободен ли порт {frontend_port}.')
        yield
    finally:
        if process and process.poll() is None:
            if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
                process.wait()


def require_free_backend(port):
    import socket
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',port))
        except OSError as error:
            raise ValueError(f'Порт backend {port} занят. Остановите прежний запуск через Ctrl+C или задайте --port.') from error
