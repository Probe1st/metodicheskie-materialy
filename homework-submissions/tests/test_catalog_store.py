import json
import subprocess
import sys
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from uuid import UUID

import pytest

from app import json_catalog
from app.catalog_store import CatalogItemNotFound, CatalogNameConflict, create_catalog_store


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({
        'groups': ['Группа А', 'группа а'],
        'disciplines': {
            'Предмет А': ['Тема А'],
            'Предмет Б': ['тема а', 'Тема Б'],
            'предмет а': ['Ещё одна тема'],
        },
    }, ensure_ascii=False), encoding='utf-8')
    return create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': path})


def test_legacy_catalog_migrates_deduplicated_entities_with_stable_uuids(store):
    snapshot = store.snapshot()
    assert [item.name for item in snapshot.groups] == ['Группа А']
    assert [item.name for item in snapshot.subjects] == ['Предмет А', 'Предмет Б']
    assert [item.name for item in snapshot.topics] == ['Тема А', 'Тема Б', 'Ещё одна тема']
    assert snapshot.contains('Группа А', 'Предмет Б', 'Тема А')
    assert isinstance(snapshot.groups[0].id, UUID)
    migrated = json.loads(store.path.read_text(encoding='utf-8'))
    assert migrated['version'] == 2
    assert migrated['groups'][0]['id'] == str(snapshot.groups[0].id)
    assert migrated['subjects'][0]['id'] == str(snapshot.subjects[0].id)
    assert migrated['topics'][0]['id'] == str(snapshot.topics[0].id)
    assert store.snapshot() == snapshot


@pytest.mark.parametrize('kind', ('group', 'subject', 'topic'))
def test_crud_preserves_identity_and_rejects_duplicate_names(store, kind):
    create = getattr(store, f'create_{kind}')
    rename = getattr(store, f'rename_{kind}')
    delete = getattr(store, f'delete_{kind}')
    list_items = getattr(store, f'list_{kind}s')
    created = create(' Новое имя ')
    assert created.name == 'Новое имя'
    with pytest.raises(CatalogNameConflict):
        create('новое ИМЯ')
    renamed = rename(created.id, 'Другое имя')
    assert renamed.id == created.id
    assert renamed.name == 'Другое имя'
    with pytest.raises(CatalogItemNotFound):
        rename(UUID(int=0), 'Не существует')
    assert delete(created.id) == renamed
    assert created.id not in [item.id for item in list_items()]
    with pytest.raises(CatalogItemNotFound):
        delete(created.id)



@pytest.mark.parametrize('name', (None, 42))
def test_create_rejects_non_string_name(store, name):
    with pytest.raises(ValueError, match='Name'):
        store.create_group(name)


def test_uuid_crud_is_independent_across_entity_types(tmp_path):
    item_id = UUID(int=0xABCDEF)
    stored_id = str(item_id).upper()

    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({
        'version': 2,
        'groups': [{'id': stored_id, 'name': 'Общее имя'}],
        'subjects': [{'id': stored_id, 'name': 'Общее имя'}],
        'topics': [],
    }, ensure_ascii=False), encoding='utf-8')
    store = create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': path})

    renamed = store.rename_group(item_id, 'Новое имя')

    assert renamed.id == item_id
    assert renamed.name == 'Новое имя'
    subject = store.list_subjects()[0]
    assert subject.id == item_id
    assert subject.name == 'Общее имя'


def test_concurrent_migration_reader_uses_persisted_uuids(store, monkeypatch):
    other = create_catalog_store({
        'CATALOG_PROVIDER': 'json',
        'CATALOG_PATH': store.path,
    })
    read_complete = Event()
    resume_read = Event()
    snapshots = Queue()
    original_read = store._read

    def pause_after_initial_read():
        data = original_read()
        read_complete.set()
        assert resume_read.wait(timeout=10)
        return data

    def take_snapshot():
        try:
            snapshots.put(store.snapshot())
        except Exception as error:
            snapshots.put(error)

    monkeypatch.setattr(store, '_read', pause_after_initial_read)
    reader = Thread(target=take_snapshot, daemon=True)
    reader.start()
    try:
        assert read_complete.wait(timeout=10)
        other.create_group('Параллельная запись')
    finally:
        resume_read.set()
    reader.join(timeout=10)

    assert not reader.is_alive()
    result = snapshots.get_nowait()
    assert not isinstance(result, Exception)
    persisted = other.snapshot()
    assert result.groups[0].id == persisted.groups[0].id


def test_failed_atomic_replace_keeps_previous_catalog_intact(store, monkeypatch):
    store.snapshot()
    original = store.path.read_bytes()
    original_unlink = json_catalog.os.unlink

    def fail_replace(source, destination):
        source = Path(source)
        assert Path(destination) == store.path
        assert source.parent == store.path.parent
        staged = json.loads(source.read_text(encoding='utf-8'))
        assert any(item['name'] == 'Не сохранено' for item in staged['groups'])
        raise OSError('simulated replace failure')

    def unlink_after_disappearance(path):
        original_unlink(path)
        raise FileNotFoundError(path)

    with monkeypatch.context() as patch:
        patch.setattr(json_catalog.os, 'replace', fail_replace)
        patch.setattr(json_catalog.os, 'unlink', unlink_after_disappearance)
        with pytest.raises(OSError, match='simulated replace failure'):
            store.create_group('Не сохранено')

    assert store.path.read_bytes() == original
    assert 'Не сохранено' not in [item.name for item in store.list_groups()]
    assert list(store.path.parent.glob('.catalog-*.tmp')) == []
    saved = store.create_group('Запись после сбоя')
    assert saved in store.list_groups()


def test_concurrent_process_writes_preserve_every_change_and_migrate_once(store):
    path = store.path
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        "import sys; "
        "from app.catalog_store import create_catalog_store; "
        "create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': sys.argv[1]})"
        ".create_group(sys.argv[2])"
    )

    processes = [
        subprocess.Popen(
            [sys.executable, '-c', script, str(path), f'Параллельная {index}'],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(12)
    ]
    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []

    snapshot = store.snapshot()
    names = [item.name for item in snapshot.groups]
    expected_names = {'Группа А'} | {f'Параллельная {index}' for index in range(12)}
    assert set(names) == expected_names
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 2


@pytest.mark.parametrize('payload', [
    [],
    {'version': 2, 'groups': [], 'subjects': []},
    {'version': 2, 'groups': [{'id': 'not-a-uuid', 'name': 'Группа'}], 'subjects': [], 'topics': []},
    {'version': 2,
     'groups': [{'id': str(UUID(int=1)), 'name': 'Группа'},
                {'id': str(UUID(int=1)), 'name': 'Другая группа'}],
     'subjects': [], 'topics': []},
    {'version': 2,
     'groups': [{'id': str(UUID(int=1)), 'name': 'Группа'},
                {'id': str(UUID(int=2)), 'name': ' группа '}],
     'subjects': [], 'topics': []},
    {'version': 3, 'groups': [], 'disciplines': {}},
    {'version': 2.0, 'groups': [], 'subjects': [], 'topics': []},
    {'groups': [None], 'disciplines': {}},
    {'groups': [], 'disciplines': {'Предмет': 'not a topic list'}},
])


def test_malformed_catalog_is_rejected(tmp_path, payload):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    store = create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': path})

    with pytest.raises(ValueError):
        store.snapshot()


def test_invalid_provider_fails_at_factory(tmp_path):
    with pytest.raises(ValueError, match='CATALOG_PROVIDER'):
        create_catalog_store({'CATALOG_PROVIDER': 'postgresql', 'CATALOG_PATH': tmp_path / 'catalog.json'})
