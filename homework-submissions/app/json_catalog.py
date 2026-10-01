from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from .catalog_store import Catalog, CatalogItem, CatalogItemNotFound, CatalogNameConflict, Topic


class JsonCatalogStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        with open(self.path.with_name(self.path.name + '.lock'), 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _name(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError('Name must be a string')
        result = value.strip()
        if not result:
            raise ValueError('Name must not be empty')
        return result

    @classmethod
    def _unique(cls, values):
        seen = set()
        result = []
        for value in values:
            name = cls._name(value)
            if name.casefold() not in seen:
                seen.add(name.casefold())
                result.append({'id': str(uuid4()), 'name': name})
        return result

    @staticmethod
    def _fallback(data):
        subject = next((item for item in data['subjects']
                        if item['name'].casefold() == 'без предмета'), None)
        if subject is None:
            subject = {'id': str(uuid4()), 'name': 'Без предмета'}
            data['subjects'].append(subject)
        subject['name'] = 'Без предмета'
        data['unassigned_subject_id'] = subject['id']
        return subject['id']

    def _read(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Catalog must be a JSON object')
        if 'version' not in data:
            if not isinstance(data.get('groups'), list) or not isinstance(data.get('disciplines'), dict):
                raise ValueError('Unsupported catalog format')
            subjects = self._unique(data['disciplines'].keys())
            by_name = {item['name'].casefold(): item['id'] for item in subjects}
            topics = []
            seen = set()
            for subject_name, names in data['disciplines'].items():
                if not isinstance(names, list):
                    raise ValueError('Invalid legacy catalog topics')
                subject_id = by_name[self._name(subject_name).casefold()]
                for value in names:
                    name = self._name(value)
                    key = (subject_id, name.casefold())
                    if key not in seen:
                        topics.append({'id': str(uuid4()), 'name': name, 'subject_id': subject_id})
                        seen.add(key)
            migrated = {
                'version': 3, 'groups': self._unique(data['groups']),
                'subjects': subjects, 'topics': topics,
            }
            self._fallback(migrated)
            return migrated
        version = data['version']
        if type(version) is not int or version not in (2, 3):
            raise ValueError(f'Unsupported catalog version: {version}')
        for kind in ('groups', 'subjects', 'topics'):
            entries = data.get(kind)
            if not isinstance(entries, list):
                raise ValueError(f'Invalid catalog {kind}')
            seen = set()
            ids = set()
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
                    raise ValueError(f'Invalid catalog {kind} entry')
                name = self._name(entry.get('name'))
                uid = UUID(entry['id'])
                key = name.casefold()
                if kind == 'topics' and version == 3:
                    if not isinstance(entry.get('subject_id'), str):
                        raise ValueError('Invalid topic subject')
                    entry['subject_id'] = str(UUID(entry['subject_id']))
                    key = (entry['subject_id'], key)
                if key in seen or uid in ids:
                    raise ValueError(f'Duplicate catalog {kind}')
                seen.add(key)
                ids.add(uid)
                entry['id'] = str(uid)
                entry['name'] = name
        if version == 2:
            subject_id = self._fallback(data)
            for entry in data['topics']:
                entry['subject_id'] = subject_id
            data['version'] = 3
        else:
            if not isinstance(data.get('unassigned_subject_id'), str):
                raise ValueError('Invalid system subject')
            fallback_id = str(UUID(data['unassigned_subject_id']))
            data['unassigned_subject_id'] = fallback_id
            if not any(item['id'] == fallback_id and item['name'] == 'Без предмета'
                       for item in data['subjects']):
                raise ValueError('Invalid system subject')
            if any(item['name'].casefold() == 'без предмета' and item['id'] != fallback_id
                   for item in data['subjects']):
                raise ValueError('Duplicate system subject')
            subject_ids = {item['id'] for item in data['subjects']}
            if any(item['subject_id'] not in subject_ids for item in data['topics']):
                raise ValueError('Unknown topic subject')
        return data

    def _source_is_legacy(self):
        source = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(source, dict):
            raise ValueError('Catalog must be a JSON object')
        if 'version' in source and (type(source['version']) is not int or source['version'] not in (2, 3)):
            raise ValueError(f"Unsupported catalog version: {source['version']}")
        return source.get('version') != 3

    def _save(self, data):
        fd, name = tempfile.mkstemp(prefix='.catalog-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def _data(self):
        legacy = self._source_is_legacy()
        data = self._read()
        if not legacy:
            return data
        with self._locked():
            legacy = self._source_is_legacy()
            data = self._read()
            if legacy:
                self._save(data)
            return data

    def snapshot(self):
        data = self._data()
        return Catalog(
            *(tuple(CatalogItem(UUID(item['id']), item['name']) for item in data[kind])
              for kind in ('groups', 'subjects')),
            tuple(Topic(UUID(item['id']), item['name'], UUID(item['subject_id']))
                  for item in data['topics']),
            UUID(data['unassigned_subject_id']),
        )

    def _list(self, kind):
        return getattr(self.snapshot(), kind)

    def _mutate(self, kind, action, item_id=None, name=None):
        with self._locked():
            data = self._read()
            entries = data[kind]
            current = next((item for item in entries if item['id'] == str(item_id)), None) if item_id else None
            if action != 'create' and current is None:
                raise CatalogItemNotFound(str(item_id))
            if kind == 'subjects' and current and current['id'] == data['unassigned_subject_id']:
                raise ValueError('System subject cannot be changed')
            if action != 'delete':
                cleaned = self._name(name)
                if any(item['name'].casefold() == cleaned.casefold() and item is not current for item in entries):
                    raise CatalogNameConflict(cleaned)
            if action == 'create':
                current = {'id': str(uuid4()), 'name': cleaned}
                entries.append(current)
            elif action == 'rename':
                current['name'] = cleaned
            else:
                if kind == 'subjects':
                    moving = [item for item in data['topics'] if item['subject_id'] == current['id']]
                    reserved = {item['name'].casefold() for item in data['topics']
                                if item['subject_id'] == data['unassigned_subject_id']}
                    conflicts = [item['name'] for item in moving if item['name'].casefold() in reserved]
                    if conflicts:
                        raise CatalogNameConflict(conflicts)
                    for item in moving:
                        item['subject_id'] = data['unassigned_subject_id']
                entries.remove(current)
            result = CatalogItem(UUID(current['id']), current['name'])
            self._save(data)
            return result

    def list_groups(self): return self._list('groups')
    def create_group(self, name): return self._mutate('groups', 'create', name=name)
    def rename_group(self, item_id, name): return self._mutate('groups', 'rename', item_id, name)
    def delete_group(self, item_id): return self._mutate('groups', 'delete', item_id)
    def list_subjects(self): return self._list('subjects')
    def create_subject(self, name): return self._mutate('subjects', 'create', name=name)
    def rename_subject(self, item_id, name): return self._mutate('subjects', 'rename', item_id, name)
    def delete_subject(self, item_id): return self._mutate('subjects', 'delete', item_id)
    def list_topics(self): return self._list('topics')

    def _topic(self, action, item_id=None, name=None, subject_id=None):
        with self._locked():
            data = self._read()
            entries = data['topics']
            current = next((item for item in entries if item['id'] == str(item_id)), None) if item_id else None
            if action != 'create' and current is None:
                raise CatalogItemNotFound(str(item_id))
            if action != 'delete':
                if str(subject_id) not in {item['id'] for item in data['subjects']}:
                    raise CatalogItemNotFound(str(subject_id))
                cleaned = self._name(name)
                if any(item['subject_id'] == str(subject_id)
                       and item['name'].casefold() == cleaned.casefold() and item is not current
                       for item in entries):
                    raise CatalogNameConflict(cleaned)
            if action == 'create':
                current = {'id': str(uuid4()), 'name': cleaned, 'subject_id': str(subject_id)}
                entries.append(current)
            elif action == 'update':
                current['name'] = cleaned
                current['subject_id'] = str(subject_id)
            else:
                entries.remove(current)
            result = Topic(UUID(current['id']), current['name'], UUID(current['subject_id']))
            self._save(data)
            return result

    def create_topic(self, name, subject_id):
        return self._topic('create', name=name, subject_id=subject_id)

    def update_topic(self, item_id, name, subject_id):
        return self._topic('update', item_id, name, subject_id)

    def delete_topic(self, item_id):
        return self._topic('delete', item_id)

    def move_topics(self, item_ids, subject_id):
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise ValueError('Select distinct topics to move')
        with self._locked():
            data = self._read()
            destination = str(subject_id)
            if destination not in {item['id'] for item in data['subjects']}:
                raise CatalogItemNotFound(destination)
            requested = {str(item_id) for item_id in item_ids}
            selected = [item for item in data['topics'] if item['id'] in requested]
            if len(selected) != len(requested):
                raise CatalogItemNotFound('Unknown selected topic')
            staying = {item['name'].casefold() for item in data['topics']
                       if item['subject_id'] == destination and item['id'] not in requested}
            seen = set()
            duplicates = set()
            for item in selected:
                key = item['name'].casefold()
                if key in seen:
                    duplicates.add(key)
                seen.add(key)
            conflicts = [item['name'] for item in selected
                         if item['name'].casefold() in staying or item['name'].casefold() in duplicates]
            if conflicts:
                raise CatalogNameConflict(conflicts)
            if any(item['subject_id'] != destination for item in selected):
                for item in selected:
                    item['subject_id'] = destination
                self._save(data)
            return tuple(Topic(UUID(item['id']), item['name'], UUID(item['subject_id']))
                         for item in selected)
