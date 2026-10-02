from test_web_cache import IsolatedCache
import tempfile
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from app.config import ROOT, Settings, ConfigurationError
from app.knownlinks import Link
from app.periods import ranking_period
from app.sparkline import SVGCache, render_svg, validate_timezone, time_label
from app.top_asn import fetch_top
from app import link_usage
from app.volumes import ParameterError, validate_parameters


async def request(path, parameters, headers=()):
    from app.main import app
    messages=[]
    async def receive():return {'type':'http.request','body':b'','more_body':False}
    async def send(message):messages.append(message)
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
               'scheme':'http','path':path,'raw_path':path.encode(),'query_string':urlencode(parameters).encode(),
               'root_path':'','headers':list(headers),'server':('test',80),'client':('test',1)},receive,send)
    first=next(message for message in messages if message['type']=='http.response.start')
    return first['status'],dict(first['headers']),b''.join(message.get('body',b'') for message in messages)


class SVGDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.isolated = IsolatedCache(directory.name)
        patched = patch('app.link_usage.link_cache', self.isolated)
        patched.start()
        self.addCleanup(patched.stop)

    def test_shared_stable_period_and_unchanged_view_asn(self):
        self.assertEqual(ranking_period(180001),ranking_period(181199))
        self.assertEqual(ranking_period(181200),(94800,181200))
        with patch('app.top_asn.query_vm',return_value=[]),patch('app.link_usage.parse_knownlinks',return_value=[]):
            top=fetch_top('url',20,now=181199)
            usage=link_usage.manifest(Settings('url',Path('unused')),now=181199)
        self.assertEqual((top['start'],top['end']),(usage['start'],usage['end']))
        self.assertEqual(top['end'],180000)
        _,_,a,b=validate_parameters('64496','both',None,None,now=181199)
        self.assertEqual(b,181140);self.assertEqual(b-a,23*3600)

    def test_timezone_dst_and_visible_timezone(self):
        before=int(datetime(2026,10,25,0,30,tzinfo=timezone.utc).timestamp())
        after=before+3600
        self.assertEqual(time_label(before,'Europe/Kyiv','%H:%M %z'),'03:30 +0300')
        self.assertEqual(time_label(after,'Europe/Kyiv','%H:%M %z'),'03:30 +0200')
        spring=int(datetime(2026,3,29,0,30,tzinfo=timezone.utc).timestamp())
        self.assertEqual(time_label(spring,'Europe/Kyiv','%H:%M %z'),'02:30 +0200')
        self.assertEqual(time_label(spring+3600,'Europe/Kyiv','%H:%M %z'),'04:30 +0300')
        data={'timestamps':[before,after], 'links':[{'color':'#abcdef','in':['1','2'],'out':['0',None]}]}
        localized=render_svg(data,tz='Europe/Kyiv')
        self.assertNotEqual(localized,render_svg(data,tz='UTC'))
        labels=[node.text for node in ET.fromstring(localized).findall('{*}text')]
        self.assertIn('Time (Europe/Kyiv)',labels)
        self.assertIn('10-25 03:30',labels)
        self.assertNotIn('10-25 00:30',labels)
        for value in ('','/etc/passwd','../UTC','Mars/Olympus','x'*129):
            with self.assertRaises(ParameterError):validate_timezone(value)

    def test_config_ttl_and_default_cache_expiration(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as directory:
            path=Path(directory)/'as-stat-web.conf'
            path.write_text('[svg]\ncache_ttl_seconds=900\n')
            with patch('app.config.CONFIG_PATH',path),patch.dict(os.environ,{},clear=True):
                self.assertEqual(Settings.from_env().svg_cache_ttl,900)
                with patch.dict(os.environ,{'ASSTAT_SVG_CACHE_TTL_SECONDS':'600'}):
                    self.assertEqual(Settings.from_env().svg_cache_ttl,600)
                path.write_text('[svg]\ncache_ttl_seconds=0\n')
                with self.assertRaises(ConfigurationError):Settings.from_env()
        ticks=[0];cache=SVGCache(clock=lambda:ticks[0]);calls=[]
        def factory():calls.append(1);return b'svg'
        cache.get('a',factory);ticks[0]=1199;cache.get('a',factory);self.assertEqual(len(calls),1)
        ticks[0]=1200;cache.get('a',factory);self.assertEqual(len(calls),2)

    def assert_svg_headers(self,headers,filename):
        self.assertEqual(headers[b'cache-control'],b'public, max-age=1200')
        self.assertIn(b'etag',headers);self.assertNotIn(b'set-cookie',headers)
        self.assertEqual(headers[b'content-disposition'],f'inline; filename="{filename}"'.encode())

    def test_asn_svg_http_cache_timezone_key_and_no_repeated_vm_query(self):
        ticks=[0];cache=SVGCache(clock=lambda:ticks[0])
        params={'asn':'64496','start':120,'end':86520,'tz':'Europe/Kyiv'}
        with patch('app.main.svg_cache',cache),patch('app.sparkline.parse_knownlinks',return_value=[]),patch('app.sparkline.query_series_vm',return_value=[]) as vm:
            status,headers,body=asyncio.run(request('/api/asn/sparkline.svg',params))
            self.assertEqual(status,200);self.assertEqual(headers[b'content-type'],b'image/svg+xml')
            self.assert_svg_headers(headers,'as64496-traffic.svg')
            self.assertIn(b'Time (Europe/Kyiv)',body)
            status,headers304,_=asyncio.run(request('/api/asn/sparkline.svg',params,[(b'if-none-match',headers[b'etag'])]))
            self.assertEqual(status,304);self.assert_svg_headers(headers304,'as64496-traffic.svg')
            ticks[0]=1199
            self.assertEqual(asyncio.run(request('/api/asn/sparkline.svg',params))[2],body);self.assertEqual(vm.call_count,1)
            utc={**params,'tz':'UTC'}
            status,utc_headers,utc_body=asyncio.run(request('/api/asn/sparkline.svg',utc))
            self.assertEqual(status,200);self.assertNotEqual(utc_body,body);self.assertNotEqual(utc_headers[b'etag'],headers[b'etag'])
            self.assertEqual(vm.call_count,2)
            ticks[0]=1200
            asyncio.run(request('/api/asn/sparkline.svg',params));self.assertEqual(vm.call_count,3)
            status,error_headers,_=asyncio.run(request('/api/asn/sparkline.svg',{**params,'tz':'Invalid/Zone'}))
            self.assertEqual(status,422);self.assertEqual(error_headers[b'cache-control'],b'no-store');self.assertEqual(vm.call_count,3)

    def test_link_svg_and_legend_share_cached_result(self):
        link=Link('a.b','Display name','#abcdef');cache=SVGCache()
        params={'link_id':'a.b','start':120,'end':86520,'tz':'Europe/Kyiv','v':'2'}
        with patch('app.link_usage.cache',cache),patch('app.link_usage.parse_knownlinks',return_value=[link]),patch('app.link_usage.query_vm',return_value=[]) as instant,patch('app.link_usage.query_series_vm',return_value=[]) as minute:
            status,headers,body=asyncio.run(request('/api/link-usage/sparkline.svg',params))
            self.assertEqual(status,200);self.assert_svg_headers(headers,'link-a.b-traffic.svg')
            self.assertEqual((instant.call_count,minute.call_count),(2,2))
            status,_,legend_body=asyncio.run(request('/api/link-usage/link',params))
            self.assertEqual(status,200);legend=json.loads(legend_body);self.assertNotIn('svg',legend)
            self.assertIn('tz=Europe%2FKyiv',legend['svg_url']);self.assertEqual(legend['start'],120)
            self.assertEqual((instant.call_count,minute.call_count),(2,2))
            status,headers304,_=asyncio.run(request('/api/link-usage/sparkline.svg',params,[(b'if-none-match',headers[b'etag'])]))
            self.assertEqual(status,304);self.assert_svg_headers(headers304,'link-a.b-traffic.svg')
            self.assertEqual((instant.call_count,minute.call_count),(2,2))
            status,headers,_=asyncio.run(request('/api/link-usage/sparkline.svg',{**params,'tz':'Invalid/Zone'}))
            self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
            self.assertEqual((instant.call_count,minute.call_count),(2,2))
