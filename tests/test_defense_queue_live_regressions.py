"""Симптомы реального листа: вводные работы не должны блокировать защиту ЛР2."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('live_regression',ROOT/'scripts/run_queue_live.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)


def test_intro_tasks_do_not_enter_os_defense_queue():
    grid=[['','','2026-09-21','-','2026-10-05','2026-10-19','GitHub'],
          ['№','ФИО','ЛР0','ЛР0.1','ЛР1','ЛР2',''],
          ['1','Тестовый Студент','v','','vv','v','test-user']]
    course=live.select_sheet_labs(live.base_course(),grid)
    assert course['queue']['required_labs']==['1','2']


def test_graph_marks_optional_bash_by_group_family():
    from grading.defense_queue.course_requirements import scored_labs
    graph=[['','Формы контроля','Срок сдачи','','','Баллы (макс.)'],
           ['','','443X','M311','4331з, 4332Кз','443X','M311','433Xз'],
           ['0.1','ЛР0.1. Bash','-','2026-02-01','-','','8',''],
           ['1','ЛР1. Threads','','','','6','6','6']]
    assert scored_labs(graph,'4434')=={'ЛР0.1':False,'ЛР1':True}
    assert scored_labs(graph,'M311')=={'ЛР0.1':True,'ЛР1':True}
    assert scored_labs(graph,'4332Кз')=={'ЛР0.1':False,'ЛР1':True}


def test_sync_skips_intro_and_exposes_all_sheet_deadlines(tmp_path):
    from grading.defense_queue.storage import QueueStore
    from grading.defense_queue.sync import sync_group, parse_queue_deadline
    from grading.sheets_client import parse_deadline
    grid=[['','','2026-09-20','-','2026-10-04','2026-10-18','GitHub'],
          ['№','ФИО','ЛР0','ЛР0.1','ЛР1','ЛР2',''],
          ['1','Тестовый Студент','v','','vv','v','test-user']]
    course=live.select_sheet_labs(live.base_course(),grid)
    store=QueueStore(tmp_path/'queue.db')
    summary=sync_group(store,'c','4434',course,grid,parse_deadline=lambda value:parse_queue_deadline(value,course,parse_deadline),now=1)
    assert summary['deadlines_available']
    assert summary['sheet_deadlines']['01']=={'available':True,'value':None,'source':'no_deadline','raw':'-'}
    assert summary['sheet_deadlines']['2']['value'] is not None
    with store.connection() as con:
        row=con.execute("SELECT * FROM queue_candidates WHERE status='eligible'").fetchone()
        assert row['lab_key']=='2'


def test_all_sheet_groups_get_independent_schedule_sources(monkeypatch):
    import grading.defense_queue.public_sheet as public
    import grading.defense_queue.schedule_sources.suai as suai
    grid=[['','','GitHub','2026-11-01'],['№','ФИО','','ЛР1'],['1','Студент','login','v']]
    book={'4431':grid,'4432':grid,'4434':grid,'Other':grid,'График':[]}
    monkeypatch.setattr(public,'read_public_workbook',lambda course:book)
    monkeypatch.setattr(public,'list_public_groups',lambda course:['4431','4432','4434','Other'])
    monkeypatch.setattr(suai,'load',lambda config,**kwargs:[{'key':config['group_id'],'starts_at':100,'ends_at':200}])
    html='<select id="selGroup"><option value="7018">4431</option><option value="7114">4432</option><option value="7116">4434</option></select>'
    course,grids,lessons,status=live.inspect_sources(live.base_course(),fetch=lambda url:html,now=1)
    assert set(grids)=={'4431','4432','4434','Other'}
    assert [course['queue']['schedule'][group]['group_id'] for group in ['4431','4432','4434']]==['7018','7114','7116']
    assert len(lessons['4432'])==1 and status['Other']=='group_not_found'
    assert course['queue']['schedule']['Other']['source']=='manual'
    assert live.allowed_path(f'/courses/{live.COURSE_ID}/groups/4432/queue/attendance','POST',groups=grids)
