from pathlib import Path
import subprocess
import importlib.util
from unittest.mock import Mock,patch
import signal
import os
import shutil
import sys

ROOT=Path(__file__).parents[1]


def test_single_command_launchers_expose_flags_without_starting_services(tmp_path):
    checkout=tmp_path/'checkout'
    checkout.mkdir()
    shutil.copytree(ROOT/'scripts',checkout/'scripts')
    for command in ('start-main','start-sandbox'):
        shutil.copy2(ROOT/command,checkout/command)
    assert not (checkout/'.venv').exists()
    # CI installs into its active interpreter, not necessarily a project-local virtualenv.
    environment=dict(os.environ,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ.get('PATH',''))
    for command in ('start-main','start-sandbox'):
        assert os.access(ROOT/command,os.X_OK)
        result=subprocess.run([str(checkout/command),'--help'],cwd=tmp_path,env=environment,capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        for flag in ('--port','--frontend-port','--check','--backend-only'):
            assert flag in result.stdout


def test_vite_receives_matching_api_and_admin_proxy_and_only_own_child_is_stopped():
    spec=importlib.util.spec_from_file_location('dev_server',ROOT/'scripts/queue_dev_server.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    process=Mock(pid=12345)
    process.poll.return_value=None
    with patch.object(module.subprocess,'Popen',return_value=process) as popen,patch.object(module.time,'sleep'),patch.object(module.os,'killpg') as stop:
        with module.frontend_server(ROOT,backend_port=8011,frontend_port=5181):pass
        environment=popen.call_args.kwargs['env']
        assert environment['VITE_API_BASE_URL']=='http://localhost:8011'
        assert environment['VITE_PROXY_TARGET']=='http://127.0.0.1:8011'
        if os.name=='posix':stop.assert_called_once_with(12345,signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)
