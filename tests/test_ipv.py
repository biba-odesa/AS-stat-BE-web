import asyncio
import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from app import ipv
from app.config import Settings
from app.knownlinks import Link
from app.web_cache import ResultCache
from app.sparkline import validate_timezone
from app.volumes import ParameterError, VMError
from test_url_prefix import request
from app.url_prefix import PrefixMiddleware
from app.main import app

class IPvTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.settings=Settings('http://127.0.0.1:8428',Path('fixture'),web_cache_path=Path(directory.name))
        for target,value in (('query_vm',[{'value':[0,'0']}]),('MAX_CHUNK_POINTS',1000000)):
            mocked=patch.object(ipv,target,return_value=value) if target=='query_vm' else patch.object(ipv,target,value)
            mocked.start();self.addCleanup(mocked.stop)
        for name in ('data_cache','svg_cache'):
            mocked=patch.object(ipv,name,ResultCache('ipv-'+name));mocked.start();self.addCleanup(mocked.stop)

    def matrix(self,mode,start,step):
        label='ip_version' if mode=='compare' else 'link_id'
        identities=('4','6') if mode=='compare' else ('example-a','example-b')
        return [{'metric':{label:i,'direction':d},'values':[[start,'0'],[start+2*step,'8']]} for i in identities for d in ('in','out')]

    def test_all_twelve_windows_rollups_gaps_zero_svg_and_timezone(self):
        for mode in ('4','6','compare'):
            for period,(duration,step,boundary) in ipv.PERIODS.items():
                a,b,s=ipv.parameters(mode,period,now=1800000123)
                self.assertEqual((b-a,s,b%boundary),(duration,step,0))
                with patch.object(ipv,'query_series_vm',return_value=self.matrix(mode,a,step)) as vm:
                    data=ipv.fetch_data(self.settings,mode,a,b,step)
                self.assertEqual(vm.call_args.args[2],a)
                self.assertEqual(vm.call_args.kwargs,{'step':step,'evaluation_end':b-step,'timeout_seconds':30})
                q=vm.call_args.args[1];self.assertIn('[1ms]',q);self.assertIn(f'* 8 / {step}',q)
                self.assertIn('sum by (ip_version, direction)' if mode=='compare' else 'sum by (link_id, direction)',q)
                self.assertNotIn('asn=',q);self.assertNotIn('rate(',q)
                self.assertEqual(ipv.query_vm.call_args.kwargs,{'timeout_seconds':30})
                self.assertEqual(data['rows'][0]['in'][:3],['0',None,'8'])
                with patch.object(ipv,'parse_knownlinks',return_value=[Link('example-a','Example A','#66c2a5'),Link('idle','Idle','#abc123')]):
                    rows=ipv.display_rows(self.settings,data)
                if mode=='compare':
                    image=ipv.compare_svg(data,rows,'in','America/New_York')
                    self.assertIn(b'Time (America/New_York)',image)
                    self.assertNotIn(b'polygon',image)
                    self.assertEqual(len(ET.fromstring(image).findall('{*}polyline')),4)
                else:
                    from app.sparkline import render_svg
                    image=render_svg({**data,'links':rows},tz='Europe/Kyiv')
                    self.assertIn(b'polygon',image)
                    self.assertEqual(next(r for r in rows if r['link_id']=='idle')['in'][0],None)
                ET.fromstring(image)
        self.assertEqual(validate_timezone('Europe/Kiev').key,'Europe/Kyiv')
        from datetime import datetime,timezone
        zone=validate_timezone('America/New_York')
        self.assertNotEqual(datetime(2026,3,8,6,tzinfo=timezone.utc).astimezone(zone).utcoffset(),datetime(2026,3,8,8,tzinfo=timezone.utc).astimezone(zone).utcoffset())

    def test_exact_half_open_boundaries_fixture(self):
        # Sum raw minute bytes, including A and the last minute, excluding B.
        a=3600;step=300
        raw=[(a-60,999),(a,60),(a+240,120),(a+step,240)]
        windows=[sum(v for t,v in raw if begin<=t<begin+step)*8/step for begin in (a,a+step)]
        self.assertEqual(windows,[4.8,6.4])
        matrix=[{'metric':{'link_id':'example','direction':'in'},'values':[[a,str(windows[0])],[a+step,str(windows[1])]]}]
        data=ipv.normalize_matrix('4',a,a+2*step,step,matrix)
        self.assertEqual(data['rows'][0]['in'],['4.8','6.4'])
        with self.assertRaises(VMError):ipv.normalize_matrix('4',a,a+2*step,step,[{'metric':matrix[0]['metric'],'values':[[a+2*step,1]]}])

    def test_persistent_data_and_svg_no_read_extension_and_errors(self):
        a,b,s=ipv.parameters('compare','1w',now=1800000123)
        ticks=[100000];new=lambda name:ResultCache(name,clock=lambda:ticks[0])
        with patch.object(ipv,'data_cache',new('numeric')),patch.object(ipv,'svg_cache',new('image')),patch.object(ipv,'query_series_vm',return_value=self.matrix('compare',a,s)) as vm:
            first=ipv.get_svg(self.settings,'compare','1w',a,b,s,'UTC','in')
            ticks[0]+=100
            with patch.object(ipv,'data_cache',new('numeric')),patch.object(ipv,'svg_cache',new('image')):
                self.assertEqual(ipv.get_svg(self.settings,'compare','1w',a,b,s,'UTC','in'),first)
                self.assertEqual(vm.call_count,1)
            ticks[0]=103600
            with patch.object(ipv,'data_cache',new('numeric')),patch.object(ipv,'svg_cache',new('image')):
                ipv.get_svg(self.settings,'compare','1w',a,b,s,'UTC','in');self.assertEqual(vm.call_count,2)
        self.assertEqual(ipv.cache_settings(self.settings,'1y').web_cache_ttl,604800)
        with patch.object(ipv,'query_series_vm',side_effect=VMError('fixture failure')) as vm:
            for _ in range(2):
                with self.assertRaises(VMError):ipv.get_data(self.settings,'4','1d',a,b,60)
            self.assertEqual(vm.call_count,2)

    def test_two_heavy_workers(self):
        import threading,time
        from concurrent.futures import ThreadPoolExecutor
        active=[0];peak=[0];lock=threading.Lock();release=threading.Event()
        def vm(*args,**kwargs):
            with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
            release.wait(2)
            with lock:active[0]-=1
            return []
        with patch.object(ipv,'query_series_vm',side_effect=vm),ThreadPoolExecutor(4) as pool:
            jobs=[pool.submit(ipv.fetch_data,self.settings,'compare',3600,7200,60) for _ in range(4)]
            deadline=time.monotonic()+1
            while peak[0]<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(peak[0],2);release.set()
            for job in jobs:job.result()
        self.assertEqual(peak[0],2)

    def test_empty_and_configured_ttls(self):
        data=ipv.normalize_matrix('compare',0,120,60,[])
        image=ipv.compare_svg(data,ipv.display_rows(self.settings,data),'out','UTC')
        self.assertIn(b'No data',image);self.assertNotIn(b'polyline',image)
        custom=replace(self.settings,ipv_short_ttl=123,ipv_long_ttl=456)
        self.assertEqual(ipv.cache_settings(custom,'1d').web_cache_ttl,123)
        self.assertEqual(ipv.cache_settings(custom,'1m').web_cache_ttl,456)

    def test_api_prefix_svg_headers_invalid_and_no_store(self):
        application=PrefixMiddleware(app,prefix='/asstat2')
        settings=self.settings
        a,b,s=ipv.parameters('compare','1w')
        with patch('app.main.Settings.from_env',return_value=settings),patch.object(ipv,'query_series_vm',return_value=self.matrix('compare',a,s)) as vm:
            status,_,body=asyncio.run(request(application,'/asstat2/api/ipv',b'mode=compare&period=1w&tz=Europe%2FKiev'))
            self.assertEqual(status,200);response=json.loads(body)
            self.assertEqual(len(response['svg_urls']),2)
            url=response['svg_urls'][0];path,qs=url.split('?')
            self.assertTrue(path.startswith('/asstat2/'))
            status,headers,_=asyncio.run(request(application,path,qs.encode()))
            self.assertEqual(status,200);self.assertEqual(headers[b'cache-control'],b'public, max-age=3600')
            status,again,_=asyncio.run(request(application,path,qs.encode(),[(b'if-none-match',headers[b'etag'])]))
            self.assertEqual(status,304);self.assertEqual(again[b'etag'],headers[b'etag']);self.assertEqual(vm.call_count,1)
            for qs in (b'mode=invalid',b'period=9d',b'tz=Invalid/Timezone',b'start=1&end=2'):
                status,headers,_=asyncio.run(request(application,'/asstat2/api/ipv/traffic.svg',qs))
                self.assertEqual(status,422);self.assertEqual(headers[b'cache-control'],b'no-store')
        with self.assertRaises(ParameterError):ipv.parameters('compare','1m',start=0,end=123)

    def test_discovery_and_bounded_chunks_keep_full_axis(self):
        a=3600; b=a+600; step=60
        def matrix(url,q,begin,end,**kwargs):
            return [{'metric':{'link_id':'example','direction':'in'},
                     'values':[[begin,'0']]}]
        with patch.object(ipv,'MAX_CHUNK_POINTS',2),patch.object(ipv,'query_vm',return_value=[{'value':[b-.001,str(a+125)]}]) as discovery,patch.object(ipv,'query_series_vm',side_effect=matrix) as vm:
            data=ipv.fetch_data(self.settings,'4',a,b,step)
        self.assertEqual(vm.call_count,4)
        self.assertEqual(vm.call_args_list[0].args[2],a+120)
        self.assertEqual(data['rows'][0]['in'][:4],[None,None,'0',None])
        self.assertEqual(data['timestamps'],list(range(a,b,step)))
        self.assertIn('tfirst_over_time',discovery.call_args.args[1])
        self.assertEqual(discovery.call_args.args[2],f'{b-1}.999')
