"""Private atomic bounded owner/project snapshots; callers own record schemas."""
import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager

from harness.durable import transaction

MAX_BYTES = 1000000


def directory(namespace, owner, project):
    if namespace not in {"chat-activity", "chat-attention"}:
        raise ValueError("invalid owned store")
    from app_paths import app_data_dir

    # Scope components are canonical UUIDs, then hashed together. No user path.
    if str(uuid.UUID(owner)) != owner or str(uuid.UUID(project)) != project:
        raise ValueError('unbound owned origin')
    digest = hashlib.sha256(json.dumps([owner, project]).encode()).hexdigest()
    return app_data_dir() / namespace / digest


@contextmanager
def locked(directory):
    # App data is trusted; do not follow substituted store/lock links within it.
    for path in (directory.parent, directory, directory / '.owner.lock'):
        if path.is_symlink():
            raise ValueError('owned store link')
    with transaction(directory):
        yield


def _path(directory, filename):
    if filename not in {'observations.json', 'decisions.json'}:
        raise ValueError('invalid owned file')
    return directory / filename


def read(directory, owner, project, *, validate, max_records, filename='observations.json'):
    path = _path(directory, filename)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return [], False
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('owned store unavailable')
        try:
            data = json.loads(stream.read(MAX_BYTES + 1))
        except RecursionError:
            raise ValueError('owned store nesting exceeded') from None
    if (not isinstance(data, dict) or set(data) != {'version', 'owner_id', 'project_id', 'records', 'truncated'}
            or type(data['version']) is not int or data['version'] != 1
            or type(data['truncated']) is not bool or data['owner_id'] != owner or data['project_id'] != project
            or not isinstance(data['records'], list) or len(data['records']) > max_records):
        raise ValueError('invalid owned scope')
    records = [validate(r) for r in data['records']]
    if len({r['source_id'] for r in records}) != len(records):
        raise ValueError('duplicate owned origin')
    return records, data['truncated']


def write(directory, owner, project, records, truncated, *, filename='observations.json'):
    data = json.dumps(dict(version=1, owner_id=owner, project_id=project,
                           records=records, truncated=truncated), sort_keys=True).encode()
    if len(data) > MAX_BYTES:
        raise ValueError('owned store full')
    path = _path(directory, filename)
    temporary = directory / (uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)
