"""Request-local cache/VM accounting without URLs, query strings or response data."""
import functools
import hashlib
import logging
import os
import threading
import time
from collections import Counter
from contextvars import ContextVar

logger = logging.getLogger('uvicorn.error')
current = ContextVar('asstat_request_diagnostics', default=None)
lock = threading.Lock()
counters = {}
ENDPOINTS = {'/api/asn/sparkline.svg', '/api/link-usage/sparkline.svg',
             '/api/top-asn', '/api/link-usage/link', '/api/asn/series', '/api/asn/volumes'}


def cache_event(name, outcome, key, **details):
    stats = current.get()
    if stats is None:
        return
    digest = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
    if outcome in ('HIT','MISS','WAIT'):
        stats['cache'].append(f'{name}:{outcome}')
    with lock:
        counters.setdefault(stats['endpoint'], Counter())[f'cache_{outcome.lower()}'] += 1
    logger.info('asstat cache endpoint=%s pid=%s cache=%s outcome=%s key=%s bytes=%s reason=%s',
                stats['endpoint'], os.getpid(), name, outcome, digest,
                details.get('size','-'), details.get('reason','-'))


def vm_request(kind):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            stats = current.get()
            start = time.monotonic()
            success = False
            if stats is not None:
                stats['vm_count'] += 1
            try:
                result = function(*args, **kwargs)
                success = True
                return result
            finally:
                duration = time.monotonic() - start
                if stats is not None:
                    stats['vm_seconds'] += duration
                    with lock:
                        endpoint_counts = counters.setdefault(stats['endpoint'], Counter())
                        endpoint_counts['vm_queries'] += 1
                        endpoint_counts['vm_seconds'] += duration
                        endpoint_counts['vm_errors'] += not success
                    logger.info('asstat vm endpoint=%s pid=%s kind=%s duration_seconds=%.6f success=%s',
                                stats['endpoint'], os.getpid(), kind, duration, success)
        return wrapped
    return decorate


class RequestDiagnosticsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        endpoint = scope.get('path')
        if scope['type'] != 'http' or endpoint not in ENDPOINTS:
            return await self.app(scope, receive, send)
        stats = {'endpoint':endpoint, 'cache':[], 'vm_count':0, 'vm_seconds':0.0}
        token = current.set(stats)
        start = time.monotonic()
        status = 500
        async def diagnostic_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = list(message.get('headers',[]))
                headers.extend([(b'x-asstat-cache',(','.join(stats['cache']) or 'BYPASS').encode()),
                                (b'x-asstat-vm-queries',str(stats['vm_count']).encode()),
                                (b'x-asstat-vm-seconds',f"{stats['vm_seconds']:.6f}".encode())])
                message = {**message,'headers':headers}
            await send(message)
        try:
            await self.app(scope, receive, diagnostic_send)
        finally:
            with lock:
                counts = counters.setdefault(endpoint, Counter())
                counts['requests'] += 1
                counts['http_errors'] += status >= 400
            logger.info('asstat request endpoint=%s pid=%s status=%s cache=%s vm_queries=%s vm_seconds=%.6f duration_seconds=%.6f',
                        endpoint, os.getpid(), status, ','.join(stats['cache']) or 'BYPASS',
                        stats['vm_count'], stats['vm_seconds'], time.monotonic()-start)
            current.reset(token)
