"""Публичная книга: группы, стили дат, пустые ячейки и ограничение архива."""
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import patch
from contextlib import contextmanager

import pytest
from grading.defense_queue.workbook import parse_workbook
from grading.defense_queue import public_sheet as public


def workbook():
    stream=BytesIO()
    with ZipFile(stream,'w') as z:
        z.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="4431" sheetId="1" r:id="r1"/><sheet name="4434" sheetId="2" r:id="r2"/><sheet name="График" sheetId="3" r:id="r3"/></sheets></workbook>''')
        z.writestr('xl/_rels/workbook.xml.rels','''<Relationships><Relationship Id="r1" Target="worksheets/a.xml"/><Relationship Id="r2" Target="worksheets/a.xml"/><Relationship Id="r3" Target="worksheets/b.xml"/></Relationships>''')
        z.writestr('xl/styles.xml','''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>''')
        z.writestr('xl/worksheets/a.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="C1" s="1"><v>46327</v></c><c r="D1" t="inlineStr"><is><t>GitHub</t></is></c></row><row r="2"><c r="C2" t="inlineStr"><is><t>ЛР3</t></is></c></row><row r="4"><c r="B4" t="inlineStr"><is><t>Тестовый Студент</t></is></c><c r="C4" t="inlineStr"><is><t>v</t></is></c><c r="D4" t="inlineStr"><is><t>test-user</t></is></c></row></sheetData></worksheet>''')
        z.writestr('xl/worksheets/b.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>''')
    return stream.getvalue()


def test_workbook_dates_and_sparse_cells():
    book=parse_workbook(workbook())
    assert book['4434'][0]==['','','2026-11-01','GitHub']
    assert book['4434'][2]==[]
    assert book['4434'][3][2]=='v'
    assert set(book)=={'4431','4434','График'}


def test_workbook_fetch_shared_between_all_groups():
    public._WORKBOOKS.clear()
    calls=[]
    @contextmanager
    def get(url,**kwargs):
        calls.append(kwargs)
        class Response:
            headers={}
            def raise_for_status(self):pass
            def iter_content(self,**kwargs):yield workbook()
        yield Response()
    course={'google':{'spreadsheet':'book-test-123','info-sheet':'График'},'queue':{'sheet_source':{'type':'public_xlsx'}}}
    with patch.object(public.requests,'get',get):
        assert public.list_public_groups(course)==['4431','4434']
        assert public.read_public_grid(course,'4434')[0][2]=='2026-11-01'
        assert public.read_public_grid(course,'4431')[3][2]=='v'
    assert len(calls)==1 and calls[0]['params']=={'format':'xlsx'}
    public._WORKBOOKS.clear()


def test_bad_archive_rejected():
    with pytest.raises(ValueError):parse_workbook(b'<html>Sign in</html>')
    with patch('grading.defense_queue.workbook.MAX_XML_BYTES',10):
        with pytest.raises(ValueError):parse_workbook(workbook())
