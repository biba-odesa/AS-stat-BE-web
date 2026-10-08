import io
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from app.config import Settings
from app.top_asn import fetch_top
from app.volumes import query_vm, VMError


class RankingTimeoutTests(unittest.TestCase):
    def test_only_ranking_uses_custom_timeout_on_both_queries(self):
        sample={'metric':{'asn':'64496'},'value':[0,'1']}
        with patch('app.top_asn.query_vm',side_effect=[[sample],[]]) as vm:
            fetch_top('http://localhost:8428',20,now=180000,timeout_seconds=30)
        self.assertEqual(vm.call_count,2)
        self.assertTrue(all(call.kwargs=={'timeout_seconds':30} for call in vm.call_args_list))
        settings=Settings('http://localhost:8428',Path('fixture'))
        self.assertEqual(settings.ranking_client_timeout_ms,74000)
        self.assertEqual(settings.metadata_http_timeout,10)

    def test_vm_deadline_and_http_grace_default_unchanged(self):
        with patch('app.volumes.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'status':'success','data':{'resultType':'vector','result':[]}}).encode()
            for timeout in (5,30):
                query_vm('http://localhost:8428','query','179.999',timeout_seconds=timeout)
                call=opener.return_value.open.call_args
                self.assertEqual(parse_qs(urlsplit(call.args[0]).query)['timeout'],[f'{timeout}s'])
                self.assertEqual(call.kwargs['timeout'],timeout+2)
                self.assertEqual(opener.call_args.args[0].proxies,{})

    def test_vm_http_422_deadline_is_a_gateway_timeout(self):
        error=HTTPError('http://localhost:8428',422,'error',{},io.BytesIO(b'{"error":"timeout exceeded while fetching data"}'))
        with patch('app.volumes.build_opener') as opener:
            opener.return_value.open.side_effect=error
            with self.assertRaises(VMError) as result:query_vm('http://localhost:8428','q','179.999',timeout_seconds=30)
            self.assertEqual(result.exception.status_code,504)
