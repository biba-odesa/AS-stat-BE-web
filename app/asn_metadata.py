"""On-demand ASN metadata with bounded workers and a locked atomic JSON store."""
import fcntl
import json
import logging
import os
import queue
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener, proxy_bypass_environment
from app.config import Settings

import dns.exception
import dns.resolver

ROOT = Path(__file__).resolve().parents[1]
TTL = 42 * 86400
HOUR = 3600
DAY = 86400
logger = logging.getLogger(__name__)
FIELDS = ('name', 'country', 'name_source', 'country_source', 'name_fetched_at',
          'country_fetched_at', 'last_attempt_at', 'cymru_retry_after', 'retry_after')
TIME_FIELDS = ('name_fetched_at', 'country_fetched_at', 'last_attempt_at',
               'cymru_retry_after', 'retry_after')


def validate_asn(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value) or int(value) > 4294967295:
        raise ValueError('ASN must be a decimal string from 0 to 4294967295')
    return str(int(value))


def utc_stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    if value is None:
        return 0
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('timestamp must be UTC')
    return parsed.timestamp()


def blank(asn):
    return {'asn': asn, **dict.fromkeys(FIELDS)}


def fresh(record, field, now):
    return record.get(field) is not None and now < timestamp(record.get(field + '_fetched_at')) + TTL


def needs_update(record, now):
    if timestamp(record.get('retry_after')) > now:
        return False
    if fresh(record, 'name', now) and fresh(record, 'country', now):
        return False
    return not (fresh(record, 'name', now) and timestamp(record.get('cymru_retry_after')) > now)


class MetadataStore:
    def __init__(self, path=ROOT / 'data/asn-metadata.json'):
        self.path = Path(path)
        self.lock = threading.RLock()

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.path.with_suffix('.lock').open('a') as lockfile:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockfile, fcntl.LOCK_UN)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('invalid cache')
            for asn, record in data.items():
                if validate_asn(asn) != asn or not isinstance(record, dict) or record.get('asn') != asn:
                    raise ValueError('invalid cache record')
                if any(field not in record for field in FIELDS):
                    raise ValueError('missing cache fields')
                if record['name'] is not None and (not isinstance(record['name'], str) or not record['name'].strip()):
                    raise ValueError('invalid cached name')
                if record['country'] is not None and not re.fullmatch('[A-Z]{2}', record['country']):
                    raise ValueError('invalid cached country')
                for field in ('name_source', 'country_source'):
                    if record[field] not in (None, 'cymru', 'ripestat'):
                        raise ValueError('invalid source')
                for field in TIME_FIELDS:
                    timestamp(record[field])
            return data
        except (ValueError, TypeError, AttributeError, UnicodeError):
            backup = self.path.with_name(self.path.name + '.corrupt-' + uuid.uuid4().hex)
            os.replace(self.path, backup)
            logger.error('ASN metadata cache is invalid; preserved as %s', backup.name)
            return {}

    def _save(self, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.asn-metadata-', delete=False) as target:
                temporary = target.name
                json.dump(data, target, ensure_ascii=False, indent=2, sort_keys=True)
                target.write('\n'); target.flush(); os.fsync(target.fileno())
            os.replace(temporary, self.path)
            temporary = None
            fd = os.open(self.path.parent, os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def read(self):
        with self.locked():
            return self._read()

    def update(self, asn, change):
        with self.locked():
            data = self._read()
            record = data.get(asn, blank(asn)).copy()
            result = change(record)
            if result is not None:
                data[asn] = result
                self._save(data)
            return result


def parse_cymru(asn, text):
    parts = [part.strip() for part in text.split('|')]
    if len(parts) != 5 or validate_asn(parts[0]) != asn or not parts[2] or not parts[4]:
        raise ValueError('invalid Cymru record')
    country = parts[1].upper()
    if country in ('', '--'):
        country = None
    elif not re.fullmatch('[A-Z]{2}', country):
        raise ValueError('invalid country')
    return {'name': parts[4], 'country': country}


def cymru_lookup(asn):
    resolver = dns.resolver.Resolver()  # Use the VM resolver configuration, never an HTTP proxy.
    resolver.timeout = 3
    resolver.lifetime = 5
    try:
        answer = resolver.resolve(f'AS{asn}.asn.cymru.com', 'TXT', lifetime=5, search=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return 'absent', None
    records = [parse_cymru(asn, b''.join(item.strings).decode('utf-8')) for item in answer]
    if not records or any(record != records[0] for record in records):
        raise ValueError('empty or conflicting Cymru records')
    return 'success', records[0]


def http_timeout():
    return Settings.from_env().metadata_http_timeout


def parse_ripe(asn, payload):
    if not isinstance(payload, dict) or payload.get('status') != 'ok' or not isinstance(payload.get('data'), dict):
        raise ValueError('invalid RIPEstat response')
    data = payload['data']
    resource = str(data.get('resource', '')).upper().removeprefix('AS')
    if validate_asn(resource) != asn or 'holder' not in data:
        raise ValueError('RIPEstat resource mismatch')
    holder = data['holder']
    if holder is None or holder == '':
        return 'absent', None
    if not isinstance(holder, str) or not holder.strip():
        raise ValueError('invalid RIPEstat holder')
    return 'success', {'name': holder.strip(), 'country': None}


def ripe_lookup(asn):
    url = 'https://stat.ripe.net/data/as-overview/data.json?' + urlencode({'resource': f'AS{asn}'})
    settings = Settings.from_env()
    # Resolve configured NO_PROXY before creating the verified HTTPS transport.
    proxies = {} if proxy_bypass_environment("stat.ripe.net", settings.proxies) else settings.proxies
    with build_opener(ProxyHandler(proxies)).open(url, timeout=settings.metadata_http_timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('RIPEstat response too large')
    return parse_ripe(asn, json.loads(raw))


class RequestGate:
    def __init__(self, interval=1, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.lock = threading.Lock()
        self.next_start = 0

    def wait(self):
        with self.lock:
            delay = self.next_start - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_start = self.clock() + self.interval


class MetadataService:
    def __init__(self, store=None, cymru=cymru_lookup, ripe=ripe_lookup, clock=time.time,
                 gate=None, queue_size=300):
        self.store = store or MetadataStore()
        self.cymru, self.ripe, self.clock = cymru, ripe, clock
        self.gate = gate or RequestGate()
        self.queue = queue.Queue(maxsize=queue_size)
        self.pending = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.threads = []

    def start(self):
        if not self.threads:
            for index in range(2):
                worker = threading.Thread(target=self._worker, name=f'asn-metadata-{index}', daemon=True)
                self.threads.append(worker); worker.start()

    def close(self):
        self.stop.set()
        # Network calls have finite deadlines; no new work is accepted after shutdown.
        for thread in self.threads:
            thread.join(timeout=1)

    def batch(self, asns, refresh=False):
        records = self.store.read()
        now = self.clock()
        result = []
        for asn in asns:
            record = records.get(asn, blank(asn))
            deferred = False
            with self.lock:
                if refresh and not self.stop.is_set() and asn not in self.pending and needs_update(record, now):
                    try:
                        self.pending.add(asn)
                        self.queue.put_nowait(asn)
                    except queue.Full:
                        self.pending.discard(asn); deferred = True
                updating = asn in self.pending
            result.append({**record, 'updating': updating,
                           'status': 'updating' if updating else 'deferred' if deferred else 'cached' if record['name'] else 'unavailable',
                           'name_stale': record['name'] is not None and not fresh(record, 'name', now),
                           'country_stale': record['country'] is not None and not fresh(record, 'country', now)})
        return {'records': result}

    def _worker(self):
        while not self.stop.is_set():
            try: asn = self.queue.get(timeout=.2)
            except queue.Empty: continue
            try:
                self.refresh(asn)
            except Exception as exc:
                # Do not log exceptions containing proxy credentials or external payloads.
                logger.error('ASN metadata update failed AS%s (%s)', asn, type(exc).__name__)
            finally:
                with self.lock: self.pending.discard(asn)
                self.queue.task_done()

    def _lookup(self, source, asn):
        self.gate.wait()
        try:
            return source(asn)
        except Exception as exc:
            logger.warning('ASN metadata source failed AS%s (%s)', asn, type(exc).__name__)
            return 'error', None

    def refresh(self, asn):
        now = self.clock()
        def claim(record):
            if not needs_update(record, now): return None
            record['last_attempt_at'] = utc_stamp(now)
            record['retry_after'] = utc_stamp(now + HOUR)
            return record
        old = self.store.update(asn, claim)
        if old is None: return
        cymru_status, cymru_data = ('skipped', None)
        if timestamp(old['cymru_retry_after']) <= now:
            cymru_status, cymru_data = self._lookup(self.cymru, asn)
        ripe_status, ripe_data = ('skipped', None)
        if cymru_status != 'success' and not fresh(old, 'name', now):
            ripe_status, ripe_data = self._lookup(self.ripe, asn)
        finished = self.clock()
        def merge(record):
            if cymru_status == 'success':
                record.update(name=cymru_data['name'], name_source='cymru', name_fetched_at=utc_stamp(finished),
                              cymru_retry_after=None, retry_after=None)
                if cymru_data['country'] is not None:
                    record.update(country=cymru_data['country'], country_source='cymru', country_fetched_at=utc_stamp(finished))
                else:
                    record['cymru_retry_after'] = utc_stamp(finished + HOUR)
            else:
                if cymru_status != 'skipped': record['cymru_retry_after'] = utc_stamp(finished + HOUR)
                if ripe_status == 'success':
                    record.update(name=ripe_data['name'], name_source='ripestat', name_fetched_at=utc_stamp(finished))
                delay = DAY if cymru_status == 'absent' and ripe_status == 'absent' else HOUR
                record['retry_after'] = utc_stamp(finished + delay)
            return record
        self.store.update(asn, merge)
