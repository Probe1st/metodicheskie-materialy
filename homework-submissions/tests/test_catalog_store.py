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


def test_legacy_catalog_preserves_subject_topic_ownership_and_stable_ids(store):
    snapshot = store.snapshot()
    assert [item.name for item in snapshot.groups] == ['Группа А']
    assert [item.name for item in snapshot.subjects] == ['Предмет А', 'Предмет Б', 'Без предмета']
    assert [(item.name, next(subject.name for subject in snapshot.subjects if subject.id == item.subject_id))
            for item in snapshot.topics] == [
        ('Тема А', 'Предмет А'), ('тема а', 'Предмет Б'),
        ('Тема Б', 'Предмет Б'), ('Ещё одна тема', 'Предмет А'),
    ]
    assert snapshot.contains('Группа А', 'Предмет Б', 'тема а')
    assert not snapshot.contains('Группа А', 'Предмет Б', 'Ещё одна тема')
    migrated = json.loads(store.path.read_text(encoding='utf-8'))
    assert migrated['version'] == 3
    assert migrated['unassigned_subject_id'] == str(snapshot.unassigned_subject_id)
    assert migrated['topics'][0]['subject_id'] == str(snapshot.topics[0].subject_id)
    assert store.snapshot() == snapshot


@pytest.mark.parametrize('kind', ('group', 'subject'))
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


def test_topic_update_and_bulk_move_preserve_ids_and_reject_conflicts_atomically(store):
    snapshot = store.snapshot()
    first, second, fallback = snapshot.subjects
    created = store.create_topic(' Общая тема ', first.id)
    same_name_other_subject = store.create_topic('общая ТЕМА', second.id)
    assert created.subject_id == first.id
    assert same_name_other_subject.subject_id == second.id
    with pytest.raises(CatalogNameConflict):
        store.create_topic('Общая тема', first.id)
    moved = store.update_topic(created.id, 'Общая тема', fallback.id)
    assert moved.id == created.id and moved.subject_id == fallback.id
    before = store.snapshot()
    with pytest.raises(CatalogNameConflict):
        store.move_topics((created.id, same_name_other_subject.id), first.id)
    assert store.snapshot() == before
    store.update_topic(same_name_other_subject.id, 'Другой заголовок', second.id)
    changed = store.move_topics((created.id, same_name_other_subject.id), fallback.id)
    assert {item.id for item in changed} == {created.id, same_name_other_subject.id}
    assert all(item.subject_id == fallback.id for item in changed)
    assert store.delete_topic(created.id) == moved


def test_deleting_subject_moves_topics_or_rejects_conflict_without_changes(store):
    subject_a, subject_b, fallback = store.snapshot().subjects
    conflict = store.create_topic('Тема А', fallback.id)
    before = store.snapshot()
    with pytest.raises(CatalogNameConflict):
        store.delete_subject(subject_a.id)
    assert store.snapshot() == before
    store.delete_topic(conflict.id)
    store.delete_subject(subject_a.id)
    after = store.snapshot()
    assert subject_a not in after.subjects
    assert all(topic.subject_id != subject_a.id for topic in after.topics)
    assert any(topic.name == 'Тема А' and topic.subject_id == fallback.id for topic in after.topics)
    with pytest.raises(ValueError):
        store.delete_subject(fallback.id)
    with pytest.raises(ValueError):
        store.rename_subject(fallback.id, 'Новое имя')


def test_v2_migration_preserves_topic_ids_in_existing_fallback(tmp_path):
    path = tmp_path / 'catalog.json'
    subject_id = UUID(int=10)
    topic_id = UUID(int=11)
    path.write_text(json.dumps({
        'version': 2, 'groups': [],
        'subjects': [{'id': str(subject_id), 'name': 'Без предмета'}],
        'topics': [{'id': str(topic_id), 'name': 'Историческая тема'}],
    }, ensure_ascii=False), encoding='utf-8')
    store = create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': path})
    snapshot = store.snapshot()
    assert snapshot.unassigned_subject_id == subject_id
    assert snapshot.topics[0].id == topic_id
    assert snapshot.topics[0].subject_id == subject_id
    assert store.snapshot() == snapshot
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 3


def test_legacy_migration_canonicalizes_existing_fallback_subject(tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({
        'groups': [], 'disciplines': {' без ПРЕДМЕТА ': ['Тема без привязки']},
    }, ensure_ascii=False), encoding='utf-8')
    store = create_catalog_store({'CATALOG_PROVIDER': 'json', 'CATALOG_PATH': path})
    snapshot = store.snapshot()
    assert [item.name for item in snapshot.subjects] == ['Без предмета']
    assert snapshot.topics[0].subject_id == snapshot.unassigned_subject_id
    assert store.snapshot() == snapshot



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
    assert json.loads(path.read_text(encoding='utf-8'))['version'] == 3


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
    {'version': 4, 'groups': [], 'disciplines': {}},
    {'version': 2.0, 'groups': [], 'subjects': [], 'topics': []},
    {'version': 3, 'groups': [], 'subjects': [{'id': str(UUID(int=1)), 'name': 'Без предмета'}],
     'topics': [{'id': str(UUID(int=2)), 'name': 'Тема', 'subject_id': str(UUID(int=3))}],
     'unassigned_subject_id': str(UUID(int=1))},
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
