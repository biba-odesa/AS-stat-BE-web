"""Project-root INI settings; explicit process environment takes precedence."""
import configparser
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / 'as-stat-web.conf'
OPTIONS = {'server': {'host', 'port'}, 'victoriametrics': {'url'},
           'links': {'knownlinks'}, 'proxy': {'http_proxy', 'https_proxy', 'no_proxy'},
           'asn_metadata': {'http_timeout_seconds'}, 'svg': {'cache_ttl_seconds'}, 'web_cache': {'path', 'ttl_seconds', 'max_size_bytes'}}


class ConfigurationError(ValueError):
    pass


def read_config():
    parser = configparser.ConfigParser(interpolation=None)
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open(encoding='utf-8') as source:
                parser.read_file(source)
        if parser.defaults() or any(section not in OPTIONS for section in parser.sections()):
            raise ConfigurationError('Unsupported section in as-stat-web.conf')
        for section in parser.sections():
            if set(parser[section]) - OPTIONS[section]:
                raise ConfigurationError(f'Unsupported option in [{section}]')
    except (configparser.Error, OSError, UnicodeError):
        # Parser diagnostics may include the original line containing credentials.
        raise ConfigurationError('Cannot read as-stat-web.conf; check INI syntax and file permissions') from None
    return parser


@dataclass(frozen=True)
class Settings:
    victoriametrics_url: str
    knownlinks_path: Path
    host: str = '127.0.0.1'
    port: int = 8000
    metadata_http_timeout: float = 10
    proxies: dict = field(default_factory=dict)
    svg_cache_ttl: int = 1200

    web_cache_path: Path = ROOT / 'data/web-cache'
    web_cache_ttl: int = 1200
    web_cache_max_bytes: int = 128 * 1024 * 1024

    @classmethod
    def from_env(cls):
        parser = read_config()
        def value(section, option, environment, default):
            return os.environ.get(environment, parser.get(section, option, fallback=default))
        url = value('victoriametrics', 'url', 'ASSTAT_VM_URL', 'http://127.0.0.1:8428').rstrip('/')
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port != 8428
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError
        except ValueError:
            raise ConfigurationError('VictoriaMetrics URL must be http://127.0.0.1:8428') from None
        path = Path(value('links', 'knownlinks', 'ASSTAT_KNOWNLINKS', '/data/as-stats/conf/knownlinks'))
        if not path.is_absolute():
            path = ROOT / path
        host = value('server', 'host', 'UVICORN_HOST', '127.0.0.1')
        if not host.strip() or any(character.isspace() for character in host):
            raise ConfigurationError('Server host must be a nonempty address')
        try:
            port = int(value('server', 'port', 'UVICORN_PORT', '8000'))
            if not 1 <= port <= 65535: raise ValueError
        except ValueError:
            raise ConfigurationError('Server port must be an integer from 1 to 65535') from None
        try:
            timeout = float(value('asn_metadata', 'http_timeout_seconds', 'ASN_METADATA_HTTP_TIMEOUT_SECONDS', '10'))
            if not math.isfinite(timeout) or not 0 < timeout <= 60: raise ValueError
        except ValueError:
            raise ConfigurationError('ASN metadata HTTP timeout must be positive and at most 60 seconds') from None
        try:
            svg_ttl = int(value('svg', 'cache_ttl_seconds', 'ASSTAT_SVG_CACHE_TTL_SECONDS', '1200'))
            if svg_ttl <= 0: raise ValueError
        except ValueError:
            raise ConfigurationError('SVG cache TTL must be a positive integer in seconds') from None
        cache_path = Path(value('web_cache', 'path', 'ASSTAT_WEB_CACHE_PATH', 'data/web-cache'))
        if not cache_path.is_absolute(): cache_path = ROOT / cache_path
        cache_path = cache_path.resolve()
        if not cache_path.is_relative_to(ROOT):
            raise ConfigurationError('Web cache path must be inside the web project')
        try:
            cache_ttl = int(value('web_cache', 'ttl_seconds', 'ASSTAT_WEB_CACHE_TTL_SECONDS', '1200'))
            cache_size = int(value('web_cache', 'max_size_bytes', 'ASSTAT_WEB_CACHE_MAX_SIZE_BYTES', '134217728'))
            if cache_ttl <= 0 or cache_size <= 0: raise ValueError
        except ValueError:
            raise ConfigurationError('Web cache TTL and size must be positive integers') from None
        environment_proxies = getproxies()
        proxies = {}
        for key in ('http', 'https', 'no'):
            variable = key.upper() + '_PROXY'
            configured = parser.get('proxy', variable.lower(), fallback=None)
            proxy = os.environ.get(variable, environment_proxies.get(key, configured))
            if proxy is not None:
                proxies[key] = proxy
            if key != 'no' and proxy:
                try:
                    parsed = urlsplit(proxy)
                    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.port == 0:
                        raise ValueError
                except ValueError:
                    raise ConfigurationError(f'{variable} must be an HTTP or HTTPS proxy URL') from None
        return cls(url, path.resolve(), host, port, timeout, proxies, svg_ttl, cache_path, cache_ttl, cache_size)
