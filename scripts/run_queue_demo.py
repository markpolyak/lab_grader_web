#!/usr/bin/env python3
"""Локальная демонстрация настоящей очереди с искусственной таблицей, без Docker и ключей."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
COURSE_ID, GROUP_ID = 'queue-demo', 'DEMO-01'


def demo_data(now):
    today = datetime.fromtimestamp(now, timezone.utc).date()
    course = {
        'name': 'Демонстрация очереди', 'university': 'Демо-вуз', 'semester': 'Локальная демонстрация',
        'timezone': 'Europe/Moscow', 'github': {'organization': 'demo-only'},
        'google': {'spreadsheet': 'demo-only', 'info-sheet': 'Информация', 'student-name-column': 1},
        'queue': {'enabled': True, 'timezone': 'Europe/Moscow', 'deadlines': {'3': None}},
        'labs': {str(i): {'short-name': f'ЛР{i}', 'github-prefix': f'demo-lab{i}'} for i in range(1,4)},
    }
    grid = [['','','GitHub', str(today-timedelta(days=14)),str(today+timedelta(days=2)), ''],
            ['№','ФИО','','ЛР1','ЛР2','ЛР3'],
            ['1','Иванов Иван Иванович','demo-ivan','v','v','v'],
            ['2','Петрова Анна Сергеевна','demo-anna','vv','v','v'],
            ['3','Сидоров Алексей Петрович','demo-alex','vv','vv','v'],
            ['4','Смирнова Мария Андреевна','demo-maria','v-2','v',''],
            ['5','Кузнецов Дмитрий Олегович','demo-dmitry','v','v',''],
            ['6','Орлова Елена Игоревна','demo-elena','vv','v@5',''],
            ['7','Васильев Максим Романович','demo-max','x','v','']]
    return course, grid


def seed_queue(store, course, grid, *, now, sync_group, teacher_view):
    summary = sync_group(store, COURSE_ID, GROUP_ID, course, grid,
                         parse_deadline=lambda v: datetime.fromisoformat(v).replace(tzinfo=timezone.utc), now=now-600)
    for key, start, end in [('demo-current',now-300,now+3600),('demo-next',now+86400,now+91800)]:
        store.create_session(key, COURSE_ID, GROUP_ID, 'demo-teacher', starts_at=start, ends_at=end,
                             local_date=datetime.fromtimestamp(start,timezone.utc).date().isoformat(), now=now-600)
    with store.connection() as con:
        rows=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible' ORDER BY candidate_id").fetchall()
    for row in rows[:4]:
        store.attendance('demo-current',row[0],True,now=now-400)
    for row in rows[1:3]:
        store.attendance('demo-next',row[0],True,now=now)
    teacher_view(store,COURSE_ID,GROUP_ID,course,deadlines_available=summary['deadlines_available'],now=now,session_id='demo-current')


def create_app():
    os.chdir(ROOT)
    sys.path.insert(0,str(ROOT))
    data_root=ROOT/'data'/'queue-demo'
    data_root.mkdir(parents=True,exist_ok=True)
    run=Path(tempfile.mkdtemp(prefix='run-',dir=data_root))
    configs=run/'courses'; configs.mkdir()
    now=int(time.time())
    course,grid=demo_data(now)
    (configs/'demo.yaml').write_text(json.dumps({'course':course},ensure_ascii=False),encoding='utf-8')
    (configs/'index.yaml').write_text(json.dumps({'version':'1.0','courses':[{'id':COURSE_ID,'file':'demo.yaml','status':'active','priority':100}]}),encoding='utf-8')
    os.environ.update(ADMIN_LOGIN='demo',ADMIN_PASSWORD='demo123',GITHUB_TOKEN='offline-demo',
                      SECRET_KEY=secrets.token_hex(32),QUEUE_WORKER_ENABLED='0',QUEUE_DB_PATH=str(run/'queue.sqlite3'),LOG_DIR=str(run/'logs'))
    import main
    from fastapi.responses import JSONResponse
    from grading.defense_queue.runtime import queue_store
    from grading.defense_queue.sync import sync_group
    from grading.defense_queue.operations import teacher_view
    main.COURSES_DIR=str(configs)
    main.INDEX_FILE=str(configs/'index.yaml')

    class Sheet:
        title=GROUP_ID
        def get_all_values(self): return [list(row) for row in grid]
        def row_values(self,index): return list(grid[index-1])
    class Spreadsheet:
        def worksheets(self): return [Sheet()]
        def worksheet(self,name):
            if name!=GROUP_ID: raise ValueError('Демо-группа не найдена')
            return Sheet()
    class Sheets:
        def open_by_key(self,key):
            if key!='demo-only': raise ValueError('В демонстрации доступна только искусственная таблица')
            return Spreadsheet()
    main.sheets_client=lambda: Sheets()
    def no_network(url): raise ValueError('В демо нет сетевого импорта. Используйте ручное занятие или JSON')
    main._fetch_queue_schedule=no_network

    @main.app.middleware('http')
    async def demo_boundary(request,call_next):
        path=request.url.path
        allowed = path in ('/courses','/admin/login','/admin/check-auth','/admin/queue/sessions','/docs','/openapi.json','/redoc')
        allowed = allowed or path.startswith(f'/courses/{COURSE_ID}') or path.startswith(f'/admin/courses/{COURSE_ID}/') and '/queue' in path
        mutation = request.method not in ('GET','HEAD','OPTIONS')
        if request.method!='OPTIONS' and (not allowed or mutation and '/queue/' not in path and not path.endswith('/queue/settings') and path!='/admin/login'):
            return JSONResponse(status_code=409,content={'detail':'Это демо очереди. Допуски уже подготовлены: откройте «Список на занятие». Проверка GitHub и изменение настоящих курсов отключены.'})
        return await call_next(request)

    seed_queue(queue_store(),course,grid,now=now,sync_group=sync_group,teacher_view=teacher_view)
    print('\nДЕМО: искусственные студенты и таблица; фоновые сетевые задачи отключены.',flush=True)
    print('Преподаватель: http://localhost:5173/admin — логин demo, пароль demo123',flush=True)
    print('Затем: http://localhost:5173/admin/courses/queue-demo/queue',flush=True)
    print('Студент: http://localhost:5173/course/queue-demo/group/DEMO-01/lab/1',flush=True)
    print('ФИО: Иванов / Иван / Иванович; GitHub: demo-ivan',flush=True)
    print(f'Изолированная база: {run / "queue.sqlite3"}\nНовый запуск создаёт новый набор данных. Ctrl+C — остановка.\n',flush=True)
    return main.app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    try:
        import uvicorn
    except ImportError:
        raise SystemExit('Установи зависимости: uv pip install --python .venv/bin/python -r requirements-dev.txt')
    uvicorn.run(create_app(),host='127.0.0.1',port=args.port)
