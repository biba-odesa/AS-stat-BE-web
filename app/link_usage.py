"""Fixed-period per-direction ASN rankings and bounded SVG/legend bundles."""
import json
import re
import threading
import time
from dataclasses import asdict
from concurrent.futures import Future
from urllib.parse import urlencode

from app.periods import ranking_period
from app.web_cache import link_cache
from app.diagnostics import cache_event
from app.knownlinks import Link, parse_knownlinks
from app.series import query_series_vm, make_series_response
from app.sparkline import SVGCache, render_svg, validate_sparkline, validate_timezone
from app.top_asn import parse_asn, parse_value
from app.volumes import ParameterError, VMError, query_vm

VERSION = '2'
PALETTE = ('#66c2a5', '#fc8d62', '#8da0cb', '#e78ac3', '#a6d854',
           '#ffd92f', '#e5c494', '#80b1d3', '#fb8072', '#b3de69',
           '#fccde5', '#bc80bd', '#ccebc5', '#ffed6f', '#a6cee3',
           '#b2df8a', '#fdbf6f', '#cab2d6', '#b3b3e6', '#d9b38c')
OTHERS_COLOR = '#8491a3'
cache = SVGCache(name='link-svg-bundle', max_entries=128, max_bytes=16*1024*1024, max_item_bytes=4*1024*1024)
# All ranking, range and rendering work is inside this cache's worker semaphore.
cache.workers = threading.BoundedSemaphore(2)


def manifest(settings, now=None):
    start, end = ranking_period(now)
    return {'start':start, 'end':end,
            'links':[asdict(link) for link in parse_knownlinks(settings.knownlinks_path)]}


def validate_link(settings, link_id, start, end):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', link_id):
        raise ParameterError('Invalid link')
    _, start, end = validate_sparkline('0', start, end)
    link = next((link for link in parse_knownlinks(settings.knownlinks_path) if link.link_id == link_id), None)
    if link is None:
        raise ParameterError('Link is not configured')
    return link, start, end


def regex_asns(asns):
    return '^(' + '|'.join(re.escape(parse_asn(asn)) for asn in asns) + ')$'


def selector(link_id, direction=None, asns=None, exclude=False):
    labels = [f'link_id={json.dumps(link_id)}', 'ip_version=~"4|6"']
    labels.append(f'direction={json.dumps(direction)}' if direction else 'direction=~"in|out"')
    if asns:
        labels.append('asn' + ('!~' if exclude else '=~') + json.dumps(regex_asns(asns)))
    return 'asstat_traffic_bytes{' + ','.join(labels) + '}'


def top_query(link_id, direction):
    return f'topk(10, sum by (asn) (sum_over_time({selector(link_id,direction)}[86400s])))'


def minute_query(link_id, asns, direction=None, others=False):
    if not others and not asns:
        raise ValueError('Empty top series query')
    grouping = 'direction' if others else 'asn, direction'
    return (f'sum by ({grouping}) (sum_over_time('
            f'{selector(link_id,direction,asns,exclude=others)}[1ms]) * 8 / 60)')


def parse_top(vector):
    try:
        entries = {}
        for sample in vector:
            asn = parse_asn(sample['metric']['asn'])
            if asn in entries or len(entries) >= 10:
                raise ValueError('Duplicate or excessive ranking')
            entries[asn] = parse_value(sample)
        return sorted(entries, key=lambda asn:(-entries[asn],int(asn)))
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
        raise VMError('Invalid link ranking response') from exc


def make_link_series(start, end, top, matrix, others, colors):
    union = sorted(set(top['in']) | set(top['out']), key=int)
    rows = [Link(asn, f'AS{asn}', colors[asn]) for asn in union]
    rows.append(Link('others', 'Others', OTHERS_COLOR))
    normalized = []
    seen = set()
    try:
        for sample in matrix:
            labels = sample['metric']
            asn, direction = parse_asn(labels['asn']), labels['direction']
            if asn not in union or direction not in ('in','out') or (asn,direction) in seen:
                raise ValueError('Unexpected top series')
            seen.add((asn,direction))
            # Families were already summed after the 1ms rollup in VictoriaMetrics.
            normalized.append({'metric':{'link_id':asn,'direction':direction,'ip_version':'4'},
                               'values':sample['values']})
        for direction in ('in','out'):
            if len(others[direction]) > 1:
                raise ValueError('Duplicate Others series')
            for sample in others[direction]:
                if sample['metric'].get('direction') != direction:
                    raise ValueError('Unexpected Others direction')
                normalized.append({'metric':{'link_id':'others','direction':direction,'ip_version':'4'},
                                   'values':sample['values']})
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError('Invalid link minute response') from exc
    result = make_series_response('0','both',start,end,rows,normalized)
    for row in result['links']:
        if row['link_id'] != 'others':
            for direction in ('in','out'):
                # The union is fetched once, but each direction keeps only its own top.
                if row['link_id'] not in top[direction]:
                    row[direction] = [None] * len(result['timestamps'])
    return result


def fetch_link_series(settings, link_id, start, end):
    instant = f'{end-1}.999'
    top = {direction:parse_top(query_vm(settings.victoriametrics_url,
                                      top_query(link_id,direction),instant))
           for direction in ('in','out')}
    union = sorted(set(top['in']) | set(top['out']), key=int)
    colors = {asn:PALETTE[index] for index,asn in enumerate(union)}
    matrix = query_series_vm(settings.victoriametrics_url,minute_query(link_id,union),start,end) if union else []
    others = {direction:query_series_vm(settings.victoriametrics_url,
                                       minute_query(link_id,top[direction],direction,others=True),start,end)
              for direction in ('in','out')}
    series = make_link_series(start,end,top,matrix,others,colors)
    legend = {direction:[{'asn':asn,'label':f'AS{asn}','color':colors[asn]} for asn in top[direction]]
              + [{'asn':None,'label':'Others','color':OTHERS_COLOR}] for direction in ('in','out')}
    return series, legend


def build_bundle(settings, link, start, end, tz='UTC', result=None):
    tz = validate_timezone(tz).key
    result = result or link_cache.get(settings, ('1', link.link_id, start, end),
                            lambda: dict(zip(('series', 'legend'), fetch_link_series(settings,link.link_id,start,end))))
    series, legend = result['series'], result['legend']
    svg = render_svg(series,height=340,time_intervals=12,y_divisions=5,tz=tz).decode('utf-8')
    has_data = any(value is not None for row in series['links'] for direction in ('in','out') for value in row[direction])
    image_url = '/api/link-usage/sparkline.svg?' + urlencode({'link_id':link.link_id,'start':start,'end':end,'tz':tz,'v':VERSION})
    return json.dumps({'start':start,'end':end,'legend':legend,'has_data':has_data,
                       'svg':svg,'svg_url':image_url},separators=(',',':')).encode('utf-8')


pipeline_workers = threading.BoundedSemaphore(2)
pipeline_lock = threading.Lock()
pipeline_pending = {}

def get_bundle(settings, link, start, end, tz='UTC'):
    tz = validate_timezone(tz).key
    # Settings contain a proxy dict, so use only source/cache identity here.
    key = (str(settings.web_cache_path), settings.web_cache_ttl, settings.svg_cache_ttl,
           settings.victoriametrics_url, str(settings.knownlinks_path), link.link_id, start, end, tz)
    with pipeline_lock:
        future = pipeline_pending.get(key)
        owner = future is None
        if owner:
            if len(pipeline_pending) >= 32:
                raise VMError('Too many pending link requests', 503)
            future = pipeline_pending[key] = Future()
    if not owner:
        cache_event('link-pipeline', 'WAIT', key)
        return future.result()
    try:
        with pipeline_workers:
            result = _get_bundle(settings, link, start, end, tz)
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with pipeline_lock:
            pipeline_pending.pop(key, None)

def _get_bundle(settings, link, start, end, tz='UTC'):
    tz = validate_timezone(tz).key
    result, expires = link_cache.get(settings, ('1', link.link_id, start, end),
        lambda: dict(zip(('series', 'legend'), fetch_link_series(settings,link.link_id,start,end))), with_expiry=True)
    key = (expires,VERSION,settings.victoriametrics_url,str(settings.knownlinks_path),link.link_id,start,end,tz,settings.svg_cache_ttl)
    return cache.get(key,lambda:build_bundle(settings,link,start,end,tz,result),ttl=min(settings.svg_cache_ttl,max(0.001,expires-time.time())))
