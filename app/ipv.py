"""All-ASN IPv traffic rollups with explicit nonoverlapping UTC windows."""
import hashlib
import json
import time
import threading
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from datetime import datetime
from urllib.parse import urlencode
from xml.etree.ElementTree import Element, SubElement, tostring
from app.knownlinks import parse_knownlinks
from app.series import query_series_vm
from app.sparkline import render_svg, validate_timezone, speed_label, nice_scale
from app.volumes import ParameterError, VMError, decimal_text, query_vm
from app.web_cache import ResultCache
from app.archive_sources import source_url, METRIC, CONTRACTS

PERIODS = {'1d':(86400,60,3600), '1w':(7*86400,300,3600),
           '1m':(30*86400,1800,86400), '1y':(365*86400,7200,86400)}
CALC_VERSION = '3-archives'
SVG_VERSION = '3-archives'
MAX_CHUNK_POINTS = 60
FAMILY_COLORS = {'4':'#66c2a5', '6':'#fc8d62'}
data_cache = ResultCache('ipv-data')
svg_cache = ResultCache('ipv-svg')
workers = threading.BoundedSemaphore(2)


def client_timeouts_ms(settings):
    # One discovery plus all sequential blocks, then queue/render/transfer grace.
    return {period: ((1 + (duration//step + MAX_CHUNK_POINTS-1)//MAX_CHUNK_POINTS)
                     * (settings.ipv_vm_timeout+2) + 60) * 1000
            for period,(duration,step,_) in PERIODS.items()}


def parameters(mode='compare', period='1w', start=None, end=None, now=None):
    if mode not in ('4','6','compare') or period not in PERIODS:
        raise ParameterError('Invalid IPv mode or period')
    duration, step, boundary = PERIODS[period]
    current = int(time.time() if now is None else now)
    if start is None and end is None:
        end = current // boundary * boundary
        start = end - duration
    else:
        try:
            if start is None or end is None or not str(start).isdigit() or not str(end).isdigit():raise ValueError
            start, end = int(start), int(end)
            if end-start != duration or end%boundary or end > current//boundary*boundary or start%step:raise ValueError
        except (ValueError, TypeError):
            raise ParameterError('Invalid IPv period boundaries') from None
    return start, end, step


def cache_settings(settings, period):
    return replace(settings, web_cache_ttl=settings.ipv_short_ttl if period in ('1d','1w') else settings.ipv_long_ttl)


def query(mode, step):
    grouping = 'ip_version, direction' if mode == 'compare' else 'link_id, direction'
    family = 'ip_version=~"4|6"' if mode == 'compare' else f'ip_version="{mode}"'
    return f'sum by ({grouping}) (sum_over_time(asstat_traffic_bytes{{{family},direction=~"in|out"}}[1ms]) * 8 / {step})'


def normalize_matrix(mode, start, end, step, matrix):
    stamps = list(range(start,end,step))
    rows = {family:{'id':family,'in':[None]*len(stamps),'out':[None]*len(stamps)} for family in ('4','6')} if mode=='compare' else {}
    seen=set()
    try:
        for sample in matrix:
            identity=sample['metric']['ip_version' if mode=='compare' else 'link_id']
            direction=sample['metric']['direction']
            if not isinstance(identity,str) or not identity or direction not in ('in','out') or (mode=='compare' and identity not in ('4','6')):raise ValueError
            row=rows.setdefault(identity,{'id':identity,'in':[None]*len(stamps),'out':[None]*len(stamps)})
            for stamp, raw in sample['values']:
                # Raw samples are stamped at the beginning of [t,t+step).
                t=Decimal(str(stamp))
                value=Decimal(raw)
                if not t.is_finite() or t!=t.to_integral_value() or not start<=t<end or (int(t)-start)%step or not value.is_finite() or value<0:raise ValueError
                key=(identity,direction,int(t))
                if key in seen:raise ValueError
                seen.add(key)
                row[direction][(int(t)-start)//step]=decimal_text(value)
    except (KeyError, TypeError, ValueError, InvalidOperation, IndexError):
        raise VMError('Invalid IPv interval response from VictoriaMetrics') from None
    return {'mode':mode,'start':start,'end':end,'step':step,'timestamps':stamps,'rows':list(rows.values())}


def fetch_data(settings, mode, start, end, step, period=None):
    period = period or next(p for p,c in CONTRACTS.items() if c[1] == step)
    url = source_url(settings,period)
    # Discover the first actual raw sample; retention is not a hard cutoff.
    family = 'ip_version=~"4|6"' if mode == 'compare' else f'ip_version="{mode}"'
    discovery = (f'min(tfirst_over_time(asstat_traffic_bytes{{{family},'
                 f'direction=~"in|out"}}[{end-start}s]))')
    with workers:
        first = query_vm(url, discovery, f'{end-1}.999', timeout_seconds=settings.ipv_vm_timeout)
        if not first:
            return normalize_matrix(mode, start, end, step, [])
        try:
            earliest = Decimal(first[0]['value'][1])
            if not earliest.is_finite(): raise ValueError
            begin = max(start, start + int((earliest-start)//step)*step)
        except (KeyError, IndexError, InvalidOperation, ValueError, TypeError):
            raise VMError('Invalid available-history response from VictoriaMetrics') from None
        # Bound the intermediate per-ASN rollup matrix before VM aggregation.
        merged = {}
        for chunk_start in range(begin, end, step*MAX_CHUNK_POINTS):
            chunk_end = min(end, chunk_start+step*MAX_CHUNK_POINTS)
            matrix = query_series_vm(url, query(mode, step),
                chunk_start, chunk_end, step=step, evaluation_end=chunk_end-step, timeout_seconds=settings.ipv_vm_timeout)
            for series in matrix:
                key = tuple(sorted(series['metric'].items()))
                item = merged.setdefault(key, {'metric':series['metric'], 'values':[]})
                item['values'].extend(series['values'])
    return normalize_matrix(mode, start, end, step, list(merged.values()))


def get_data(settings, mode, period, start, end, step):
    return data_cache.get(cache_settings(settings,period),(CALC_VERSION,source_url(settings,period),METRIC,step,period,mode,start,end),
        lambda:fetch_data(settings,mode,start,end,step,period),with_expiry=True)


def display_rows(settings, data):
    if data['mode']=='compare':
        return [{'link_id':r['id'],'name':'IPv'+r['id'],'color':FAMILY_COLORS[r['id']],
                 'in':r['in'],'out':r['out']} for r in data['rows']]
    count=len(data['timestamps'])
    known={link.link_id:{'link_id':link.link_id,'name':link.name,'color':link.color,
                        'in':[None]*count,'out':[None]*count} for link in parse_knownlinks(settings.knownlinks_path)}
    for row in data['rows']:
        item=known.setdefault(row['id'],{'link_id':row['id'],'name':'Unknown link','color':'#8491a3'})
        item.update({'in':row['in'],'out':row['out']})
    return list(known.values())


def compare_svg(data, rows, direction, tz):
    zone=validate_timezone(tz)
    width,height=1000,340
    left,right,top,bottom=110,970,38,275
    root=Element('svg',{'xmlns':'http://www.w3.org/2000/svg','width':str(width),'height':str(height),'viewBox':f'0 0 {width} {height}','role':'img'})
    SubElement(root,'rect',{'width':str(width),'height':str(height),'fill':'#18212d'})
    def text(x,y,value,anchor='start'):
        SubElement(root,'text',{'x':str(x),'y':str(y),'fill':'#d5deea','font-family':'sans-serif','font-size':'11','text-anchor':anchor}).text=value
    text(left,20,'Input' if direction=='in' else 'Output')
    values=[Decimal(v) for row in rows for v in row[direction] if v is not None]
    if not values:
        text(500,160,'No data','middle');text(500,325,f'Time ({zone.key})','middle')
        return tostring(root,encoding='utf-8',xml_declaration=True)
    limit,spacing,divisions=nice_scale(max(values),4)
    for i in range(divisions+1):
        y=bottom-i/divisions*(bottom-top)
        SubElement(root,'line',{'x1':str(left),'x2':str(right),'y1':str(y),'y2':str(y),'stroke':'#9aadc4' if i==0 else '#2b394b','stroke-width':'1.5' if i==0 else '.7'})
        text(left-8,y+4,speed_label(spacing*i),'end')
    for i in range(7):
        x=left+i/6*(right-left);t=data['start']+i/6*(data['end']-data['start'])
        SubElement(root,'line',{'x1':str(x),'x2':str(x),'y1':str(top),'y2':str(bottom),'stroke':'#2b394b','stroke-width':'.7'})
        text(x,298,datetime.fromtimestamp(t,zone).strftime('%m-%d %H:%M'),'start' if i==0 else 'end' if i==6 else 'middle')
    text(540,325,f'Time ({zone.key})','middle')
    for row in rows:
        points=[]
        def flush():
            if points:
                # Single observed intervals are visible, without bridging a gap.
                SubElement(root,'polyline',{'points':' '.join(points),'fill':'none','stroke':row['color'],'stroke-width':'2'})
                points.clear()
        for i,v in enumerate(row[direction]):
            if v is None:flush();continue
            x=left+i/len(data['timestamps'])*(right-left)
            end_x=left+(i+1)/len(data['timestamps'])*(right-left)
            y=bottom-float(Decimal(v)/limit)*(bottom-top)
            points.extend((f'{x:.4f},{y:.4f}',f'{end_x:.4f},{y:.4f}'))
        flush()
    return tostring(root,encoding='utf-8',xml_declaration=True)


def get_svg(settings, mode, period, start, end, step, tz, direction):
    data,expires=get_data(settings,mode,period,start,end,step)
    rows=display_rows(settings,data)
    colors=hashlib.sha256(json.dumps([(r['link_id'],r['name'],r['color']) for r in rows]).encode()).hexdigest()
    key=(SVG_VERSION,CALC_VERSION,source_url(settings,period),METRIC,period,mode,start,end,step,tz,direction,colors,expires)
    def render():
        with workers:
            if mode=='compare':image=compare_svg(data,rows,direction,tz)
            else:image=render_svg({**data,'links':rows},height=340,tz=tz)
        return {'svg':image.decode()}
    result=svg_cache.get(cache_settings(settings,period),key,render,expiry_limit=expires)
    return result['svg'].encode()
