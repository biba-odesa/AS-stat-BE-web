from test_web_cache import IsolatedCache
import tempfile
import asyncio
import json
import unittest
from unittest.mock import patch

from app.knownlinks import Link
from app.sparkline import SVGCache, validate_timezone, validate_sparkline
from test_svg_delivery import request

EXACT = {'asn':'64500','start':1790847600,'end':1790934000,'tz':'Europe/Kiev','v':'5'}


class LegacyTimezoneTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.isolated = IsolatedCache(directory.name)
        patched = patch('app.link_usage.link_cache', self.isolated)
        patched.start()
        self.addCleanup(patched.stop)

    def test_exact_asn_url_alias_shared_cache_and_minute_only_boundaries(self):
        self.assertEqual(validate_timezone('Europe/Kiev').key,'Europe/Kyiv')
        cache=SVGCache()
        with patch('app.main.svg_cache',cache),patch('app.sparkline.parse_knownlinks',return_value=[]),patch('app.sparkline.query_series_vm',return_value=[]) as vm:
            for parameters in (EXACT,{**EXACT,'tz':'Europe/Kyiv'}):
                status,headers,body=asyncio.run(request('/api/asn/sparkline.svg',parameters))
                self.assertEqual(status,200);self.assertIn(b'Time (Europe/Kyiv)',body)
                if parameters is EXACT: first_etag=headers[b'etag']
                else:self.assertEqual(headers[b'etag'],first_etag)
            self.assertEqual(vm.call_count,1)
            self.assertEqual(vm.call_args.args[2:],(EXACT['start'],EXACT['end']))
            # Legacy URLs may use any completed minute boundary, not just 20-minute boundaries.
            minute_period={**EXACT,'start':EXACT['start']+60,'end':EXACT['end']+60}
            self.assertNotEqual(minute_period['end']%1200,0)
            status,_,_=asyncio.run(request('/api/asn/sparkline.svg',minute_period));self.assertEqual(status,200)
            self.assertEqual(vm.call_count,2)
            for tz in ('Europe/Invalid',''):
                status,headers,_=asyncio.run(request('/api/asn/sparkline.svg',{**EXACT,'tz':tz}))
                self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
            self.assertEqual(vm.call_count,2)
        for a,b in ((EXACT['start']+1,EXACT['end']+1),(EXACT['start'],EXACT['end']-60)):
            with self.assertRaises(ValueError):validate_sparkline('64500',str(a),str(b))
        with patch('app.volumes.time.time',return_value=EXACT['end']-1):
            with self.assertRaises(ValueError):validate_sparkline('64500',str(EXACT['start']),str(EXACT['end']))

    def test_link_alias_shared_svg_legend_cache_and_legacy_period(self):
        cache=SVGCache();parameters={**EXACT,'link_id':'example-link','v':'2'};parameters.pop('asn')
        with patch('app.link_usage.cache',cache),patch('app.link_usage.parse_knownlinks',return_value=[Link('example-link','Example link','#abcdef')]),patch('app.link_usage.query_vm',return_value=[]) as instant,patch('app.link_usage.query_series_vm',return_value=[]) as minute:
            status,headers,body=asyncio.run(request('/api/link-usage/sparkline.svg',parameters))
            self.assertEqual(status,200);self.assertIn(b'Time (Europe/Kyiv)',body)
            status,alias_headers,_=asyncio.run(request('/api/link-usage/sparkline.svg',{**parameters,'tz':'Europe/Kyiv'}))
            self.assertEqual(status,200);self.assertEqual(headers[b'etag'],alias_headers[b'etag'])
            status,_,data=asyncio.run(request('/api/link-usage/link',parameters))
            self.assertEqual(status,200);self.assertIn('tz=Europe%2FKyiv',json.loads(data)['svg_url'])
            self.assertEqual((instant.call_count,minute.call_count),(2,2))
            status,_,_=asyncio.run(request('/api/link-usage/sparkline.svg',{**parameters,'start':EXACT['start']+60,'end':EXACT['end']+60}))
            self.assertEqual(status,200)
            status,headers,_=asyncio.run(request('/api/link-usage/sparkline.svg',{**parameters,'tz':'Invalid/Zone'}))
            self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
            self.assertEqual((instant.call_count,minute.call_count),(4,4))
