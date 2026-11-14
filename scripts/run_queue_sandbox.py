#!/usr/bin/env python3
"""Изолированная песочница полного UI: настоящие HTTP/SQLite, симуляция Google/GitHub."""
import argparse
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
COURSE='queue-sandbox'
GROUP='DEMO-01'


def initial_state():
    today=datetime.now(timezone.utc).date()
    header=['','','GitHub',str(today-timedelta(days=14)),str(today+timedelta(days=7)),str(today+timedelta(days=21))]
    return {'ci':{},'grids':{
        GROUP:[header,['№','ФИО','','ЛР1','ЛР2','ЛР3'],
            ['1','Иванов Иван Иванович','demo-ivan','v','v','v'],
            ['2','Петрова Анна Сергеевна','demo-anna','v','v','v'],
            ['3','Сидоров Алексей Петрович','demo-alex','v','v',''],
            ['4','Орлова Елена Игоревна','demo-elena','v','v',''],
            ['5','Кузнецов Дмитрий Олегович','demo-dmitry','v','v',''],
            ['6','Новиков Никита Никитич','','','','']],
        'DEMO-02':[header,['№','ФИО','','ЛР1','ЛР2','ЛР3'],['1','Тестов Тест Тестович','demo-test','v','','']]}}


def create_app(directory=None,frontend_port=5180,reset=False):
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    directory=Path(directory or ROOT/'data/queue-sandbox').resolve()
    if not directory.is_relative_to((ROOT/'data').resolve()):
        raise ValueError('Каталог песочницы должен находиться внутри data/')
    directory.mkdir(parents=True,exist_ok=True)
    config_dir=directory/'courses';config_dir.mkdir(exist_ok=True)
    state_path=directory/'state.json'
    lock=threading.RLock()
    state=json.loads(state_path.read_text()) if state_path.exists() and not reset else initial_state()
    if reset:state['database']=f'queue-{secrets.token_hex(6)}.sqlite3'
    def save():
        with lock:
            temp=state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
            temp.replace(state_path)
    save()
    database=state.get('database') or 'queue-initial.sqlite3'
    if Path(database).name != database: raise ValueError('Некорректный путь базы песочницы')
    os.environ.update(ADMIN_LOGIN='sandbox',ADMIN_PASSWORD='sandbox123',SECRET_KEY=secrets.token_hex(32),
        GITHUB_TOKEN='sandbox-no-network',QUEUE_WORKER_ENABLED='0',QUEUE_DB_PATH=str(directory/database),
        LOG_DIR=str(directory/'logs'),FRONTEND_URL=f'http://localhost:{frontend_port}',PUBLIC_BASE_URL=f'http://localhost:{frontend_port}')
    course={'name':'Песочница очереди — симуляция Google/GitHub','semester':'Тестовый семестр','university':'Тестовый вуз',
        'timezone':'UTC+3','github':{'organization':'sandbox-only'},
        'google':{'spreadsheet':'sandbox-only','info-sheet':'Информация','student-name-column':1,'lab-column-offset':2},
        'queue':{'enabled':True,'timezone':'Europe/Moscow','required_labs':['1','2','3'],'semester_week_anchor':'2026-08-31'},
        'labs':{str(i):{'short-name':f'ЛР{i}','github-prefix':f'sandbox-lab{i}','ignore-task-id':True,
            'penalty-max':5,'penalty-strategy':'weekly','files':['solution.py','report.md'],'ci':{'jobs':['tests','report']}} for i in range(1,4)}}
    (config_dir/'sandbox.yaml').write_text(json.dumps({'course':course},ensure_ascii=False))
    (config_dir/'index.yaml').write_text(json.dumps({'version':'1.0','courses':[{'id':COURSE,'file':'sandbox.yaml','status':'active','priority':1000}]}))
    (config_dir/'logos').mkdir(exist_ok=True)
    os.environ['COURSES_DIR']=str(config_dir)
    import main
    from fastapi import Depends,HTTPException
    from fastapi.responses import HTMLResponse,JSONResponse
    from pydantic import BaseModel,Field
    from grading.defense_queue.runtime import queue_store
    from grading.defense_queue.sync import sync_group,parse_queue_deadline
    from grading.defense_queue.operations import teacher_view
    from grading.sheets_client import parse_deadline
    from grading.github_client import CommitInfo
    main.COURSES_DIR=str(config_dir);main.INDEX_FILE=str(config_dir/'index.yaml')
    class Sheet:
        def __init__(self,title):self.title=title
        def get_all_values(self):
            with lock:return [list(row) for row in state['grids'][self.title]]
        def row_values(self,n):return self.get_all_values()[n-1]
        def col_values(self,n):return [row[n-1] if len(row)>=n else '' for row in self.get_all_values()]
        def cell(self,row,col):return SimpleNamespace(value=self.row_values(row)[col-1])
        def find(self,text):
            for r,row in enumerate(self.get_all_values(),1):
                for c,value in enumerate(row,1):
                    if value==text:return SimpleNamespace(row=r,col=c,value=value)
            return None
        def update_cell(self,row,col,value):
            with lock:
                state['grids'][self.title][row-1][col-1]=value;save()
    class Spreadsheet:
        def worksheets(self):return [Sheet(key) for key in state['grids']]
        def worksheet(self,key):
            if key not in state['grids']:raise ValueError('В песочнице нет такой группы')
            return Sheet(key)
        def fetch_sheet_metadata(self):return {'properties':{'locale':'en_US'}}
    class Sheets:
        def open_by_key(self,key):
            if key!='sandbox-only':raise ValueError('Песочница не открывает внешние таблицы')
            return Spreadsheet()
    main.sheets_client=lambda:Sheets()
    class GitHub:
        def __init__(self,token):pass
        def check_required_files(self,org,repo,files):return []
        def has_workflows_directory(self,org,repo):return True
        def get_latest_commit(self,org,repo):return CommitInfo('sandbox-commit',[])
        def get_check_runs(self,org,repo,sha):
            if org!='sandbox-only':raise ValueError('Внешний GitHub отключён')
            prefix,login=next(((f'sandbox-lab{i}-',repo[len(f'sandbox-lab{i}-'):]) for i in range(1,4) if repo.startswith(f'sandbox-lab{i}-')),(None,None))
            if prefix is None:raise ValueError('Неизвестный тестовый репозиторий')
            lab=prefix[len('sandbox-lab'):].rstrip('-')
            verdict=state['ci'].get(f'{login}:{lab}','success')
            return [{'name':name,'conclusion':None if verdict=='pending' else ('failure' if verdict=='failure' and name=='tests' else 'success'),
                'html_url':'#sandbox-simulated-ci','completed_at':datetime.now(timezone.utc).isoformat()} for name in ('tests','report')]
    main.GitHubClient=GitHub
    import requests as requests_library
    class Requests:
        RequestException = requests_library.RequestException
        def get(self,url,**kwargs):
            if not url.startswith('https://api.github.com/users/'):raise ValueError('Внешние запросы запрещены в песочнице')
            login=url.rsplit('/',1)[-1]
            valid=login in {'demo-ivan','demo-anna','demo-alex','demo-elena','demo-dmitry','demo-test','demo-new'}
            return SimpleNamespace(status_code=200 if valid else 404)
    main.requests=Requests()
    def no_network(*args,**kwargs):raise ValueError('В песочнице нет внешней сети. Используйте ручное расписание или JSON-импорт.')
    main._fetch_queue_schedule=no_network
    def sync(group):
        return sync_group(queue_store(),COURSE,group,course,Sheet(group).get_all_values(),
            parse_deadline=lambda value:parse_queue_deadline(value,course,parse_deadline),now=int(time.time()))
    def seed():
        store=queue_store();now=int(time.time())
        for group in state['grids']:
            sync(group)
            with store.connection() as con:
                exists=con.execute('SELECT 1 FROM sessions WHERE course_id=? AND group_id=?',(COURSE,group)).fetchone()
            if exists:continue
            for key,start in [('nearest',now+300),('later',now+86400)]:
                session=f'{group}-{key}'
                store.create_session(session,COURSE,group,'sandbox-teacher',starts_at=start,ends_at=start+5400,
                    local_date=datetime.fromtimestamp(start,timezone.utc).date().isoformat(),now=now-600)
                with store.connection() as con:
                    candidates=con.execute("SELECT candidate_id FROM queue_candidates WHERE group_id=? AND status='eligible' ORDER BY candidate_id",(group,)).fetchall()
                for candidate in candidates[:3]:store.attendance(session,candidate[0],True,now=now-60)
    seed()
    @main.app.middleware('http')
    async def boundary(request,call_next):
        path=request.url.path
        allowed=path in ('/courses','/admin/login','/admin/check-auth','/admin/queue/sessions','/sandbox','/sandbox/state','/sandbox/ci','/sandbox/start','/sandbox/finish','/sandbox/reopen','/sandbox/mark','/sandbox/reset')
        allowed=allowed or path.startswith(f'/courses/{COURSE}') or path.startswith(f'/admin/courses/{COURSE}/queue') or (path.startswith(f'/admin/courses/{COURSE}/groups/') and '/queue' in path)
        if request.method!='OPTIONS' and not allowed:
            return JSONResponse(status_code=409,content={'detail':'Песочница изолирована от внешних курсов и сервисов'})
        response=await call_next(request)
        response.headers['X-Queue-Sandbox']='simulated-google-github'
        return response
    @main.app.get('/sandbox',response_class=HTMLResponse)
    def controls():
        return (ROOT/'scripts/queue_sandbox.html').read_text().replace('__FRONTEND_PORT__',str(frontend_port))
    @main.app.get('/sandbox/state')
    def get_state(admin=Depends(main.require_admin)):
        with lock:return dict(state,groups=list(state['grids']))
    class LabState(BaseModel):
        group:str=GROUP
        login:str='demo-new'
        lab:int=Field(default=1,ge=1,le=3)
        value:str
    def target(data):
        if data.group not in state['grids']:raise HTTPException(422,'Неизвестная тестовая группа')
        return next((row for row in state['grids'][data.group][2:] if row[2]==data.login or data.login=='demo-new' and row[1]=='Новиков Никита Никитич'),None)
    @main.app.post('/sandbox/ci')
    def change_ci(data:LabState,admin=Depends(main.require_admin)):
        if data.value not in ('success','failure','pending') or target(data) is None:raise HTTPException(422,'Неизвестный сценарий CI')
        with lock:state['ci'][f'{data.login}:{data.lab}']=data.value;save()
        return {'status':'updated'}
    @main.app.post('/sandbox/mark')
    def mark(data:LabState,admin=Depends(main.require_admin)):
        if data.value not in ('','v','vv','x'):raise HTTPException(422,'Неизвестная отметка')
        row=target(data)
        if row is None:raise HTTPException(422,'Неизвестный тестовый студент')
        with lock:row[2+data.lab]=data.value;save()
        summary=sync(data.group)
        return {'status':'updated','eligible':summary['eligible']}
    @main.app.post('/sandbox/start')
    def start(group:str=GROUP,admin=Depends(main.require_admin)):
        store=queue_store();now=int(time.time())
        with store.transaction() as con:
            if con.execute("SELECT 1 FROM sessions WHERE group_id=? AND starts_at<=? AND ends_at>? AND enrollment_status!='cancelled'",(group,now,now)).fetchone():
                raise HTTPException(409,'Текущее занятие ещё идёт. Завершите его или сбросьте песочницу.')
            old=con.execute("SELECT * FROM sessions WHERE group_id=? AND starts_at>? AND enrollment_status='scheduled' ORDER BY starts_at LIMIT 1",(group,now)).fetchone()
            if old is None:raise HTTPException(409,'Будущее занятие не найдено')
            con.execute('UPDATE sessions SET starts_at=?,opens_at=?,ends_at=?,expires_at=? WHERE session_id=?',(now,now,now+5400,now+5400,old['session_id']))
        summary=sync(group)
        teacher_view(store,COURSE,group,course,deadlines_available=summary['deadlines_available'],now=now,session_id=old['session_id'])
        return {'status':'started','session_id':old['session_id']}
    @main.app.post('/sandbox/finish')
    def finish(group:str=GROUP,admin=Depends(main.require_admin)):
        from grading.defense_queue.worker import reconcile
        store=queue_store();now=int(time.time())
        with store.transaction() as con:
            session=con.execute('SELECT * FROM sessions WHERE group_id=? AND starts_at<=? AND ends_at>? ORDER BY starts_at DESC LIMIT 1',(group,now,now)).fetchone()
            if session is None:raise HTTPException(409,'Текущее занятие не найдено')
            con.execute('UPDATE sessions SET starts_at=min(starts_at,?),opens_at=min(opens_at,?),ends_at=?,expires_at=? WHERE session_id=?',(now-2,now-2,now-1,now-1,session['session_id']))
            con.execute('UPDATE queue_reconciliations SET due_at=? WHERE session_id=?',(now,session['session_id']))
        reconcile(store,session['session_id'],now=now,sync=lambda:sync(group))
        return {'status':'finished'}
    @main.app.post('/sandbox/reopen')
    def reopen(group:str=GROUP,admin=Depends(main.require_admin)):
        store=queue_store();now=int(time.time())
        with store.transaction() as con:
            session=con.execute("SELECT * FROM sessions WHERE group_id=? AND starts_at<=? AND ends_at<=? AND enrollment_status!='cancelled' ORDER BY ends_at DESC LIMIT 1",(group,now,now)).fetchone()
            if session is None:raise HTTPException(409,'Сначала завершите тестовое занятие')
            con.execute('UPDATE sessions SET starts_at=min(starts_at,?),opens_at=min(opens_at,?),ends_at=?,expires_at=? WHERE session_id=?',(now-601,now-601,now-600,now-600,session['session_id']))
        return {'status':'next_enrollment_open'}
    @main.app.post('/sandbox/reset')
    def reset(admin=Depends(main.require_admin)):
        nonlocal state
        with lock:
            state=initial_state();state['database']=f'queue-{secrets.token_hex(6)}.sqlite3';save()
            os.environ['QUEUE_DB_PATH']=str(directory/state['database'])
            from grading.defense_queue import runtime
            runtime._CACHE.clear()
            seed()
        return {'status':'reset'}
    return main.app


def main_cli():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8010)
    parser.add_argument('--frontend-port',type=int,default=5180)
    parser.add_argument('--backend-only',action='store_true')
    parser.add_argument('--reset',action='store_true',help='Начать с чистой песочницы, сохранив старые базы')
    parser.add_argument('--check',action='store_true',help='Проверить API в процессе, без серверов и внешней сети')
    args=parser.parse_args()
    if args.check:
        from fastapi.testclient import TestClient
        (ROOT/'data').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='sandbox-check-',dir=ROOT/'data') as temporary:
            directory=Path(temporary)
            app=create_app(directory,args.frontend_port)
            with TestClient(app) as client:
                assert client.post('/admin/login',json={'login':'sandbox','password':'sandbox123'}).status_code==200
                assert client.post('/sandbox/reset').status_code==200
                assert client.get('/courses/'+COURSE+'/groups/'+GROUP+'/queue').status_code==200
                assert client.post('/sandbox/start').status_code==200
                result=client.get('/admin/courses/'+COURSE+'/groups/'+GROUP+'/queue?session_id='+GROUP+'-nearest')
                assert result.status_code==200,result.text
                assert len(result.json()['round_entries'])==3
                print('Sandbox API OK: isolated sources, real HTTP handlers and SQLite, seeded roster.')
        return
    from queue_dev_server import frontend_server,require_free_backend
    try:require_free_backend(args.port)
    except ValueError as error:raise SystemExit(str(error))
    app=create_app(frontend_port=args.frontend_port,reset=args.reset)
    with frontend_server(ROOT,backend_port=args.port,frontend_port=args.frontend_port,enabled=not args.backend_only):
        print(f'\nПЕСОЧНИЦА: Google/GitHub симулируются. Панель управления: http://localhost:{args.port}/sandbox',flush=True)
        print(f'Студент: http://localhost:{args.frontend_port}/course/{COURSE} — выбор группы, затем лабораторной',flush=True)
        print(f'Вход преподавателя: http://localhost:{args.frontend_port}/admin — sandbox / sandbox123',flush=True)
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=args.port)


if __name__=='__main__':main_cli()
