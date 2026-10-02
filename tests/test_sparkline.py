import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app.sparkline import render_svg, nice_scale, observed_peaks, speed_label, stack_bands, SVGCache, validate_sparkline, load_svg
from app.config import Settings
from app.volumes import ParameterError
from pathlib import Path


def example():
    return {'timestamps':[120,180,240], 'links':[
        {'color':'#112233','in':['10',None,'20'],'out':['2','3','4']},
        {'color':'#445566','in':['5','5','5'],'out':['1',None,'1']} ]}


class SparklineTests(unittest.TestCase):
    def test_input_output_independent_and_gap(self):
        bands, maximum = stack_bands(example())
        self.assertEqual(maximum,25)
        self.assertEqual(bands[0][2],[0,None,0])
        self.assertEqual(bands[1][3],[15,5,25])
        self.assertEqual(bands[3][3],[3,None,5])
        root=ET.fromstring(render_svg(example()))
        self.assertEqual(root.attrib['viewBox'],'0 0 840 260')
        self.assertEqual((root.attrib['width'],root.attrib['height']),('840','260'))
        polygons=root.findall('{*}polygon')
        self.assertEqual(len(polygons),6)
        first_y=[float(point.split(',')[1]) for point in polygons[0].attrib['points'].split()]
        self.assertTrue(all(y>=123 for y in first_y))
        output_y=[float(point.split(',')[1]) for point in polygons[3].attrib['points'].split()]
        self.assertTrue(all(y<=123 for y in output_y))
        first_x=[float(point.split(',')[0]) for point in polygons[0].attrib['points'].split()]
        self.assertLess(max(first_x),350)
        self.assertEqual(root.findall('{*}line')[-1].attrib['y1'],'123')

    def test_grid_labels_and_summed_observed_peaks(self):
        from decimal import Decimal
        peaks, incomplete = observed_peaks(example())
        self.assertEqual(peaks, {'in':Decimal(25), 'out':Decimal(5)})
        self.assertTrue(incomplete['in']); self.assertTrue(incomplete['out'])
        root = ET.fromstring(render_svg(example()))
        labels = [text.text for text in root.findall('{*}text')]
        self.assertIn('Time (UTC)', labels)
        self.assertIn('Output ↑ / Input ↓', labels)
        self.assertIn('Observed peak input: 25 bit/s | Observed peak output: 5 bit/s',labels)
        self.assertIn('0 bit/s',labels)
        self.assertEqual(labels.count('30 bit/s'),2)
        self.assertEqual(sum(line.attrib['x1']==line.attrib['x2'] for line in root.findall('{*}line')),7)
        for maximum in map(Decimal, ('0','0.004','1','26','1234567','9000000000')):
            limit, step, divisions = nice_scale(maximum)
            self.assertGreaterEqual(limit,maximum)
            self.assertEqual(limit,step*divisions)
        self.assertEqual(speed_label(Decimal(1500000000)), '1.5 Gbit/s')
        empty=example();empty['links']=[]
        empty_root=ET.fromstring(render_svg(empty))
        self.assertEqual(empty_root.findall('{*}line'),[])
        self.assertIn('No data',[t.text for t in empty_root.findall('{*}text')])

    def test_zero_no_data_and_escaping(self):
        data=example();data['links']=[{'color':'#112233','in':['0']*3,'out':[None]*3}]
        self.assertNotIn('No data',[t.text for t in ET.fromstring(render_svg(data)).findall('{*}text')])
        data['links'][0]['in']=[None]*3
        self.assertIn(b'No data',render_svg(data))
        data['links'][0]['in']=['1']*3;data['links'][0]['color']='"<&'
        ET.fromstring(render_svg(data))
        self.assertIn(b'&quot;',render_svg(data))

    def test_etag_changes_with_geometry(self):
        import hashlib
        current = render_svg(example())
        old = current.replace(b'840',b'240').replace(b'260',b'70')
        self.assertNotEqual(hashlib.sha256(current).digest(),hashlib.sha256(old).digest())

    def test_period_validation(self):
        self.assertEqual(validate_sparkline('64496','120','86520'),('64496',120,86520))
        for start,end in [(None,None),('120','180'),('121','86521'),('120',None)]:
            with self.assertRaises(ParameterError):validate_sparkline('1',start,end)

    def test_cache_ttl_limits_and_errors(self):
        now=[0]; cache=SVGCache(ttl=300,max_entries=2,max_bytes=6,clock=lambda:now[0])
        calls=[]
        def build():calls.append(1);return b'abc'
        first=cache.get('a',build);self.assertEqual(cache.get('a',build),first)
        self.assertEqual(len(calls),1)
        cache.get('b',build);cache.get('c',build)
        self.assertNotIn('a',cache.entries);self.assertLessEqual(cache.size,6)
        now[0]=301;cache.get('c',build);self.assertEqual(len(calls),4)
        def fail():raise ValueError('failure')
        for _ in range(2):
            with self.assertRaises(ValueError):cache.get('error',fail)
        self.assertNotIn('error',cache.entries)

    def test_coalescing(self):
        cache=SVGCache();started=threading.Event();release=threading.Event();calls=[]
        def build():calls.append(1);started.set();release.wait(2);return b'svg'
        with ThreadPoolExecutor(max_workers=4) as pool:
            first=pool.submit(cache.get,'a',build);started.wait(1)
            others=[pool.submit(cache.get,'a',build) for _ in range(3)]
            release.set()
            self.assertEqual([f.result() for f in [first,*others]],[(b'svg',first.result()[1])]*4)
        self.assertEqual(len(calls),1)

    def test_query_and_series_reuse(self):
        settings=Settings('http://127.0.0.1:8428',Path('unused'))
        with patch('app.sparkline.parse_knownlinks',return_value=[]),patch('app.sparkline.query_series_vm',return_value=[]) as query:
            ET.fromstring(load_svg(settings,'64496',120,86520))
            self.assertEqual(query.call_args.args[1],'sum_over_time(asstat_traffic_bytes{asn="64496"}[1ms]) * 8 / 60')
            self.assertEqual(query.call_args.args[2:],(120,86520))

    def test_http_etag_and_error_cache(self):
        import asyncio
        from app.main import app
        async def request(query, headers=[]):
            messages=[]
            async def receive():return {'type':'http.request','body':b'','more_body':False}
            async def send(message):messages.append(message)
            await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET',
                       'scheme':'http','path':'/api/asn/sparkline.svg','raw_path':b'/api/asn/sparkline.svg',
                       'query_string':query.encode(),'root_path':'','headers':headers,
                       'server':('test',80),'client':('test',1)},receive,send)
            first=next(m for m in messages if m['type']=='http.response.start')
            return first['status'],dict(first['headers'])
        with patch('app.main.svg_cache',SVGCache()),patch('app.main.load_svg',return_value=b'<svg/>') as build:
            status,headers=asyncio.run(request('asn=64496&start=120&end=86520'))
            self.assertEqual(status,200)
            self.assertEqual(headers[b'content-type'],b'image/svg+xml')
            self.assertEqual(headers[b'cache-control'],b'public, max-age=1200')
            status,_=asyncio.run(request('asn=64496&start=120&end=86520',[(b'if-none-match',headers[b'etag'])]))
            self.assertEqual(status,304);self.assertEqual(build.call_count,1)
            status,headers=asyncio.run(request('asn=1&start=120&end=180'))
            self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
            status,headers=asyncio.run(request('start=120&end=86520'))
            self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
