"""Повторяемая сквозная проверка HTTP + SQLite без внешних запросов."""
import time
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
import main
from tests.test_queue_endpoints import queue_client, authenticate
from grading.defense_queue.worker import reconcile
from grading.defense_queue.storage import QueueStore


@pytest.mark.parametrize('run',range(3))
def test_entire_defense_journey(queue_client,monkeypatch,tmp_path,run):
    client,store,course,now=queue_client
    public='/courses/c/groups/g/queue'
    admin='/admin/courses/c/groups/g/queue'
    student={'surname':'Фамилия','name':'Имя','patronymic':'Отчество','github':'login','session_id':'future'}
    authenticate(client)
    assert client.put('/admin/courses/c/queue/settings',json={'random':bool(run%2),'deadlines':{}}).status_code==200
    for attendance in [True,False,True]:
        assert client.post(public+'/attendance',json=dict(student,will_attend=attendance)).status_code==200
        assert client.get(public).json()['entries'][0]['will_attend']==attendance
    store.create_session('next','c','g','teacher',starts_at=now+10000,ends_at=now+12000,local_date='2026-11-08',now=now)
    monkeypatch.setattr(time,'time',lambda:now+3601)
    authenticate(client)
    assert client.post(public+'/attendance',json=dict(student,will_attend=False)).status_code==409
    assert client.get(public).json()['session']['session_id']=='future'
    assert client.get(public).json()['read_only'] is True
    called=client.post(admin+'/actions',json={'session_id':'future','action':'call_next'})
    assert called.status_code==200,called.text
    eid=called.json()['current_entry_id']
    assert client.post(admin+'/actions',json={'session_id':'future','action':'call_next'}).json()['current_entry_id']==eid
    defended=client.post(admin+'/actions',json={'session_id':'future','action':'result','entry_id':eid,'outcome':'defended'})
    assert defended.status_code==200,defended.text
    waiting=[a for a in defended.json()['attempts'] if a['status']=='waiting']
    assert len(waiting)==1 and waiting[0]['lab_key']=='2' and waiting[0]['is_repeat']==1
    for outcome in ('failed','no_show'):
        view=client.post(admin+'/actions',json={'session_id':'future','action':'call_next'}).json()
        result=client.post(admin+'/actions',json={'session_id':'future','action':'result','entry_id':view['current_entry_id'],'outcome':outcome})
        assert result.status_code==200,result.text
    assert result.json()['stats']['defended']==1
    assert result.json()['stats']['failed']==1
    assert result.json()['stats']['no_show']==1
    assert result.json()['stats']['waiting']==0
    monkeypatch.setattr(time,'time',lambda:now+7800)
    authenticate(client)
    assert reconcile(store,'future',now=now+7800,sync=lambda:main._sync_defense_queue('c','g',course,force=True))
    for _ in range(3):
        assert client.get(admin+'?session_id=future').json()['stats']['waiting']==0
    upcoming=client.get(public).json()
    assert upcoming['entries'][0]['lab_key']=='2'
    assert not upcoming['entries'][0]['will_attend']
    with store.connection() as con:
        assert con.execute('SELECT checked_at FROM queue_reconciliations WHERE session_id=?',('future',)).fetchone()[0] is not None
        midnight = con.execute('SELECT due_at FROM queue_reconciliations WHERE session_id=?',('future',)).fetchone()[0]
    assert reconcile(store,'future',now=midnight,sync=lambda:main._sync_defense_queue('c','g',course,force=True))
    # Реальная SQLite backup/restore, без копирования одного файла при активном WAL.
    path=tmp_path/'restored.db'
    with store.connection() as con, sqlite3.connect(path) as target: con.backup(target)
    restored=QueueStore(path)
    with restored.connection() as con:
        assert con.execute('SELECT count(*) FROM queue_entries').fetchone()[0]==3
        assert con.execute('SELECT done_at FROM queue_reconciliations WHERE session_id=?',('future',)).fetchone()[0] is not None


def test_concurrent_teacher_calls_are_idempotent(queue_client):
    _,store,course,now=queue_client
    from grading.defense_queue.operations import teacher_view,call_next
    with store.connection() as con:
        cid=con.execute("SELECT candidate_id FROM queue_candidates WHERE status='eligible'").fetchone()[0]
    store.attendance('future',cid,True,now=now)
    teacher_view(store,'c','g',course,deadlines_available=True,now=now+3601,session_id='future')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:call_next(store,'future',now=now+3601,actor='admin',request_id='same-command'),range(12)))
    assert len(set(results))==1
    with store.connection() as con:
        assert con.execute("SELECT count(*) FROM queue_events WHERE kind='called'").fetchone()[0]==1
