"""HTTP-контракт очереди: реальные FastAPI/SQLite, искусственное чтение Sheets."""
from datetime import datetime, timezone
import time

import pytest
from fastapi.testclient import TestClient

import main
from grading.defense_queue.storage import QueueStore
from grading.defense_queue.sync import sync_group

ORIGINAL_QUEUE_SYNC = main._sync_defense_queue


@pytest.fixture
def queue_client(tmp_path, monkeypatch):
    monkeypatch.setenv('QUEUE_WORKER_ENABLED','0')
    monkeypatch.setattr(main.limiter,'enabled',False)
    now=int(time.time())
    course={'name':'Предмет','timezone':'UTC','google':{'student-name-column':1},
            'queue':{'enabled':True},'labs':{'1':{'short-name':'ЛР1'},'2':{'short-name':'ЛР2'}}}
    grid=[['','','GitHub','2026-11-01','2026-11-08'],['№','ФИО','','ЛР1','ЛР2'],
          ['1','Фамилия Имя Отчество','login','v','v']]
    store=QueueStore(tmp_path/'queue.db')
    def sync(*args,**kwargs):
        summary=sync_group(store,'c','g',course,grid,parse_deadline=lambda v:datetime.fromisoformat(v).replace(tzinfo=timezone.utc),now=now-60)
        return store,summary
    sync()
    store.create_session('future','c','g','teacher',starts_at=now+3600,ends_at=now+7200,local_date='2026-11-07',now=now-100)
    monkeypatch.setattr(main,'get_course_by_id',lambda _:course)
    monkeypatch.setattr(main,'_sync_defense_queue',sync)
    monkeypatch.setattr('grading.defense_queue.runtime.queue_store',lambda:store)
    client=TestClient(main.app)
    return client,store,course,now


def authenticate(client):
    client.cookies.set('admin_session',main.signer.sign(main.ADMIN_LOGIN.encode()).decode())


@pytest.mark.parametrize('method,path,body',[
    ('GET','/admin/queue/sessions',None),
    ('GET','/admin/courses/c/groups/g/queue?session_id=future',None),
    ('GET','/admin/courses/c/queue/settings',None),
    ('PUT','/admin/courses/c/queue/settings',{'random':False,'deadlines':{}}),
    ('POST','/admin/courses/c/groups/g/queue/actions',{'session_id':'future','action':'call_next'}),
    ('PATCH','/admin/courses/c/groups/g/queue/sessions/future',{'cancel':True}),
    ('PUT','/admin/courses/c/groups/g/queue/schedule',{'config':{'source':'manual'}}),
    ('POST','/admin/courses/c/groups/g/queue/schedule/refresh',None),
])
def test_teacher_routes_require_cookie(queue_client,method,path,body):
    client,*_=queue_client
    assert client.request(method,path,json=body).status_code==401


def test_student_can_confirm_without_regrading(queue_client):
    client,store,_,_=queue_client
    path='/courses/c/groups/g/queue'
    assert client.get(path).json()['entries'][0]['state']=='unconfirmed'
    body={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'LOGIN','session_id':'future','will_attend':True}
    assert client.post(path+'/attendance',json=body).status_code==200
    assert client.get(path).json()['entries'][0]['position']==1
    body['name']='Чужое имя'
    assert client.post(path+'/attendance',json=body).status_code==403


def test_freeze_and_teacher_result(queue_client):
    client,store,_,now=queue_client
    store.create_session('current','c','g','teacher',starts_at=now-30,ends_at=now+600,local_date='2026-11-07',now=now-100)
    with store.connection() as con: cid=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.attendance('current',cid,True,now=now-40)
    path='/courses/c/groups/g/queue'
    body={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'login','session_id':'current','will_attend':False}
    assert client.post(path+'/attendance',json=body).status_code==409
    assert client.get(path).json()['session']['session_id']=='current'
    assert client.get(path).json()['read_only'] is True
    authenticate(client)
    admin='/admin/courses/c/groups/g/queue'
    view=client.post(admin+'/actions',json={'session_id':'current','action':'call_next'})
    assert view.status_code==200
    eid=view.json()['current_entry_id']
    result=client.post(admin+'/actions',json={'session_id':'current','action':'result','entry_id':eid,'outcome':'failed'})
    assert result.status_code==200
    assert result.json()['stats']['failed']==1
    assert result.json()['stats']['waiting']==1
    assert result.json()['entries'][0]['attempt']['is_repeat']==1


def test_settings_and_manual_schedule_without_sheet_access(queue_client):
    client,store,_,now=queue_client
    authenticate(client)
    assert client.put('/admin/courses/c/queue/settings',json={'random':True,'deadlines':{'1':None}}).status_code==200
    response=client.post('/admin/courses/c/groups/g/queue/sessions',json={
        'session_id':'manual','teacher_key':'teacher','starts_at':datetime.fromtimestamp(now+8000,timezone.utc).isoformat(),
        'ends_at':datetime.fromtimestamp(now+9000,timezone.utc).isoformat()})
    assert response.status_code==200
    assert any(row['session_id']=='manual' for row in client.get('/admin/queue/sessions').json())
    with store.connection() as con:
        assert con.execute('SELECT random_override FROM queue_settings').fetchone()[0]==1


def test_queue_dates_support_iana_zone_and_explicit_offset():
    from grading.defense_queue.sync import parse_queue_deadline
    from grading.sheets_client import parse_deadline
    course={'timezone':'UTC+3','queue':{'timezone':'Asia/Tokyo'}}
    parsed=parse_queue_deadline('2026-11-07',course,parse_deadline)
    assert parsed.utcoffset().total_seconds()==9*3600
    assert parsed.hour==23
    explicit=parse_queue_deadline('2026-11-07T15:00:00+02:00',course,parse_deadline)
    assert explicit.utcoffset().total_seconds()==2*3600


@pytest.mark.parametrize('config',[
 {'source':'suai','group_id':'127.0.0.1/?x=','subject':'Предмет','week_anchor':'2026-11-02'},
 {'source':'suai','group_id':'123','subject':42,'week_anchor':'2026-11-02'},
 {'source':'import','lessons':[{}]},
 {'source':'import','lessons':[{'key':'x','starts_at':'2026-11-07T10:00','ends_at':'2026-11-07T11:00'}]},
 {'source':'import','lessons':[{'key':'x','starts_at':True,'ends_at':100}]},
])
def test_malformed_schedule_is_rejected_without_saving(queue_client,config):
    client,store,_,_=queue_client
    authenticate(client)
    response=client.put('/admin/courses/c/groups/g/queue/schedule',json={'config':config})
    assert response.status_code==422,response.text
    with store.connection() as con:
        assert con.execute('SELECT count(*) FROM queue_settings').fetchone()[0]==0


def test_teacher_cannot_add_unconfirmed_student_after_freeze(queue_client):
    client,store,_,now=queue_client
    authenticate(client)
    store.create_session('current','c','g','teacher',starts_at=now-30,ends_at=now+600,local_date='2026-11-07',now=now-100)
    path='/admin/courses/c/groups/g/queue'
    assert client.get(path+'?session_id=current').status_code==200
    sid=store.student('Новый Студент','new-user',now=now)
    cid=store.qualify('c','g',sid,'1',1,deadline_at=None,now=now)
    # The fixture's sheet sync would remove this synthetic new student; keep its admission snapshot here.
    from unittest.mock import patch
    with patch.object(main,'_sync_defense_queue',return_value=(store,{'deadlines_available':True})):
        view=client.get(path+'?session_id=current').json()
        assert cid not in [r['candidate_id'] for r in view['available_candidates']]
        assert cid not in [r['candidate_id'] for r in view['entries']]
        response=client.post(path+'/actions',json={'session_id':'current','action':'add_tail','candidate_id':cid})
    assert response.status_code==409,response.text
    assert 'не подтвердил' in response.json()['detail']


def test_public_default_secret_cannot_forge_admin_cookie(queue_client):
    from itsdangerous import TimestampSigner
    client,*_=queue_client
    client.cookies.set('admin_session',TimestampSigner('super-secret-key').sign(main.ADMIN_LOGIN.encode()).decode())
    assert client.get('/admin/queue/sessions').status_code==401


def test_https_admin_cookie_is_secure(queue_client):
    from fastapi.testclient import TestClient
    with TestClient(main.app,base_url='https://testserver') as client:
        response=client.post('/admin/login',json={'login':main.ADMIN_LOGIN,'password':main.ADMIN_PASSWORD})
        assert response.status_code==200
        cookie=response.headers['set-cookie']
        assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie


def test_untrusted_origin_cannot_read_cookie_authenticated_queue(queue_client):
    client,*_=queue_client
    authenticate(client)
    blocked=client.options('/admin/queue/sessions',headers={'Origin':'https://attacker.invalid','Access-Control-Request-Method':'GET'})
    assert blocked.status_code==400
    assert 'access-control-allow-origin' not in blocked.headers
    allowed=client.options('/admin/queue/sessions',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'GET'})
    assert allowed.status_code==200
    assert allowed.headers['access-control-allow-origin']=='http://localhost:5173'

@pytest.mark.parametrize('mark,eligible',[('v',1),('v-3',1),('x',0),('?',0),('vv',0)])
def test_real_grade_hook_reads_admission_without_writing_marks(tmp_path,monkeypatch,mark,eligible):
    from grading.defense_queue.storage import QueueStore
    monkeypatch.setenv('QUEUE_WORKER_ENABLED','0')
    store=QueueStore(tmp_path/'hook.db')
    monkeypatch.setattr('grading.defense_queue.runtime.queue_store',lambda:store)
    course={'name':'Предмет','timezone':'Europe/Moscow','google':{'student-name-column':1},'queue':{'enabled':True},'labs':{'1':{'short-name':'ЛР1'}}}
    class Sheet:
        def get_all_values(self): return [['','','GitHub','07.11.2026'],['№','ФИО','','ЛР1'],['1','Студент','login',mark]]
        def update_cell(self,*args): raise AssertionError('Queue must not write grades')
    main._after_grade_queue_sync('c','g',course,{'sheet':Sheet()})
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM queue_candidates WHERE status='eligible'").fetchone()[0]==eligible
        if eligible:
            assert con.execute('SELECT deadline_available FROM queue_candidates').fetchone()[0]==1


def test_http_move_and_cancel_preserve_candidate_and_participation(queue_client):
    client,store,_,now=queue_client
    authenticate(client)
    with store.connection() as con: cid=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.attendance('future',cid,True,now=now)
    path='/admin/courses/c/groups/g/queue/sessions/future'
    response=client.patch(path,json={'starts_at':datetime.fromtimestamp(now+5400,timezone.utc).isoformat(),'ends_at':datetime.fromtimestamp(now+9000,timezone.utc).isoformat()})
    assert response.status_code==200,response.text
    with store.connection() as con:
        session=con.execute("SELECT * FROM sessions WHERE session_id='future'").fetchone()
        assert session['ends_at']-session['starts_at']==5400
    assert client.patch(path,json={'starts_at':datetime.fromtimestamp(now-10,timezone.utc).isoformat()}).status_code==422
    view=client.get('/courses/c/groups/g/queue').json()
    assert view['session']['session_id']=='future' and view['entries'][0]['will_attend']
    assert client.patch(path,json={'cancel':True}).status_code==200
    assert client.get('/courses/c/groups/g/queue').json()['session'] is None
    with store.connection() as con: assert con.execute("SELECT count(*) FROM queue_candidates WHERE status='eligible'").fetchone()[0]==1


def test_expired_teacher_cookie_rejected(queue_client):
    from unittest.mock import patch
    client,_,_,now=queue_client
    with patch('time.time',return_value=now-4000):
        cookie=main.signer.sign(main.ADMIN_LOGIN.encode()).decode()
    client.cookies.set('admin_session',cookie)
    assert client.get('/admin/queue/sessions').status_code==401


@pytest.mark.parametrize('secret',[None,'super-secret-key'])
def test_startup_rejects_missing_or_public_secret(tmp_path,secret):
    import os,subprocess,sys
    env=dict(os.environ,LOG_DIR=str(tmp_path/'logs'),ADMIN_LOGIN='test',ADMIN_PASSWORD='test',GITHUB_TOKEN='dummy',QUEUE_WORKER_ENABLED='0')
    env.pop('SECRET_KEY',None)
    if secret is not None: env['SECRET_KEY']=secret
    run=subprocess.run([sys.executable,'-c','import dotenv; dotenv.load_dotenv=lambda *a, **k: None; import main'],env=env,capture_output=True,text=True,timeout=20)
    assert run.returncode!=0 and 'собственный SECRET_KEY' in run.stderr


def test_teacher_schedule_shows_configured_university(queue_client):
    client,store,_,now=queue_client
    from grading.defense_queue.operations import save_schedule
    save_schedule(store,'c','g',{'source':'manual','university':'Тестовый вуз'},now=now)
    authenticate(client)
    assert client.get('/admin/queue/sessions').json()[0]['university']=='Тестовый вуз'


def test_public_csv_queue_and_navigation_need_no_service_account(queue_client, monkeypatch):
    client,store,course,now=queue_client
    import grading.defense_queue.public_sheet as public
    course['google']['spreadsheet']='test-sheet-123'
    course['queue']['sheet_source']={'type':'public_csv','worksheets':{'g':'123'}}
    grid=[['','','GitHub','2026-11-01','2026-11-08'],['№','ФИО','','ЛР1','ЛР2'],['1','Фамилия Имя Отчество','login','v','v']]
    monkeypatch.setattr(public,'read_public_grid',lambda *args:grid)
    def no_credentials(): raise AssertionError('Public reading must not request Google credentials')
    monkeypatch.setattr(main,'sheets_client',no_credentials)
    assert client.get('/courses/c/groups').json()==['g']
    assert client.get('/courses/c/groups/g/labs').json()==['ЛР1','ЛР2']
    # Исходный обработчик интеграции, извлечённый до fixture-подмены.
    monkeypatch.setattr(main,'_sync_defense_queue',ORIGINAL_QUEUE_SYNC)
    response=client.get('/courses/c/groups/g/queue')
    assert response.status_code==200,response.text
    assert response.json()['entries'][0]['github']=='login'


def test_read_only_course_rejects_registration_and_grading(queue_client, monkeypatch):
    client,_,course,_=queue_client
    course['queue']['registration_disabled']=True
    def no_credentials(): raise AssertionError('Read-only course must not use credentials')
    monkeypatch.setattr(main,'sheets_client',no_credentials)
    identity={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'login'}
    assert client.post('/courses/c/groups/g/register',json=identity).status_code==409
    assert client.post('/courses/c/groups/g/labs/1/grade',json={'github':'login'}).status_code==409


def test_settings_include_sheet_dates_even_with_manual_override(queue_client):
    client,_,_,_=queue_client
    authenticate(client)
    assert client.put('/admin/courses/c/queue/settings',json={'random':False,'deadlines':{'1':None}}).status_code==200
    response=client.get('/admin/courses/c/queue/settings?group_id=g')
    assert response.status_code==200,response.text
    data=response.json()
    assert data['sheet_deadlines']['1']['value'] is not None
    assert data['sheet_deadlines']['1']['raw']=='2026-11-01'
    assert data['sheet_deadlines']['1']['source']=='sheet'
    assert data['timezone']=='UTC'


def test_cancel_and_restore_session_preserve_student_confirmation(queue_client):
    client,store,_,_=queue_client
    authenticate(client)
    student={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'login','session_id':'future','will_attend':True}
    assert client.post('/courses/c/groups/g/queue/attendance',json=student).status_code==200
    path='/admin/courses/c/groups/g/queue/sessions/future'
    assert client.patch(path,json={'cancel':True}).status_code==200
    cancelled=next(row for row in client.get('/admin/queue/sessions').json() if row['session_id']=='future')
    assert cancelled['can_restore'] and not cancelled['can_move']
    assert client.patch(path,json={'restore':True}).status_code==200
    view=client.get('/courses/c/groups/g/queue').json()
    assert view['session']['session_id']=='future' and view['entries'][0]['will_attend']
    assert client.patch(path,json={'restore':True}).status_code==200
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM queue_events WHERE kind='restored'").fetchone()[0]==1


def test_schedule_patch_rejects_conflicting_actions(queue_client):
    client,*_=queue_client
    authenticate(client)
    path='/admin/courses/c/groups/g/queue/sessions/future'
    assert client.patch(path,json={'cancel':True,'restore':True}).status_code==422
    assert client.patch(path,json={'restore':True,'starts_at':'2026-11-09T18:40:00+03:00'}).status_code==422


def test_started_defense_cannot_be_moved_even_after_cancel(queue_client):
    client,store,_,now=queue_client
    authenticate(client)
    store.create_session('current','c','g','teacher',starts_at=now-30,ends_at=now+600,local_date='2026-11-07',now=now-100)
    with store.connection() as con: cid=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.attendance('current',cid,True,now=now-40)
    assert client.post('/admin/courses/c/groups/g/queue/actions',json={'session_id':'current','action':'call_next'}).status_code==200
    row=next(row for row in client.get('/admin/queue/sessions').json() if row['session_id']=='current')
    assert row['service_started'] and not row['can_move']
    assert client.patch('/admin/courses/c/groups/g/queue/sessions/current',json={'cancel':True}).status_code==200
    row=next(row for row in client.get('/admin/queue/sessions').json() if row['session_id']=='current')
    assert not row['can_move'] and row['can_restore']


def test_correct_result_http_keeps_single_row_and_retracts_followup(queue_client):
    client,store,_,now=queue_client
    authenticate(client)
    store.create_session('today','c','g','teacher',starts_at=now-30,ends_at=now+5400,local_date='2026-11-07',now=now-100)
    with store.connection() as con:
        cid=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.attendance('today',cid,True,now=now-40)
    path='/admin/courses/c/groups/g/queue'
    assert client.get(path+'?session_id=today').status_code==200
    def action(**payload):
        response=client.post(path+'/actions',json={'session_id':'today',**payload})
        assert response.status_code==200,response.text
        return response.json()
    called=action(action='call_next')
    eid=called['current_entry_id']
    passed=action(action='result',entry_id=eid,outcome='defended')
    assert len(passed['entries'])==1
    assert passed['entries'][0]['lab_key']=='2'
    assert passed['entries'][0]['attempt']['is_repeat']
    corrected=action(action='correct_result',entry_id=eid,outcome='failed')
    assert len(corrected['entries'])==1
    assert corrected['entries'][0]['lab_key']=='1'
    assert corrected['entries'][0]['attempt']['status']=='waiting'
    assert corrected['stats']['defended']==0
    assert corrected['stats']['failed']==1
    assert any(e['kind']=='result_corrected' and e['outcome']=='failed' and e['previous_outcome']=='defended' for e in corrected['history'])


def test_next_attendance_blocked_until_end_plus_ten_minutes(queue_client,monkeypatch):
    client,store,_,now=queue_client
    with store.connection() as con:
        candidate=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.create_session('today','c','g','teacher',starts_at=now-10,ends_at=now+100,local_date='2026-11-08',now=now-100)
    store.attendance('today',candidate,True,now=now-20)
    public='/courses/c/groups/g/queue'
    identity={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'login','session_id':'future','will_attend':True}
    for instant in (now,now+100,now+699):
        monkeypatch.setattr(time,'time',lambda instant=instant:instant)
        view=client.get(public).json()
        assert view['read_only'] and not view['attendance_open']
        assert view['session']['session_id']=='today'
        assert 'student_catalog' not in view
        assert client.post(public+'/attendance',json=identity).status_code==409
    monkeypatch.setattr(time,'time',lambda:now+700)
    view=client.get(public).json()
    assert not view['read_only'] and view['attendance_open']
    assert view['session']['session_id']=='future'
    assert client.post(public+'/attendance',json=identity).status_code==200


def test_review_fifo_settings_window_and_running_signup(queue_client):
    client, store, course, now = queue_client
    authenticate(client)
    settings = '/admin/courses/c/queue/settings'
    response = client.put(settings, json={'fifo': True, 'random': False, 'opens_before_minutes': 15})
    assert response.status_code == 200, response.text
    assert client.get(settings + '?group_id=g').json()['mode'] == 'fifo'
    body = {'surname': 'Фамилия', 'name': 'Имя', 'patronymic': 'Отчество', 'github': 'login', 'session_id': 'future', 'will_attend': True}
    path = '/courses/c/groups/g/queue'
    assert not client.get(path).json()['attendance_open']
    assert client.post(path + '/attendance', json=body).status_code == 409
    store.create_session('running-fifo', 'c', 'g', 'teacher', starts_at=now-600, ends_at=now+600, local_date='2026-11-14', now=now-700)
    body['session_id'] = 'running-fifo'
    assert client.post(path + '/attendance', json=body).status_code == 200
    public = client.get(path).json()
    assert public['attendance_open']
    assert not {'history', 'student_catalog', 'available_candidates', 'attempts'} & public.keys()
    assert client.put(settings, json={'fifo': True, 'random': True}).status_code == 422
    assert client.put(settings, json={'fifo': True, 'opens_before_minutes': -1}).status_code == 422
