"""Verified archive contracts and server-controlled source selection."""
import re
import time
from urllib.parse import urlsplit
from app.volumes import ParameterError, validate_parameters

METRIC = 'asstat_traffic_bytes'
CONTRACTS = {'1d': (86400,60,8428), '1w': (604800,300,8429),
             '1m': (2592000,1800,8430), '1y': (31536000,7200,8431)}


def endpoint_url(endpoint):
    from app.config import ConfigurationError
    try:
        if not isinstance(endpoint,str) or not re.fullmatch(r'(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):[0-9]+',endpoint): raise ValueError
        parsed=urlsplit('http://'+endpoint)
        if not parsed.hostname or not 1 <= parsed.port <= 65535 or '..' in parsed.hostname: raise ValueError
    except ValueError:
        raise ConfigurationError('VictoriaMetrics endpoint must be host:port without credentials or URL components') from None
    return 'http://'+endpoint


def source_url(settings,period):
    if period not in CONTRACTS: raise ParameterError('Period must be 1d, 1w, 1m or 1y')
    return settings.vm_endpoints.get(period, settings.victoriametrics_url if period=='1d' else f'http://127.0.0.1:{CONTRACTS[period][2]}')


def view_parameters(asn, family, period, start=None, end=None, now=None):
    # Reuse ASN/family validation independently of the legacy seven-day window.
    asn,family,_,_=validate_parameters(asn,family,None,None,now)
    if period not in CONTRACTS: raise ParameterError('Period must be 1d, 1w, 1m or 1y')
    duration,step,_=CONTRACTS[period]
    current=int(time.time() if now is None else now)//step*step
    if start is None and end is None: return asn,family,current-duration,current,step
    try:
        if start is None or end is None or not re.fullmatch(r'[0-9]{1,12}',str(start)) or not re.fullmatch(r'[0-9]{1,12}',str(end)): raise ValueError
        start,end=int(start),int(end)
        if start%step or end%step or end-start!=duration or end>current: raise ValueError
    except (TypeError,ValueError):
        raise ParameterError('Archive boundaries must span the selected period and align to completed archive intervals') from None
    return asn,family,start,end,step
