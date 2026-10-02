import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ROOT, ConfigurationError, Settings


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'as-stat-web.conf'
        self.configuration = patch('app.config.CONFIG_PATH', self.path)
        self.configuration.start(); self.addCleanup(self.configuration.stop)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start(); self.addCleanup(self.environment.stop)

    def test_defaults_and_conf(self):
        settings = Settings.from_env()
        self.assertEqual((settings.host, settings.port, settings.metadata_http_timeout), ('127.0.0.1', 8000, 10))
        self.assertEqual(settings.proxies, {})
        self.path.write_text('[server]\nhost=192.0.2.10\nport=8080\n[links]\nknownlinks=data/links\n[asn_metadata]\nhttp_timeout_seconds=9\n[proxy]\nHTTPS_PROXY=http://user:p%25word@proxy.example:3128\nNO_PROXY=stat.ripe.net\n')
        settings = Settings.from_env()
        self.assertEqual((settings.host, settings.port, settings.metadata_http_timeout), ('192.0.2.10', 8080, 9))
        self.assertEqual(settings.knownlinks_path, ROOT / 'data/links')
        self.assertEqual(settings.proxies['https'], 'http://user:p%25word@proxy.example:3128')
        from app.asn_metadata import ripe_lookup
        with patch('app.asn_metadata.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"status":"ok","data":{"resource":"AS1","holder":"Name"}}'
            ripe_lookup('1')
            self.assertEqual(opener.call_args.args[0].proxies, {})
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 9)

    def test_environment_priority_and_cwd(self):
        self.path.write_text('[server]\nhost=127.0.0.2\nport=8080\n[proxy]\nHTTPS_PROXY=http://conf:3128\n[links]\nknownlinks=conf-links\n[asn_metadata]\nhttp_timeout_seconds=9\n')
        with patch.dict(os.environ, {'UVICORN_HOST':'192.0.2.10','UVICORN_PORT':'8001', 'ASSTAT_KNOWNLINKS':'env-links','HTTPS_PROXY':'http://env:3128','ASN_METADATA_HTTP_TIMEOUT_SECONDS':'8'}):
            old = Path.cwd()
            try:
                os.chdir(self.directory.name)
                settings = Settings.from_env()
            finally:
                os.chdir(old)
        self.assertEqual((settings.host,settings.port,settings.metadata_http_timeout), ('192.0.2.10',8001,8))
        self.assertEqual(settings.knownlinks_path,ROOT/'env-links')
        self.assertEqual(settings.proxies['https'],'http://env:3128')
        with patch.dict(os.environ,{'HTTPS_PROXY':''}):
            self.assertEqual(Settings.from_env().proxies['https'],'')

    def test_safe_errors(self):
        for content in ('[proxy]\nHTTPS_PROXY=http://user:SECRET@host:bad\n',
                        '[server]\nport=SECRET\n', '[proxy]\nSECRET malformed\n',
                        '[asn_metadata]\nhttp_timeout_seconds=nan\n', '[other]\nkey=SECRET\n'):
            self.path.write_text(content)
            with self.assertRaises(ConfigurationError) as result:
                Settings.from_env()
            self.assertNotIn('SECRET',str(result.exception))
            self.assertNotIn('user:',str(result.exception))

    def test_web_cache_options(self):
        self.path.write_text('[web_cache]\npath=data/cache-test\nttl_seconds=1200\nmax_size_bytes=12345\n')
        settings=Settings.from_env()
        self.assertEqual(settings.web_cache_path,ROOT/'data/cache-test')
        self.assertEqual((settings.web_cache_ttl,settings.web_cache_max_bytes),(1200,12345))
        with patch.dict(os.environ,{'ASSTAT_WEB_CACHE_TTL_SECONDS':'900'}):
            self.assertEqual(Settings.from_env().web_cache_ttl,900)
        self.path.write_text('[web_cache]\npath=/tmp/outside-project\n')
        with self.assertRaises(ConfigurationError):Settings.from_env()
        self.path.write_text('[web_cache]\nttl_seconds=0\n')
        with self.assertRaises(ConfigurationError):Settings.from_env()

    def test_launcher(self):
        self.path.write_text('[server]\nhost=192.0.2.10\nport=8010\n')
        from app.__main__ import main
        with patch('app.__main__.uvicorn.run') as run:
            self.assertEqual(main(),0)
            run.assert_called_once_with('app.main:app',host='192.0.2.10',port=8010)
        self.path.write_text('[server]\nport=bad\n')
        with patch('app.__main__.uvicorn.run') as run, patch('sys.stderr') as stderr:
            self.assertEqual(main(),2)
            run.assert_not_called()
            self.assertIn('Configuration error',stderr.write.call_args_list[0].args[0])
