from test_web_cache import IsolatedCache
import tempfile
import asyncio
import unittest
from unittest.mock import patch
from app.top_asn import validate_limit, fetch_top, ranking_response
from app.volumes import ParameterError
from test_volumes import request


def sample(asn,value,direction=None):
    labels = {'asn':asn}
    if direction is not None: labels['direction']=direction
    return {'metric':labels,'value':[0,value]}


class TopTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        isolated=IsolatedCache(directory.name)
        isolated.name='top-ranking'
        mocked=patch('app.main.top_cache',isolated)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_limit(self):
        for value in ('1','20','300'): self.assertEqual(validate_limit(value),int(value))
        for value in ('0','301','-1','1.0','x',''):
            with self.assertRaises(ParameterError): validate_limit(value)

    def test_ranking_order_missing_and_zero(self):
        top = [sample('20','5'),sample('3','5'),sample('1','0')]
        details = [sample('20','5','out'),sample('3','5','in'),sample('1','0','in')]
        result = ranking_response(20,0,86400,top,details)
        self.assertEqual([r['asn'] for r in result['rows']],['3','20','1'])
        self.assertEqual(result['rows'][2],{'rank':3,'asn':'1','in':'0','out':None,'total':'0'})
        self.assertIsNone(result['rows'][0]['out'])

    def test_two_queries_fixed_window_and_anchored_match(self):
        with patch('app.top_asn.query_vm',side_effect=[[sample('64496','8'),sample('2','6')],
                   [sample('64496','8','in'),sample('2','6','out')]]) as vm:
            result=fetch_top('url',20,now=180061)
            self.assertEqual((result['start'],result['end']),(93600,180000))
            self.assertEqual(vm.call_count,2)
            self.assertEqual(vm.call_args_list[0].args[1], 'topk(20, sum by (asn) (sum_over_time(asstat_traffic_bytes[86400s])))')
            self.assertIn('asn=~"^(64496|2)$"',vm.call_args_list[1].args[1])
            self.assertEqual([c.args[2] for c in vm.call_args_list],['179999.999']*2)
        with patch('app.top_asn.query_vm',return_value=[]) as vm:
            self.assertEqual(fetch_top('url',20,now=180061)['rows'],[])
            self.assertEqual(vm.call_count,1)

    def test_limit_http_422(self):
        import test_volumes
        original = test_volumes.app
        async def top_app(scope,receive,send):
            scope['path']='/api/top-asn';scope['raw_path']=b'/api/top-asn'
            await original(scope,receive,send)
        with patch('test_volumes.app',top_app):
            for limit in ('0','301','1.1','abc'):
                self.assertEqual(asyncio.run(request('limit='+limit))[0],422)
            with patch('app.main.fetch_top',return_value={'rows':[]}) as fetch:
                self.assertEqual(asyncio.run(request('limit=20'))[0],200)
                self.assertEqual(fetch.call_args.args[1],20)
