"""Профиль читает внешние данные, но допускает изменения только локальной очереди."""
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('queue_live_script',ROOT/'scripts/run_queue_live.py')
live=importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


@pytest.mark.parametrize('method,path',[
    ('POST','/grade'),('POST',f'/courses/{live.COURSE_ID}/groups/{live.GROUP_ID}/register'),
    ('POST','/join/course/lab'),('GET','/join/course/lab'),
    ('PUT',f'/courses/{live.COURSE_ID}/edit'),
    ('POST','/admin/courses/other/groups/4434/queue/actions'),
    ('POST',f'/admin/courses/{live.COURSE_ID}/groups/other/queue/actions'),
])
def test_external_mutations_are_blocked(method,path):
    assert not live.allowed_path(path,method)


def test_local_attendance_and_teacher_operations_remain_available():
    for method,path in [('POST',f'/courses/{live.COURSE_ID}/groups/{live.GROUP_ID}/queue/attendance'),
                        ('POST',f'/admin/courses/{live.COURSE_ID}/groups/{live.GROUP_ID}/queue/actions'),
                        ('GET',f'/courses/{live.COURSE_ID}/groups'),('POST','/admin/login')]:
        assert live.allowed_path(path,method)


def test_data_directory_cannot_expose_students_in_git():
    with pytest.raises(ValueError): live.local_directory('docs/live-data')


def test_local_course_selects_actual_sheet_columns_without_modifying_original():
    original=live.base_course()
    grid=[['','','GitHub',''],['№','ФИО','','ЛР1']]
    selected=live.select_sheet_labs(original,grid)
    assert list(selected['labs'])==['1']
    assert len(original['labs'])>1
    assert selected['queue']['required_labs']==['1']
    assert selected['queue']['registration_disabled']
    assert selected['queue']['sheet_source']['type']=='public_xlsx'
    assert 'worksheets' not in selected['queue']['sheet_source']


def test_isolated_live_profile_http_without_external_writes():
    # Отдельный процесс: main.app уже используется другими HTTP-тестами и нельзя
    # добавлять его middleware/менять глобальные каталоги после старта.
    import os
    import subprocess
    import sys
    code = r'''
import contextlib, importlib.util, io, os, tempfile, time
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
root=Path.cwd()
spec=importlib.util.spec_from_file_location('live',root/'scripts/run_queue_live.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
grid=[['','','GitHub','2026-11-01'],['№','ФИО','','ЛР1'],['1','Тестовый Студент','test-user','v']]
course=live.select_sheet_labs(live.base_course(),grid)
with tempfile.TemporaryDirectory(dir=root/'data') as temp:
    with contextlib.redirect_stdout(io.StringIO()):
        app=live.create_app(Path(temp),course,[{'key':'test-live-session','starts_at':int(time.time())+3600,'ends_at':int(time.time())+7200}])
    os.environ['QUEUE_WORKER_ENABLED']='0'
    import main
    import grading.defense_queue.public_sheet as public
    def never(*args,**kwargs): raise AssertionError('Unexpected credentials or external request')
    with patch.object(public,'read_public_grid',lambda *args:grid), patch.object(public,'read_public_workbook',lambda *args:{'4434':grid}), patch.object(main,'sheets_client',never), patch('requests.get',never):
        client=TestClient(app)
        base='/courses/'+live.COURSE_ID
        assert main.validate_course_index()
        assert client.get(base).json()['queue_registration_disabled']
        assert client.get(base+'/groups').json()==['4434']
        assert client.get(base+'/groups/4434/labs').json()==['ЛР1']
        queue=base+'/groups/4434/queue'
        data=client.get(queue).json()
        assert len(data['entries'])==1
        response=client.post(queue+'/attendance',json={'surname':'Тестовый','name':'Студент','patronymic':'','github':'test-user','session_id':data['session']['session_id'],'will_attend':True})
        assert response.status_code==200,response.text
        assert client.get(queue).json()['entries'][0]['will_attend']
        for path in ('/grade',base+'/groups/4434/register',base+'/edit'):
            assert client.post(path,json={}).status_code==409
'''
    (ROOT/'data').mkdir(exist_ok=True)
    env=dict(os.environ,GITHUB_TOKEN='public-read-only',QUEUE_WORKER_ENABLED='0')
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def test_reset_archives_local_history_and_keeps_configuration(tmp_path):
    import sqlite3
    database=tmp_path/'queue.sqlite3'
    with sqlite3.connect(database) as con:
        con.execute('CREATE TABLE examples(value)')
        con.execute("INSERT INTO examples VALUES ('synthetic')")
    config=tmp_path/'config.json'
    config.write_text('{}')
    archive=live.reset_local_state(tmp_path)
    assert not database.exists()
    assert config.exists()
    with sqlite3.connect(archive) as con:
        assert con.execute('SELECT value FROM examples').fetchone()[0]=='synthetic'
    assert live.reset_local_state(tmp_path) is None
