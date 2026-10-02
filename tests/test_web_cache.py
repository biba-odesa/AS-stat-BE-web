import json
import tempfile
import unittest
import threading
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from app.config import Settings
from app.web_cache import ResultCache


class IsolatedCache(ResultCache):
    def __init__(self, directory, clock=None):
        super().__init__('link-data', **({'clock':clock} if clock else {}))
        self.directory = Path(directory)

    def get(self, settings, key, factory, **kwargs):
        return super().get(replace(settings, web_cache_path=self.directory), key, factory, **kwargs)


class WebCacheTests(unittest.TestCase):
    def test_memory_file_restart_fixed_ttl_corruption_errors_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings('url',Path('links'),web_cache_path=Path(directory),web_cache_max_bytes=1000)
            ticks=[100]
            create=lambda:ResultCache('test',clock=lambda:ticks[0])
            cache=create();calls=[]
            def factory():calls.append(1);return {'in':0,'out':None}
            self.assertEqual(cache.get(settings,('1',1,2),factory),{'in':0,'out':None})
            ticks[0]=200
            cache.get(settings,('1',1,2),factory)
            create().get(settings,('1',1,2),factory)
            self.assertEqual(len(calls),1)
            ticks[0]=1300
            create().get(settings,('1',1,2),factory)
            self.assertEqual(len(calls),2)
            path=next(Path(directory).glob('*.json'));path.write_text('invalid')
            create().get(settings,('1',1,2),factory)
            self.assertEqual(len(calls),3)
            with self.assertRaises(ValueError):create().get(settings,('bad',),lambda:(_ for _ in ()).throw(ValueError('failed')))
            for i in range(10):create().get(settings,('large',i),lambda:{'value':'x'*300})
            self.assertLessEqual(sum(p.stat().st_size for p in Path(directory).glob('*.json')),1000)
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_coalescing(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings('url',Path('links'),web_cache_path=Path(directory))
            cache=ResultCache('test');release=threading.Event();started=threading.Event();calls=[]
            def factory():calls.append(1);started.set();release.wait(2);return {'data':[None,0,2]}
            with ThreadPoolExecutor(2) as pool:
                first=pool.submit(cache.get,settings,('key',),factory);started.wait(1)
                second=pool.submit(cache.get,settings,('key',),factory);release.set()
                self.assertEqual(first.result(),second.result())
            self.assertEqual(len(calls),1)
