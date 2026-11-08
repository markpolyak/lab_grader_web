"""Course YAML updates must publish whole files and preserve concurrent changes."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest
import yaml
from fastapi.testclient import TestClient

import main


@pytest.fixture
def courses(tmp_path, monkeypatch):
    directory = tmp_path / 'courses'
    directory.mkdir()
    entries = []
    for number in range(8):
        course_id = f'course-{number}'
        filename = course_id + '.yaml'
        (directory / filename).write_text(yaml.safe_dump({'course': {'name': course_id}}))
        entries.append({'id': course_id, 'file': filename, 'status': 'active'})
    (directory / 'index.yaml').write_text(yaml.safe_dump({'courses': entries}))
    monkeypatch.setattr(main, 'COURSES_DIR', str(directory))
    monkeypatch.setattr(main, 'INDEX_FILE', str(directory / 'index.yaml'))
    monkeypatch.setattr(main.limiter, 'enabled', False)
    return directory


def client():
    result = TestClient(main.app)
    result.cookies.set('admin_session', main.signer.sign(main.ADMIN_LOGIN.encode()).decode())
    return result


def test_parallel_hides_preserve_all_index_updates(courses):
    start = Barrier(8)
    def hide(number):
        start.wait(timeout=10)
        return client().delete(f'/courses/course-{number}').status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(hide, range(8)))
    assert responses == [200] * 8
    index = yaml.safe_load((courses / 'index.yaml').read_text())
    assert len(index['courses']) == 8
    assert all(entry['status'] == 'hidden' for entry in index['courses'])


def test_parallel_upload_and_hide_preserve_index_and_files(courses):
    start = Barrier(8)
    def mutate(number):
        start.wait(timeout=10)
        if number < 4:
            return client().delete(f'/courses/course-{number}').status_code
        return client().post('/courses/upload', files={
            'file': (f'new-{number}.yaml', f'course:\n  name: New {number}\n', 'application/x-yaml'),
        }).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(mutate, range(8)))
    assert responses == [200] * 8
    entries = yaml.safe_load((courses / 'index.yaml').read_text())['courses']
    assert len(entries) == 12
    assert len({entry['id'] for entry in entries}) == 12
    assert sum(entry['status'] == 'hidden' for entry in entries) == 4
    assert all((courses / entry['file']).is_file() for entry in entries)


def test_parallel_upload_same_filename_only_publishes_one_course(courses):
    start = Barrier(6)
    def upload(number):
        start.wait(timeout=10)
        return client().post('/courses/upload', files={
            'file': ('same.yaml', f'course:\n  name: Writer {number}\n', 'application/x-yaml'),
        }).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(upload, range(6)))
    assert sorted(responses) == [200] + [400] * 5
    entries = yaml.safe_load((courses / 'index.yaml').read_text())['courses']
    assert len([entry for entry in entries if entry['file'] == 'same.yaml']) == 1
    assert yaml.safe_load((courses / 'same.yaml').read_text())['course']['name'].startswith('Writer ')


def test_parallel_course_edits_reject_stale_base(courses):
    base = client().get('/courses/course-0/edit').json()['content']
    start = Barrier(2)
    def edit(name):
        start.wait(timeout=10)
        return client().put('/courses/course-0/edit', json={
            'content': yaml.safe_dump({'course': {'name': name}}), 'original_content': base,
        }).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(edit, ['First writer', 'Second writer']))
    assert sorted(responses) == [200, 409]
    assert yaml.safe_load((courses / 'course-0.yaml').read_text())['course']['name'] in {'First writer', 'Second writer'}


def test_parallel_edits_validate_against_latest_revision(courses):
    target = courses / 'course-0.yaml'
    def content(revision):
        return yaml.safe_dump({'course': {'name': 'Revision test', 'labs': {'1': {'join': {'revision': revision}}}}})
    target.write_text(content(1))
    start = Barrier(2)
    def edit(revision):
        start.wait(timeout=10)
        return revision, client().put('/courses/course-0/edit', json={'content': content(revision)}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = dict(pool.map(edit, [2, 3]))
    assert responses[3] == 200
    assert responses[2] in {200, 400}
    assert yaml.safe_load(target.read_text())['course']['labs']['1']['join']['revision'] == 3


@pytest.mark.parametrize('operation', ['hide', 'upload'])
def test_failed_index_publication_preserves_previous_index(courses, monkeypatch, operation):
    index_path = courses / 'index.yaml'
    previous = index_path.read_bytes()
    original_replace = main.os.replace
    def fail_index(source, destination):
        if Path(destination) == index_path:
            raise OSError('disk full')
        original_replace(source, destination)
    with monkeypatch.context() as failure:
        failure.setattr(main.os, 'replace', fail_index)
        with pytest.raises(OSError, match='disk full'):
            if operation == 'hide':
                client().delete('/courses/course-0')
            else:
                client().post('/courses/upload', files={'file': ('retry.yaml', b'course: {}', 'application/x-yaml')})
    assert index_path.read_bytes() == previous
    assert not (courses / 'retry.yaml').exists()
    assert not list(courses.glob('*.tmp'))
    if operation == 'upload':
        response = client().post('/courses/upload', files={'file': ('retry.yaml', b'course: {}', 'application/x-yaml')})
        assert response.status_code == 200


def test_index_readers_never_observe_partial_yaml(courses):
    start = Barrier(2)
    stopped = Event()
    def reader():
        count = 0
        start.wait(timeout=10)
        while not stopped.is_set() or count < 100:
            index = yaml.safe_load((courses / 'index.yaml').read_text())
            assert len(index['courses']) == 8
            assert all(entry['id'] and entry['file'] for entry in index['courses'])
            count += 1
        return count
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(reader)
        start.wait(timeout=10)
        try:
            for number in range(8):
                assert client().delete(f'/courses/course-{number}').status_code == 200
        finally:
            stopped.set()
        assert reading.result(timeout=10) >= 100
