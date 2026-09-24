from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from .catalog_store import Catalog, CatalogItem, CatalogItemNotFound, CatalogNameConflict


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

    def _read(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Catalog must be a JSON object')
        if 'version' in data:
            if type(data['version']) is not int or data['version'] != 2:
                raise ValueError(f"Unsupported catalog version: {data['version']}")
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
                    if name.casefold() in seen or uid in ids:
                        raise ValueError(f'Duplicate catalog {kind}')
                    seen.add(name.casefold())
                    ids.add(uid)
                    entry['id'] = str(uid)
                    entry['name'] = name
            return data
        if not isinstance(data.get('groups'), list) or not isinstance(data.get('disciplines'), dict):
            raise ValueError('Unsupported catalog format')

        def topics():
            for subject_topics in data['disciplines'].values():
                if not isinstance(subject_topics, list):
                    raise ValueError('Invalid legacy catalog topics')
                yield from subject_topics

        return {
            'version': 2,
            'groups': self._unique(data['groups']),
            'subjects': self._unique(data['disciplines'].keys()),
            'topics': self._unique(topics()),
        }

    def _source_is_legacy(self):
        source = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(source, dict):
            raise ValueError('Catalog must be a JSON object')
        if 'version' in source and (type(source['version']) is not int or source['version'] != 2):
            raise ValueError(f"Unsupported catalog version: {source['version']}")
        return 'version' not in source

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
        return Catalog(*(tuple(CatalogItem(UUID(item['id']), item['name']) for item in data[kind])
                         for kind in ('groups', 'subjects', 'topics')))

    def _list(self, kind):
        return getattr(self.snapshot(), kind)

    def _mutate(self, kind, action, item_id=None, name=None):
        with self._locked():
            data = self._read()
            entries = data[kind]
            current = next((item for item in entries if item['id'] == str(item_id)), None) if item_id else None
            if action != 'create' and current is None:
                raise CatalogItemNotFound(str(item_id))
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
    def create_topic(self, name): return self._mutate('topics', 'create', name=name)
    def rename_topic(self, item_id, name): return self._mutate('topics', 'rename', item_id, name)
    def delete_topic(self, item_id): return self._mutate('topics', 'delete', item_id)
