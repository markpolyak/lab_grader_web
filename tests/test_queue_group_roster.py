"""The group list exists before admission; defended progress retires earlier labs."""
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

PACKAGE = 'queue_roster_under_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[PACKAGE] = package
Store = importlib.import_module(PACKAGE + '.storage').QueueStore
sync_group = importlib.import_module(PACKAGE + '.sync').sync_group
queue_view = importlib.import_module(PACKAGE + '.runtime').queue_view
operations = importlib.import_module(PACKAGE + '.operations')
select_lab = importlib.import_module(PACKAGE + '.sheet_state').select_queue_lab


@pytest.fixture
def roster(tmp_path):
    store = Store(tmp_path / 'queue.sqlite3')
    course = {'name': 'Предмет', 'timezone': 'UTC', 'google': {'student-name-column': 1},
              'queue': {}, 'labs': {str(i): {'short-name': f'ЛР{i}'} for i in range(1, 4)}}
    grid = [['', '', 'GitHub', '2026-10-01', '2026-11-10', '2026-11-20'],
            ['№', 'ФИО', '', 'ЛР1', 'ЛР2', 'ЛР3'],
            ['1', 'Готовый Студент', 'ready', 'v', '', ''],
            ['2', 'Будущий Студент', 'future', 'vv', '', ''],
            ['3', 'Начинающий Студент', '', '', '', ''],
            ['4', 'Закончивший Студент', 'done', 'vv', 'vv', 'vv']]
    now = int(datetime(2026, 11, 8, tzinfo=timezone.utc).timestamp())

    def sync(at=now):
        return sync_group(store, 'c', 'g', course, grid,
                          parse_deadline=lambda v: datetime.fromisoformat(v).replace(tzinfo=timezone.utc), now=at)

    sync()
    store.create_session('s', 'c', 'g', 'teacher', starts_at=now+300, ends_at=now+5700,
                         local_date='2026-11-08', now=now)

    def view(mode='priority', at=now, session='s', teacher=False):
        course['queue']['random'] = mode == 'random'
        function = operations.teacher_view if teacher else queue_view
        return function(store, 'c', 'g', course, deadlines_available=True, now=at, session_id=session)

    return store, course, grid, now, sync, view


@pytest.mark.parametrize('cells,expected', [
    ({'1':'vv','2':'','3':'v'}, ('3','3',False)),
    ({'1':'v','2':'vv','3':'v'}, ('3','3',False)),
    ({'1':'v','2':'vv','3':''}, (None,'3',False)),
    ({'1':'','2':'','3':''}, (None,'1',False)),
    ({'1':'v','2':'v','3':''}, ('1','1',False)),
    ({'1':'vv','2':'vv','3':'vv'}, (None,None,True)),
])
def test_progress_frontier(cells, expected):
    assert select_lab(['1','2','3'], cells) == expected


def test_history_also_establishes_frontier():
    assert select_lab(['1','2','3'], {'1':'v','3':'v'}, defended_in_history={'2'})[0] == '3'


def test_random_roster_includes_everyone_without_admission(roster):
    store, _, _, _, _, view = roster
    rows = view('random')['entries']
    assert len(rows) == 4
    assert {r['full_name'] for r in rows} >= {'Начинающий Студент','Закончивший Студент'}
    assert all(not r['will_attend'] and r['position'] is None for r in rows)
    assert all(r['priority'] is None for r in rows)
    assert not any(r['github'].startswith('__sheet__') for r in rows)
    placeholder = next(r for r in rows if r['full_name'] == 'Начинающий Студент')
    assert placeholder['ready'] is False
    with pytest.raises(ValueError):
        store.attendance('s', placeholder['candidate_id'], True, now=roster[3])


def test_virtual_priority_uses_next_lab_and_completed_is_omitted(roster):
    store, _, _, _, _, view = roster
    rows = view()['entries']
    assert len(rows) == 3
    future = next(r for r in rows if r['github']=='future')
    assert future['lab_key']=='2' and future['priority']==1
    assert future['virtual_priority'] and not future['ready']
    assert next(r for r in rows if r['github']=='ready')['priority']==-1
    with store.connection() as con:
        assert con.execute("SELECT queue_state FROM queue_members WHERE github_login='done'").fetchone()[0]=='not_attending'
    # The teacher sees the whole class, including a student without a GitHub account.
    teacher=view(teacher=True)
    assert len(teacher['absent_entries'])==3
    assert len(teacher['student_catalog'])==4


def test_registration_does_not_reroll_or_duplicate_a_place(roster):
    store, _, grid, now, sync, view = roster
    initial = view('random')['entries']
    order = [r['student_id'] for r in initial]
    blank = next(r for r in initial if not r['github'])
    grid[4][2:] = ['new-login', 'v', '', '']
    sync(now+1)
    rows = view('random', at=now+2)['entries']
    assert [r['student_id'] for r in rows] == order
    registered = next(r for r in rows if r['github']=='new-login')
    assert registered['student_id']==blank['student_id'] and registered['ready']
    # Order survives reopen/restart, not just a Python cache.
    reopened=Store(store.path)
    assert [r['student_id'] for r in queue_view(reopened,'c','g',roster[1],deadlines_available=True,now=now+3,session_id='s')['entries']]==order


def test_new_session_has_its_own_shuffle_and_resets_consent(roster):
    store, _, _, now, _, view = roster
    row=next(r for r in view('random')['entries'] if r['github']=='ready')
    store.attendance('s',row['candidate_id'],True,now=now+1)
    assert next(r for r in view('random',at=now+2)['entries'] if r['github']=='ready')['will_attend']
    store.create_session('next','c','g','teacher',starts_at=now+86400,ends_at=now+91800,
                         local_date='2026-11-09',now=now+3)
    rows=view('random',session='next',at=now+3)['entries']
    assert len(rows)==4 and all(not r['will_attend'] for r in rows)
    with store.connection() as con:
        first={r['student_id']:r['random_key'] for r in con.execute("SELECT * FROM session_roster WHERE session_id='s'")}
        second={r['student_id']:r['random_key'] for r in con.execute("SELECT * FROM session_roster WHERE session_id='next'")}
    assert first.keys()==second.keys() and first!=second


def test_later_defense_retires_old_priority_and_keeps_one_current_place(roster):
    store, _, grid, now, sync, view = roster
    first=next(r for r in view()['entries'] if r['github']=='ready')
    store.attendance('s',first['candidate_id'],True,now=now+1)
    grid[2][3:]=['v','vv','v']
    sync(now+2)
    row=next(r for r in view(at=now+3)['entries'] if r['github']=='ready')
    assert row['lab_key']=='3' and row['priority']>0 and row['will_attend']
    assert len([r for r in view(at=now+3)['entries'] if r['github']=='ready'])==1
    with store.connection() as con:
        assert con.execute("SELECT status FROM queue_candidates WHERE candidate_id=?",(first['candidate_id'],)).fetchone()[0]=='inactive'


def test_waiting_old_lab_moves_to_tail_when_later_defense_appears(roster):
    store, _, grid, now, sync, view = roster
    first=next(r for r in view()['entries'] if r['github']=='ready')
    store.attendance('s',first['candidate_id'],True,now=now+1)
    assert view(at=now+300,teacher=True)['round_entries'][0]['lab_key']=='1'
    grid[2][3:]=['v','vv','v']
    sync(now+301)
    teacher=view(at=now+302,teacher=True)
    active=[r for r in teacher['round_entries'] if r.get('attempt',{}).get('status') in ('waiting','called')]
    assert len(active)==1 and active[0]['lab_key']=='3' and active[0]['is_repeat']


def test_missing_deadline_locks_random_but_still_lists_entire_group(roster):
    store, course, grid, now, sync, _=roster
    grid[0][4]=''
    summary=sync(now+1)
    result=queue_view(store,'c','g',course,deadlines_available=summary['deadlines_available'],now=now+2,session_id='s')
    assert result['mode']=='random' and result['mode_locked'] and len(result['entries'])==4


def test_an_existing_login_keeps_the_placeholder_random_slot(roster):
    store, _, grid, now, sync, view = roster
    original=view('random')['entries']
    old=next(r for r in original if not r['github'])
    with store.connection() as con:
        key=con.execute("SELECT random_key FROM session_roster WHERE session_id='s' AND student_id=?",(old['student_id'],)).fetchone()[0]
    # Already known in the app, but not previously linked to this sheet row.
    real_id=store.student('Начинающий Студент','existing-login',now=now+1)
    grid[4][2:] = ['existing-login','v','','']
    sync(now+2)
    rows=view('random',at=now+3)['entries']
    assert len(rows)==4
    assert next(r for r in rows if r['github']=='existing-login')['student_id']==real_id
    with store.connection() as con:
        assert con.execute("SELECT random_key FROM session_roster WHERE session_id='s' AND student_id=?",(real_id,)).fetchone()[0]==key


def test_same_named_students_without_github_do_not_collapse(roster):
    _, _, grid, now, sync, view = roster
    grid.append(['5','Начинающий Студент','','','',''])
    sync(now+1)
    before=[r for r in view('random',at=now+2)['entries'] if r['full_name']=='Начинающий Студент']
    assert len(before)==2 and before[0]['student_id']!=before[1]['student_id']
    grid[-1][2:] = ['second-name','v','','']
    sync(now+3)
    after=view('random',at=now+4)['entries']
    assert len(after)==5
    assert len([r for r in after if r['full_name']=='Начинающий Студент'])==2
    assert next(r for r in after if r['github']=='second-name')['student_id'] in {r['student_id'] for r in before}


def test_called_retired_lab_does_not_reappear_as_auto_retry(roster):
    store, _, grid, now, sync, view = roster
    row=next(r for r in view()['entries'] if r['github']=='ready')
    store.attendance('s',row['candidate_id'],True,now=now+1)
    teacher=view(at=now+300,teacher=True)
    entry=teacher['round_entries'][0]['attempt']['entry_id']
    operations.call_next(store,'s',now=now+301,actor='test')
    grid[2][3:]=['v','vv','v']
    sync(now+302)
    operations.record_result(store,entry,'failed',now=now+303,actor='test')
    with store.connection() as con:
        assert not con.execute("SELECT 1 FROM queue_entries WHERE candidate_id=? AND status IN ('waiting','called')",(row['candidate_id'],)).fetchone()
