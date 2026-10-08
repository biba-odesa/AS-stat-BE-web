import tempfile
import time
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app.archive_sources import CONTRACTS, endpoint_url, source_url, view_parameters
from app.config import ConfigurationError, Settings
from app.knownlinks import Link
from app import ipv
from app.main import asn_series, asn_volumes
from app.web_cache import ResultCache
from app.volumes import ParameterError, VMError


class ArchiveSourcesTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.settings=Settings('http://127.0.0.1:8428',Path('fixture'),web_cache_path=Path(self.directory.name))
        self.links=[Link('example','Example','#66c2a5'),Link('idle','Idle','#abcdef')]

    def test_endpoint_validation_and_period_alignment(self):
        self.assertEqual(endpoint_url('localhost:8429'),'http://localhost:8429')
        self.assertEqual(endpoint_url('[::1]:8430'),'http://[::1]:8430')
        for value in ('http://localhost:8429','host:0','host:65536','user:password@host:8429','host:8429/path','host:8429?x=1'):
            with self.assertRaises(ConfigurationError):endpoint_url(value)
        for period,(duration,step,port) in CONTRACTS.items():
            _,_,a,b,s=view_parameters('00064496','both',period,now=1800000123)
            self.assertEqual((b-a,s,b%step),(duration,step,0))
            self.assertEqual(source_url(self.settings,period),f'http://127.0.0.1:{port}')
            self.assertEqual(view_parameters('64496','both',period,str(a),str(b),now=1800000123)[2:4],(a,b))
            with self.assertRaises(ParameterError):view_parameters('64496','both',period,str(a+60),str(b),now=1800000123)
            with self.assertRaises(ParameterError):view_parameters('64496','both',period,str(a+step),str(b+step),now=b)
        with self.assertRaises(ParameterError):view_parameters('64496','both','unknown')

    def test_view_series_and_independent_instant_volumes_all_sources(self):
        for period,(duration,step,port) in CONTRACTS.items():
            _,_,a,b,_=view_parameters('64496','both',period)
            matrix=[{'metric':{'link_id':'example','direction':'in','ip_version':'4'},'values':[[a,'0'],[a+2*step,str(Decimal(1200)*8/step)]]}]
            with patch('app.main.Settings.from_env',return_value=self.settings),patch('app.main.parse_knownlinks',return_value=self.links),patch('app.main.query_series_vm',return_value=matrix) as vm:
                response=asn_series('64496','both',str(a),str(b),period)
                self.assertEqual(vm.call_args.args[0],f'http://127.0.0.1:{port}')
                self.assertIn(f'[1ms]) * 8 / {step}',vm.call_args.args[1])
                self.assertEqual(vm.call_args.kwargs,{'step':step,'evaluation_end':b-step})
                self.assertEqual(response['step'],step)
                self.assertEqual(response['links'][0]['in'][:2],['0',None])
                self.assertEqual(response['links'][0]['out'][0],None)
            vector=[{'metric':{'link_id':'example','direction':'in'},'value':[b-.001,'0']}]
            with patch('app.main.Settings.from_env',return_value=self.settings),patch('app.main.parse_knownlinks',return_value=self.links),patch('app.main.query_vm',return_value=vector) as vm:
                response=asn_volumes('64496','both',str(a),str(b),period)
                self.assertEqual(vm.call_args.args[0],f'http://127.0.0.1:{port}')
                self.assertIn(f'[{duration}s]',vm.call_args.args[1])
                self.assertEqual(vm.call_args.args[2],f'{b-1}.999')
                self.assertEqual(response['totals'],{'in':'0','out':None})

    def test_source_failure_has_period_and_never_falls_back(self):
        with patch('app.main.Settings.from_env',return_value=self.settings),patch('app.main.parse_knownlinks',return_value=self.links),patch('app.main.query_series_vm',side_effect=VMError('fixture failure')) as vm:
            with self.assertRaises(HTTPException) as error:asn_series('64496',period='1m')
            self.assertIn('period 1m',error.exception.detail)
            self.assertEqual(vm.call_count,1)
            self.assertEqual(vm.call_args.args[0],'http://127.0.0.1:8430')

    def test_numeric_cache_separates_sources_and_reuses_success(self):
        a,b,step=ipv.parameters('compare','1w')
        data=ipv.normalize_matrix('compare',a,b,step,[])
        other=replace(self.settings,vm_endpoints={'1w':'http://localhost:8439'})
        with patch.object(ipv,'data_cache',ResultCache('fixture')),patch.object(ipv,'fetch_data',return_value=data) as compute:
            ipv.get_data(self.settings,'compare','1w',a,b,step)
            ipv.get_data(self.settings,'compare','1w',a,b,step)
            ipv.get_data(other,'compare','1w',a,b,step)
            self.assertEqual(compute.call_count,2)
            self.assertEqual(compute.call_args.args[-1],'1w')
