"""Bounded persistent numerical results, with fixed expiry and request coalescing."""
import hashlib
import json
import os
import tempfile
import threading
import time
from concurrent.futures import Future
from collections import OrderedDict
from app.diagnostics import cache_event

DISK_LOCK = threading.RLock()


class ResultCache:
    def __init__(self, name, clock=time.time):
        self.name, self.clock = name, clock
        self.lock = threading.RLock()
        self.entries, self.pending = OrderedDict(), {}
        self.workers = threading.BoundedSemaphore(2)
        self.memory_limit = 16 * 1024 * 1024

    def get(self, settings, key, factory, with_expiry=False, expiry_limit=None):
        digest = hashlib.sha256(json.dumps(key, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        identity = (str(settings.web_cache_path), settings.web_cache_ttl, digest)
        with self.lock:
            now = self.clock()
            for old in [k for k, v in self.entries.items() if v['expires'] <= now]:
                del self.entries[old]
            if identity in self.entries:
                self.entries.move_to_end(identity)
                cache_event(self.name, 'HIT', key, reason='memory')
                entry = self.entries[identity]
                result = json.loads(entry['payload'])
                return (result, entry['expires']) if with_expiry else result
            future = self.pending.get(identity)
            if future is None:
                if len(self.pending) >= 32:
                    from app.volumes import VMError
                    raise VMError('Too many pending cached requests', 503)
                future = self.pending[identity] = Future()
                owner = True
            else:
                owner = False
                cache_event(self.name, 'WAIT', key)
        if not owner:
            entry = future.result()
            result = json.loads(entry['payload'])
            return (result, entry['expires']) if with_expiry else result
        try:
            directory = settings.web_cache_path
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (self.name + '-' + digest + '.json')
            with DISK_LOCK:
                self.cleanup(directory, settings.web_cache_max_bytes)
                entry = self.read(path, digest, self.clock())
            if entry is not None:
                cache_event(self.name, 'HIT', key, reason='file')
            else:
                cache_event(self.name, 'MISS', key)
                with self.workers:
                    result = factory()
                computed = self.clock()
                entry = {'key': digest, 'computed': computed, 'expires': min(computed + settings.web_cache_ttl, expiry_limit) if expiry_limit is not None else computed + settings.web_cache_ttl,
                         'payload': json.dumps(result, separators=(',', ':'), allow_nan=False)}
                data = json.dumps(entry, separators=(',', ':')).encode()
                with DISK_LOCK:
                    if len(data) <= settings.web_cache_max_bytes:
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(dir=directory, prefix='.web-', suffix='.tmp', delete=False) as target:
                                temporary = target.name
                                target.write(data)
                                target.flush()
                                os.fsync(target.fileno())
                            os.replace(temporary, path)
                        finally:
                            if temporary and os.path.exists(temporary):
                                os.unlink(temporary)
                        self.cleanup(directory, settings.web_cache_max_bytes)
            with self.lock:
                if len(entry['payload']) <= self.memory_limit:
                    self.entries[identity] = entry
                    while sum(len(v['payload']) for v in self.entries.values()) > self.memory_limit or len(self.entries) > 128:
                        self.entries.popitem(last=False)
                future.set_result(entry)
            result = json.loads(entry['payload'])
            return (result, entry['expires']) if with_expiry else result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def read(self, path, digest, now):
        try:
            entry = json.loads(path.read_text(encoding='utf-8'))
            if (entry['key'] != digest or not isinstance(entry['computed'], (int, float))
                    or not entry['computed'] <= now < entry['expires']):
                return None
            json.loads(entry['payload'])
            return entry
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def cleanup(self, directory, budget):
        files = []
        for path in directory.glob('*.json'):
            try:
                stat = path.stat()
                entry = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(entry, dict) or not isinstance(entry.get('expires'), (int, float)) or entry['expires'] <= self.clock():
                    path.unlink(missing_ok=True)
                else:
                    files.append((stat.st_mtime, path, stat.st_size))
            except (OSError, ValueError, TypeError):
                path.unlink(missing_ok=True)
        size = sum(item[2] for item in files)
        for _, path, amount in sorted(files):
            if size <= budget:
                break
            path.unlink(missing_ok=True)
            size -= amount


top_cache = ResultCache('top-ranking')
link_cache = ResultCache('link-data')
