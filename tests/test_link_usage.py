from test_web_cache import IsolatedCache
import tempfile
import asyncio
import hashlib
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app import link_usage as usage
from app.config import Settings
from app.knownlinks import Link
from app.sparkline import SVGCache, render_svg
from app.volumes import VMError, ParameterError

SETTINGS = Settings('http://127.0.0.1:8428',Path('unused'))
LINK = Link('a.b','Visible link','#abcdef')
START, END = 120, 86520


def vector(asns):
    return [{'metric':{'asn':asn},'value':[1,str(len(asns)-index)]} for index,asn in enumerate(asns)]


def minute(asn, direction, values):
    return {'metric':{'asn':asn,'direction':direction},'values':values}


def sample_data():
    top = {'in':['1','2'], 'out':['2','3']}
    colors = {asn:usage.PALETTE[i] for i,asn in enumerate(['1','2','3'])}
    matrix = [minute('1','in',[[120,'0'],[240,'10']]),minute('2','in',[[120,'5']]),
              minute('2','out',[[120,'2'],[240,'4']]),minute('3','out',[[120,'3']]),
              minute('3','in',[[120,'999']])]
    others = {'in':[{'metric':{'direction':'in'},'values':[[120,'2'],[240,'0']]}], 'out':[]}
    return top, colors, matrix, others


class LinkUsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.isolated = IsolatedCache(directory.name)
        patched = patch('app.link_usage.link_cache', self.isolated)
        patched.start()
        self.addCleanup(patched.stop)

    def test_manifest_and_validation(self):
        with patch('app.link_usage.parse_knownlinks',return_value=[LINK,Link('idle-example','Idle Example','#123456')]):
            manifest = usage.manifest(SETTINGS,now=END+59)
            self.assertEqual((manifest['start'],manifest['end']),(0,86400))
            self.assertEqual(len(manifest['links']),2)
            self.assertEqual(usage.validate_link(SETTINGS,'a.b',str(START),str(END)),(LINK,START,END))
            for name,a,b in [('bad"link',START,END),('unknown',START,END),('a.b',START+1,END),('a.b',START,None),('a.b',START,END-60)]:
                with self.assertRaises(ParameterError):usage.validate_link(SETTINGS,name,str(a),None if b is None else str(b))

    def test_fixed_direction_top_queries_and_colors(self):
        ranks=[vector(['2','1']),vector(['3','2'])]
        def ranges(url,query,start,end):
            self.assertEqual((start,end),(START,END))
            self.assertIn('[1ms]',query);self.assertIn('ip_version=~"4|6"',query)
            if 'sum by (asn, direction)' in query:
                self.assertIn('asn=~"^(1|2|3)$"',query)
                return sample_data()[2]
            if 'direction="in"' in query:
                self.assertIn('asn!~"^(2|1)$"',query)
                self.assertNotIn('3',query.split('asn!~')[1])
                return sample_data()[3]['in']
            self.assertIn('asn!~"^(3|2)$"',query)
            return []
        with patch('app.link_usage.query_vm',side_effect=ranks) as instant,patch('app.link_usage.query_series_vm',side_effect=ranges) as query_range:
            series,legend = usage.fetch_link_series(SETTINGS,'a.b',START,END)
            self.assertEqual(instant.call_count,2);self.assertEqual(query_range.call_count,3)
            for direction,call in zip(('in','out'),instant.call_args_list):
                self.assertEqual(call.args[2],f'{END-1}.999')
                self.assertIn('topk(10, sum by (asn)',call.args[1]);self.assertIn('[86400s]',call.args[1])
                self.assertIn(f'direction="{direction}"',call.args[1])
            a=next(entry for entry in legend['in'] if entry['asn']=='2')
            b=next(entry for entry in legend['out'] if entry['asn']=='2')
            self.assertEqual(a['color'],b['color'])
            self.assertEqual([entry['asn'] for entry in legend['in']],['2','1',None])
            self.assertEqual([entry['asn'] for entry in legend['out']],['3','2',None])
            self.assertEqual(next(row for row in series['links'] if row['link_id']=='3')['in'],[None]*1440)

    def test_full_top_ten_fixed_membership_and_deterministic_palette(self):
        ranks = [vector([str(value) for value in range(1,11)]), vector([str(value) for value in range(6,16)])]
        legends = []
        for _ in range(2):
            with patch('app.link_usage.query_vm',side_effect=ranks),patch('app.link_usage.query_series_vm',return_value=[]):
                _,legend=usage.fetch_link_series(SETTINGS,'a.b',START,END)
                legends.append(legend)
        self.assertEqual(legends[0],legends[1])
        self.assertEqual(len(legends[0]['in']),11);self.assertEqual(len(legends[0]['out']),11)
        colors={entry['asn']:entry['color'] for entry in legends[0]['in'] if entry['asn']}
        for entry in legends[0]['out']:
            if entry['asn'] in colors:self.assertEqual(entry['color'],colors[entry['asn']])
        with self.assertRaises(VMError):usage.parse_top(vector([str(value) for value in range(11)]))

    def test_gaps_zeros_direction_stacks_and_dense_svg(self):
        top,colors,matrix,others=sample_data()
        series=usage.make_link_series(START,END,top,matrix,others,colors)
        row=next(row for row in series['links'] if row['link_id']=='1')
        self.assertEqual(row['in'][:3],['0',None,'10'])
        other=next(row for row in series['links'] if row['link_id']=='others')
        self.assertEqual(other['in'][:3],['2',None,'0']);self.assertEqual(other['out'][:3],[None]*3)
        root=ET.fromstring(render_svg(series,height=340,time_intervals=12,y_divisions=5))
        self.assertEqual(root.attrib['viewBox'],'0 0 840 340')
        lines=root.findall('{*}line')
        self.assertEqual(len(lines),25)  # Eleven Y ticks, thirteen X ticks, one zero line.
        self.assertEqual(lines[-1].attrib['y1'],'163')
        texts=[node.text for node in root.findall('{*}text')]
        self.assertIn('Time (UTC)',texts);self.assertIn('Output ↑ / Input ↓',texts)
        self.assertFalse(any(text.startswith('-') for text in texts))
        polygons=root.findall('{*}polygon')
        input_polygons=[p for p in polygons if p.attrib['fill']==colors['1']]
        self.assertEqual(len(input_polygons),2)
        first_x=[float(point.split(',')[0]) for point in input_polygons[0].attrib['points'].split()]
        self.assertLess(max(first_x),106)
        self.assertTrue(all(float(point.split(',')[1])>=163 for point in input_polygons[1].attrib['points'].split()))
        output_polygons=[p for p in polygons if p.attrib['fill']==colors['3']]
        self.assertTrue(all(float(point.split(',')[1])<=163 for point in output_polygons[0].attrib['points'].split()))

    def test_empty_top_and_invalid_vm_data(self):
        with patch('app.link_usage.query_vm',return_value=[]),patch('app.link_usage.query_series_vm',return_value=[]) as ranges:
            series,legend=usage.fetch_link_series(SETTINGS,'a.b',START,END)
            self.assertEqual(ranges.call_count,2)
            self.assertTrue(all('asn' not in call.args[1] for call in ranges.call_args_list))
            self.assertEqual(legend['in'],[{'asn':None,'label':'Others','color':usage.OTHERS_COLOR}])
            root=ET.fromstring(render_svg(series,height=340,time_intervals=12,y_divisions=5))
            self.assertIn('No data',[node.text for node in root.findall('{*}text')]);self.assertEqual(root.findall('{*}line'),[])
        with self.assertRaises(VMError): usage.parse_top(vector(['1','1']))
        with self.assertRaises(VMError): usage.parse_top(vector(['99999999999']))
        top,colors,matrix,others=sample_data()
        with self.assertRaises(VMError):usage.make_link_series(START,END,top,matrix+[matrix[0]],others,colors)
        matrix[0]['values']=[[START+1,'1']]
        with self.assertRaises(VMError):usage.make_link_series(START,END,top,matrix,others,colors)

    def test_bundle_cache_key_atomic_legend_and_ttl(self):
        top,colors,matrix,others=sample_data()
        series=usage.make_link_series(START,END,top,matrix,others,colors)
        ticks=[0];cache=SVGCache(clock=lambda:ticks[0])
        self.isolated.clock=lambda:ticks[0]
        with patch('app.link_usage.cache',cache),patch('app.link_usage.fetch_link_series',return_value=(series,{'in':[{'asn':'1','color':colors['1']}],'out':[]})) as fetch:
            data,etag=usage.get_bundle(SETTINGS,LINK,START,END)
            bundle=json.loads(data)
            self.assertIn(colors['1'],bundle['svg']);self.assertEqual(bundle['legend']['in'][0]['color'],colors['1'])
            self.assertIn('v=2',bundle['svg_url'])
            self.assertEqual(etag,'"'+hashlib.sha256(data).hexdigest()+'"')
            self.assertEqual(usage.get_bundle(SETTINGS,LINK,START,END),(data,etag));self.assertEqual(fetch.call_count,1)
            ticks[0]=1200
            usage.get_bundle(SETTINGS,LINK,START,END);self.assertEqual(fetch.call_count,2)
            usage.get_bundle(SETTINGS,LINK,START+60,END+60);self.assertEqual(fetch.call_count,3)

    def test_two_server_workers_coalescing_and_errors_not_cached(self):
        cache=SVGCache();cache.workers=threading.BoundedSemaphore(2)
        lock=threading.Lock();active=[0];peak=[0];calls=[0];release=threading.Event()
        def factory(settings,link,start,end,tz='UTC',result=None):
            with lock:
                active[0]+=1;peak[0]=max(peak[0],active[0]);calls[0]+=1
            release.wait(2)
            with lock:active[0]-=1
            return b'{}'
        with patch('app.link_usage.cache',cache),patch('app.link_usage.build_bundle',side_effect=factory),patch('app.link_usage.fetch_link_series',return_value=({},{})):
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs=[pool.submit(usage.get_bundle,SETTINGS,Link(name,name,'#000000'),START,END) for name in ('a','a','b','c','d','e')]
                deadline=time.monotonic()+1
                while peak[0]<2 and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(peak[0],2);release.set()
                for job in jobs:job.result()
            self.assertEqual(calls[0],5);self.assertEqual(peak[0],2)
        with patch('app.link_usage.cache',cache),patch('app.link_usage.build_bundle',side_effect=VMError('failed')) as build,patch('app.link_usage.fetch_link_series',return_value=({},{})):
            for _ in range(2):
                with self.assertRaises(VMError):usage.get_bundle(SETTINGS,Link('failed','Failed','#000000'),START,END)
            self.assertEqual(build.call_count,2)

    def test_api_etag_errors_and_proxy_free_transport(self):
        from app.main import app
        async def request(path,query,headers=()):
            result=[]
            async def receive():return {'type':'http.request','body':b'','more_body':False}
            async def send(message):result.append(message)
            await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':path,'raw_path':path.encode(),'query_string':query.encode(),'root_path':'','headers':list(headers),'server':('test',80),'client':('test',1)},receive,send)
            return result[0],b''.join(message.get('body',b'') for message in result)
        bundle=json.dumps({'svg':'<svg/>','legend':{'in':[],'out':[]}}).encode()
        with patch('app.main.usage_bundle',return_value=(bundle,'"bundle"')):
            response,body=asyncio.run(request('/api/link-usage/sparkline.svg','link_id=a&start=120&end=86520'))
            self.assertEqual(response['status'],200);self.assertEqual(body,b'<svg/>')
            headers=dict(response['headers']);self.assertIn(b'image/svg+xml',headers[b'content-type']);self.assertEqual(headers[b'cache-control'],b'public, max-age=1200')
            response,_=asyncio.run(request('/api/link-usage/sparkline.svg','link_id=a&start=120&end=86520',[(b'if-none-match',headers[b'etag'])]))
            self.assertEqual(response['status'],304)
        with patch('app.main.link_usage.validate_link',side_effect=ParameterError('Invalid link')):
            response,_=asyncio.run(request('/api/link-usage/link','link_id=x'))
            self.assertEqual(response['status'],422);self.assertEqual(dict(response['headers'])[b'cache-control'],b'no-store')
        with patch('app.main.link_usage.validate_link',return_value=(LINK,START,END)),patch('app.main.link_usage.get_bundle',side_effect=VMError('source failure',504)):
            response,body=asyncio.run(request('/api/link-usage/link','link_id=a&start=120&end=86520'))
            self.assertEqual(response['status'],504);self.assertEqual(dict(response['headers'])[b'cache-control'],b'no-store');self.assertNotIn(b'source failure',body)
