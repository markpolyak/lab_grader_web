"""Чтение значений открытой XLSX-книги без зависимостей и записи файла."""
from datetime import datetime, timedelta
from io import BytesIO
import math
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
MAX_XML_BYTES = 32 * 1024 * 1024


def parse_workbook(content):
    try:
        with ZipFile(BytesIO(content)) as book:
            if len(book.infolist()) > 1000 or sum(item.file_size for item in book.infolist()) > MAX_XML_BYTES:
                raise ValueError('Распакованная таблица слишком велика')
            def xml(path):
                data = book.read(path)
                if b'<!DOCTYPE' in data.upper():
                    raise ValueError('DTD в файле таблицы не поддерживается')
                return ET.fromstring(data)
            shared = []
            if 'xl/sharedStrings.xml' in book.namelist():
                shared = [''.join(node.itertext()) for node in xml('xl/sharedStrings.xml').findall('s:si', NS)]
            dates = set()
            if 'xl/styles.xml' in book.namelist():
                styles = xml('xl/styles.xml')
                custom = {int(node.attrib['numFmtId']): node.attrib.get('formatCode', '') for node in styles.findall('s:numFmts/s:numFmt', NS)}
                for index, style in enumerate(styles.findall('s:cellXfs/s:xf', NS)):
                    number = int(style.attrib.get('numFmtId', '0'))
                    code = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', '', custom.get(number, '')).lower()
                    if number in range(14, 23) or number in (45, 46, 47) or re.search('[dy]', code):
                        dates.add(index)
            workbook = xml('xl/workbook.xml')
            properties = workbook.find('s:workbookPr', NS)
            epoch = datetime(1904, 1, 1) if properties is not None and properties.attrib.get('date1904') in ('1', 'true') else datetime(1899, 12, 30)
            links = {node.attrib['Id']: node.attrib.get('Target', '') for node in xml('xl/_rels/workbook.xml.rels') if node.attrib.get('TargetMode') != 'External'}
            result = {}
            for sheet in workbook.findall('s:sheets/s:sheet', NS):
                target = links.get(sheet.attrib[REL], '')
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl', target))
                if not path.startswith('xl/'):
                    raise ValueError('Некорректная ссылка на лист XLSX')
                rows = []
                for row in xml(path).findall('s:sheetData/s:row', NS):
                    number = int(row.attrib['r'])
                    if not 1 <= number <= 10000:
                        raise ValueError('Слишком много строк в листе')
                    while len(rows) < number:
                        rows.append([])
                    values = rows[number-1]
                    for cell in row.findall('s:c', NS):
                        match = re.fullmatch(r'([A-Z]+)[0-9]+', cell.attrib.get('r', ''))
                        if not match:
                            raise ValueError('Некорректный адрес ячейки')
                        column = 0
                        for letter in match[1]:
                            column = column * 26 + ord(letter) - ord('A') + 1
                        if column > 1000:
                            raise ValueError('Слишком много столбцов в листе')
                        while len(values) < column:
                            values.append('')
                        raw = cell.find('s:v', NS)
                        text = raw.text if raw is not None and raw.text else ''
                        kind = cell.attrib.get('t')
                        if kind == 's':
                            text = shared[int(text)]
                        elif kind == 'inlineStr':
                            inline = cell.find('s:is', NS)
                            text = ''.join(inline.itertext()) if inline is not None else ''
                        elif kind in (None, 'n') and int(cell.attrib.get('s', '0')) in dates and text:
                            serial = float(text)
                            if not math.isfinite(serial):
                                raise ValueError('Некорректная дата Excel')
                            value = epoch + timedelta(days=serial)
                            text = value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(timespec='seconds')
                        values[column-1] = text.strip()
                result[sheet.attrib['name']] = rows
            return result
    except (BadZipFile, KeyError, ET.ParseError, IndexError, OverflowError) as error:
        raise ValueError('Не удалось прочитать структуру XLSX Google-таблицы') from error
