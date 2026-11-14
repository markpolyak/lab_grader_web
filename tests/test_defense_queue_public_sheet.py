"""Публичный экспорт читается без сервисного аккаунта; HTML не принимается за CSV."""
from contextlib import contextmanager
from unittest.mock import patch
import importlib
import sys
import types
from pathlib import Path

import pytest

package = types.ModuleType('queue_public_test')
package.__path__ = [str(Path(__file__).parents[1] / 'grading/defense_queue')]
sys.modules[package.__name__] = package
public = importlib.import_module('queue_public_test.public_sheet')
CSV = '\ufeff,,GitHub,2026-11-01\r\n№,ФИО,,ЛР1\r\n1,"Имя, Фамилия",student,v\r\n'.encode()


def course():
    return {'google':{'spreadsheet':'test-sheet-123'},'queue':{'sheet_source':{'type':'public_csv','worksheets':{'g':'123'}}}}


def test_csv_bom_quotes_and_unicode():
    rows = public.parse_public_csv(CSV)
    assert rows[0][2] == 'GitHub'
    assert rows[2] == ['1','Имя, Фамилия','student','v']


@pytest.mark.parametrize('content',[b'',b'<html>Sign in</html>',b',,GitHub,GitHub\n1,2,3\n',b'\xff\xfe'])
def test_reject_bad_exports(content):
    with pytest.raises(ValueError): public.parse_public_csv(content)


@pytest.mark.parametrize('patcher',[
    lambda c:c['google'].update(spreadsheet='../../private'),
    lambda c:c['queue']['sheet_source'].update(type='http'),
    lambda c:c['queue']['sheet_source'].update(worksheets={'g':True}),
    lambda c:c['queue']['sheet_source'].update(worksheets={'g':'https://example.com'}),
])
def test_bad_config_never_fetches(patcher):
    c=course();patcher(c)
    with patch.object(public.requests,'get') as get:
        with pytest.raises(ValueError): public.read_public_grid(c,'g')
        get.assert_not_called()


def test_fixed_google_get_no_authorization():
    calls=[]
    @contextmanager
    def get(url,**kwargs):
        calls.append((url,kwargs))
        class Response:
            headers={'content-type':'text/csv; charset=utf-8'}
            def raise_for_status(self): pass
            def iter_content(self,**kwargs): yield CSV
        yield Response()
    with patch.object(public.requests,'get',get):
        assert len(public.read_public_grid(course(),'g'))==3
    url,kwargs=calls[0]
    assert url=='https://docs.google.com/spreadsheets/d/test-sheet-123/export'
    assert kwargs['params']=={'format':'csv','gid':'123'}
    assert 'auth' not in kwargs and 'headers' not in kwargs


def test_html_and_oversize_download_are_rejected():
    for headers,chunk in [({'content-type':'text/html'},b'login'),({},b'x'*(public.MAX_BYTES+1))]:
        @contextmanager
        def get(*args,**kwargs):
            class Response:
                def raise_for_status(self): pass
                def iter_content(self,**kwargs): yield chunk
            response=Response();response.headers=headers
            yield response
        with patch.object(public.requests,'get',get):
            with pytest.raises(ValueError): public.read_public_grid(course(),'g')
