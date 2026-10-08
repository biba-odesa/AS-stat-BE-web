import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.asn_metadata import (MetadataStore, MetadataService, RequestGate, blank, utc_stamp,
                              TTL, HOUR, DAY, parse_cymru, parse_ripe, needs_update,
                              ripe_lookup, cymru_lookup, timestamp)
from app.volumes import query_vm
from app.series import query_series_vm


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.directory.name)/'asn-metadata.json'
        self.store = MetadataStore(self.path)
        self.now = [1800000000]
        self.gate = RequestGate(interval=0)
        self.services = []

    def tearDown(self):
        for service in self.services: service.close()
        self.directory.cleanup()

    def service(self,cymru,ripe):
        service=MetadataService(self.store,cymru,ripe,clock=lambda:self.now[0],gate=self.gate)
        self.services.append(service)
        return service

    def test_cymru_and_ripe_parsing(self):
        self.assertEqual(parse_cymru('64498','64498 | UA | ripencc | 1997-03-01 | EXAMPLE-AS'),{'name':'EXAMPLE-AS','country':'UA'})
        for value in ('bad','64499 | UA | ripe | 2020-01-01 | name','64498 | Ukraine | ripe | date | name'):
            with self.assertRaises(ValueError):parse_cymru('64498',value)
        self.assertEqual(parse_ripe('64498',{'status':'ok','data':{'resource':'AS64498','holder':'EXAMPLE'}}),('success',{'name':'EXAMPLE','country':None}))
        self.assertEqual(parse_ripe('64498',{'status':'ok','data':{'resource':'64498','holder':None}}),('absent',None))
        with self.assertRaises(ValueError):parse_ripe('64498',{'status':'ok','data':{'resource':'AS1','holder':'wrong'}})

    def test_fallback_country_null_and_country_age_preserved(self):
        service=self.service(lambda asn:('error',None),lambda asn:('success',{'name':'RIPE holder','country':None}))
        service.refresh('64498')
        row=self.store.read()['64498'];self.assertIsNone(row['country'])
        self.assertEqual(row['name_source'],'ripestat')
        old=utc_stamp(self.now[0]-TTL-1)
        self.store.update('64497',lambda row:{**row,'country':'UA','country_source':'cymru',
             'country_fetched_at':old})
        service.refresh('64497');row=self.store.read()['64497']
        self.assertEqual((row['country'],row['country_fetched_at']),('UA',old))

    def test_ttl_read_does_not_extend_and_fresh_complete_no_queries(self):
        stamp=utc_stamp(self.now[0])
        self.store.update('64498',lambda row:{**row,'name':'Name','country':'UA','name_source':'cymru',
                         'country_source':'cymru','name_fetched_at':stamp,'country_fetched_at':stamp})
        calls=[]; service=self.service(lambda a:calls.append(a),lambda a:calls.append(a))
        service.batch(['64498'],refresh=True)
        self.assertFalse(service.pending);self.assertEqual(calls,[])
        self.now[0]+=TTL-1
        self.assertFalse(needs_update(self.store.read()['64498'],self.now[0]))
        self.now[0]+=1
        self.assertTrue(needs_update(self.store.read()['64498'],self.now[0]))
        self.assertEqual(self.store.read()['64498']['name_fetched_at'],stamp)

    def test_ripe_name_fresh_cymru_retry_hour_only_on_new_access(self):
        calls=[]
        def cymru(asn):calls.append('cymru');return 'error',None
        def ripe(asn):calls.append('ripe');return 'success',{'name':'Holder','country':None}
        service=self.service(cymru,ripe)
        service.refresh('64498');self.assertEqual(calls,['cymru','ripe'])
        service.refresh('64498');self.assertEqual(len(calls),2)
        self.now[0]+=HOUR
        service.batch(['64498'],refresh=False);self.assertFalse(service.pending)
        service.refresh('64498');self.assertEqual(calls,['cymru','ripe','cymru'])
        self.assertEqual(timestamp(self.store.read()['64498']['name_fetched_at']),self.now[0]-HOUR)

    def test_error_and_absence_cooldowns_preserve_old_values(self):
        for outcome,delay in [('error',HOUR),('absent',DAY)]:
            self.store.update('64498',lambda row:blank('64498'))
            service=self.service(lambda a:(outcome,None),lambda a:(outcome,None))
            service.refresh('64498')
            row=self.store.read()['64498'];self.assertEqual(timestamp(row['retry_after']),self.now[0]+delay)
            self.now[0]+=delay-1;self.assertFalse(needs_update(row,self.now[0]))
            self.now[0]+=1;self.assertTrue(needs_update(row,self.now[0]))
        old={**blank('64498'),'name':'Previous','name_source':'cymru','name_fetched_at':utc_stamp(self.now[0]-TTL-1),
             'country':'UA','country_source':'cymru','country_fetched_at':utc_stamp(self.now[0]-TTL-1)}
        self.store.update('64498',lambda row:old)
        service=self.service(lambda a:('error',None),lambda a:('error',None));service.refresh('64498')
        row=self.store.read()['64498'];self.assertEqual((row['name'],row['country']),('Previous','UA'))

    def test_polling_coalescing_and_workers(self):
        started=threading.Event();release=threading.Event();calls=[]
        def cymru(asn):calls.append(asn);started.set();release.wait(2);return 'success',{'name':'Holder','country':'UA'}
        service=self.service(cymru,lambda a:('absent',None));service.start()
        for _ in range(10):service.batch(['64498'],refresh=True)
        self.assertTrue(started.wait(1))
        self.now[0]+=HOUR*2
        for _ in range(10):service.batch(['64498'],refresh=False)
        self.assertEqual(calls,['64498']);release.set();service.queue.join()
        self.assertFalse(service.batch(['64498'],refresh=False)['records'][0]['updating'])
        self.assertEqual(len(service.threads),2)

    def test_atomic_updates_restart_concurrent_store_and_corruption(self):
        stores=[MetadataStore(self.path),MetadataStore(self.path)]
        def save(asn):stores[int(asn)%2].update(asn,lambda row:row)
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(save,map(str,range(20))))
        self.assertEqual(len(MetadataStore(self.path).read()),20)
        self.assertEqual(list(self.path.parent.glob('.asn-metadata-*')),[])
        self.path.write_text('{broken')
        with self.assertLogs('app.asn_metadata',level='ERROR'):self.assertEqual(self.store.read(),{})
        preserved=list(self.path.parent.glob('asn-metadata.json.corrupt-*'))
        self.assertEqual(len(preserved),1);self.assertEqual(preserved[0].read_text(),'{broken')
        self.store.update('64498',lambda row:row)
        self.assertEqual(MetadataStore(self.path).read()['64498']['asn'],'64498')

    def test_proxy_external_and_local_bypass(self):
        proxy='http://proxy.example:3128'
        with patch.dict(os.environ,{'HTTP_PROXY':proxy,'HTTPS_PROXY':proxy,'NO_PROXY':'localhost,127.0.0.1,::1'},clear=True),patch('app.asn_metadata.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'status':'ok','data':{'resource':'AS64498','holder':'Name'}}).encode()
            ripe_lookup('64498')
            self.assertEqual(opener.call_args.args[0].proxies['https'],proxy)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],10)
        for module,call,payload in [('app.volumes',lambda:query_vm('url','q','1'),b'{"status":"success","data":{"resultType":"vector","result":[]}}'),
                                    ('app.series',lambda:query_series_vm('url','q',0,60),b'{"status":"success","data":{"resultType":"matrix","result":[]}}')]:
            with patch(module+'.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=payload
                call();self.assertEqual(opener.call_args.args[0].proxies,{})

    def test_dns_timeout_and_absence(self):
        import dns.resolver
        with patch('app.asn_metadata.dns.resolver.Resolver') as resolver:
            resolver.return_value.resolve.side_effect=dns.resolver.NXDOMAIN()
            self.assertEqual(cymru_lookup('64498'),('absent',None))
            self.assertEqual(resolver.return_value.lifetime,5)
            self.assertEqual(resolver.return_value.resolve.call_args.args,('AS64498.asn.cymru.com','TXT'))
            self.assertFalse(resolver.return_value.resolve.call_args.kwargs['search'])

    def test_rate_gate_and_bounded_queue(self):
        ticks=[0];delays=[]
        def sleep(delay):delays.append(delay);ticks[0]+=delay
        gate=RequestGate(clock=lambda:ticks[0],sleep=sleep)
        gate.wait();gate.wait();gate.wait();self.assertEqual(delays,[1,1])
        service=MetadataService(self.store,queue_size=1)
        result=service.batch(['1','2'],refresh=True)
        self.assertEqual([r['status'] for r in result['records']],['updating','deferred'])
        self.assertEqual(len(service.pending),1)


    def test_api_batch_validation_and_poll_readonly(self):
        import asyncio
        from app.main import app
        async def request(path, body):
            messages=[]
            async def receive():return {'type':'http.request','body':json.dumps(body).encode(),'more_body':False}
            async def send(message):messages.append(message)
            await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST',
                       'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'',
                       'root_path':'','headers':[(b'content-type',b'application/json')],
                       'server':('test',80),'client':('test',1)},receive,send)
            status=next(m['status'] for m in messages if m['type']=='http.response.start')
            return status
        with patch('app.main.metadata_service') as service:
            service.batch.return_value={'records':[]}
            for asns in (['-1'],['4294967296'],[64498],list(map(str,range(301)))):
                self.assertEqual(asyncio.run(request('/api/asn/metadata',{'asns':asns})),422)
            self.assertEqual(asyncio.run(request('/api/asn/metadata',{'asns':['00064498','64498']})),200)
            service.batch.assert_called_with(['64498'],refresh=True)
            self.assertEqual(asyncio.run(request('/api/asn/metadata/status',{'asns':['64498']})),200)
            service.batch.assert_called_with(['64498'],refresh=False)

    def test_at_most_two_external_calls(self):
        active=[0];peak=[0];lock=threading.Lock();release=threading.Event()
        def cymru(asn):
            with lock:
                active[0]+=1;peak[0]=max(peak[0],active[0])
            release.wait(2)
            with lock:active[0]-=1
            return 'success',{'name':'Name','country':'UA'}
        service=self.service(cymru,lambda a:('absent',None));service.start()
        service.batch(['1','2','3','4'],refresh=True)
        import time
        deadline=time.monotonic()+1
        while peak[0]<2 and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(peak[0],2)
        release.set();service.queue.join();self.assertEqual(peak[0],2)
