"""Sandbox stays isolated; real registration/grading/queue handlers run in a subprocess."""
from pathlib import Path
import subprocess
import os
import sys

ROOT=Path(__file__).parents[1]


def test_complete_sandbox_journey_has_no_external_network():
    script=r'''
import tempfile,json,sys
from pathlib import Path
import importlib.util
from fastapi.testclient import TestClient
spec=importlib.util.spec_from_file_location('sandbox_script',Path('scripts/run_queue_sandbox.py'))
sandbox=importlib.util.module_from_spec(spec);spec.loader.exec_module(sandbox)
Path('data').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sandbox-test-',dir='data') as directory:
    app=sandbox.create_app(Path(directory))
    import main
    from grading.defense_queue.runtime import queue_store
    with TestClient(app) as client:
        assert client.post('/sandbox/reset').status_code==401
        assert client.post('/admin/login',json={'login':'sandbox','password':'sandbox123'}).status_code==200
        assert client.get('/sandbox').status_code==200
        assert client.get('/admin/courses/queue-sandbox/queue/settings').json()['preferences']['auto_repeat']
        student={'surname':'Новиков','name':'Никита','patronymic':'Никитич','github':'demo-new'}
        path='/courses/queue-sandbox/groups/DEMO-01'
        reg=client.post(path+'/register',json=student)
        assert reg.status_code==200,reg.text
        for scenario,status,mark in [('pending','pending',''),('failure','updated','x'),('success','updated','v-2')]:
            assert client.post('/sandbox/ci',json={'login':'demo-new','value':scenario}).status_code==200
            result=client.post(path+'/labs/1/grade',json={'github':'demo-new'})
            assert result.status_code==200,result.text
            assert result.json()['status']==status,result.text
            state=client.get('/sandbox/state').json()
            assert state['grids']['DEMO-01'][-1][3]==mark,state
        session=client.get(path+'/queue').json()['session']['session_id']
        assert client.post(path+'/queue/attendance',json=dict(student,session_id=session,will_attend=True)).status_code==200
        assert client.post('/sandbox/start').status_code==200
        admin='/admin/courses/queue-sandbox/groups/DEMO-01/queue'
        view=client.get(admin+'?session_id='+session).json()
        assert len(view['round_entries'])==4
        eid=view['round_entries'][0]['attempt']['entry_id']
        def action(kind,**extra):
            result=client.post(admin+'/actions',json=dict(session_id=session,action=kind,request_id=kind+str(extra),**extra))
            assert result.status_code==200,result.text
            return result.json()
        action('call',entry_id=eid)
        failed=action('fail',entry_id=eid)
        assert failed['round_entries'][0]['attempt']['status']=='failed'
        assert failed['round_entries'][-1]['is_repeat']
        action('undo',command_id=failed['undo']['command_id'])
        passed=action('pass',entry_id=eid)
        assert passed['round_entries'][0]['attempt']['status']=='defended'
        assert len(passed['round_entries'])==4 # next lab is added explicitly
        action('undo',command_id=passed['undo']['command_id'])
        action('uncall',entry_id=eid)
        missing=action('absent',entry_id=eid)
        absent=next(row for row in missing['absent_entries'] if row['github']=='demo-ivan')
        returned=action('arrived',student_id=absent['student_id'])
        assert returned['round_entries'][-1]['github']=='demo-ivan'
        duplicate=client.post(admin+'/actions',json=dict(session_id=session,action='add_student',student_id=absent['student_id'],request_id='duplicate'))
        assert duplicate.status_code==409,duplicate.text
        assert client.put('/admin/courses/queue-sandbox/queue/preferences',json={'auto_repeat':False,'undo_seconds':10}).status_code==200
        assert client.post('/sandbox/mark',json={'login':'demo-anna','lab':1,'value':'vv'}).status_code==200
        assert client.post('/sandbox/finish').status_code==200
        assert client.get(path+'/queue').json()['read_only'] is True
        assert client.post('/sandbox/reopen').status_code==200
        assert client.get(path+'/queue').json()['read_only'] is False
        old_database=str(queue_store().path)
        assert client.post('/sandbox/reset').status_code==200
        assert str(queue_store().path)!=old_database
        assert len(client.get(path+'/queue').json()['entries'])==6
        assert client.get('/courses/real-teacher-course').status_code==409
print('sandbox full journey OK')
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=dict(os.environ),text=True,capture_output=True,timeout=60)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'sandbox full journey OK' in result.stdout
