import hashlib
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from decimal import Decimal, localcontext, ROUND_CEILING
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from xml.etree.ElementTree import Element, SubElement, tostring

from app.diagnostics import cache_event
from app.knownlinks import parse_knownlinks
from app.series import query_series_vm, make_series_response
from app.volumes import ParameterError, validate_parameters, VMError


SVG_VERSION = "5"


def validate_timezone(value):
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 128:
            raise ValueError
        # Some VM timezone databases omit the legacy spelling returned by browsers.
        value = 'Europe/Kyiv' if value == 'Europe/Kiev' else value
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ParameterError('Invalid timezone; use an IANA timezone name') from None


def time_label(timestamp, tz, pattern):
    return datetime.fromtimestamp(timestamp, validate_timezone(tz)).strftime(pattern)


def validate_sparkline(asn, start, end):
    if start is None or end is None:
        raise ParameterError('Start and end are required')
    asn, _, start, end = validate_parameters(asn, 'both', start, end)
    if end - start != 86400:
        raise ParameterError('Sparkline period must be exactly 24 hours')
    return asn, start, end


def stack_bands(series):
    count = len(series['timestamps'])
    bands = []
    maximum = Decimal(0)
    with localcontext() as context:
        context.prec = 100
        for direction in ('in', 'out'):
            base = [Decimal(0)] * count
            for link in series['links']:
                lower, upper = [], []
                for i, raw in enumerate(link[direction]):
                    if raw is None:
                        lower.append(None); upper.append(None)
                        continue
                    value = Decimal(raw)
                    if not value.is_finite() or value < 0:
                        raise ValueError('invalid speed')
                    lower.append(base[i]); base[i] += value; upper.append(base[i])
                    maximum = max(maximum, base[i])
                bands.append((direction, link['color'], lower, upper))
    return bands, maximum


def nice_scale(maximum, target_divisions=3, fixed_divisions=False):
    target = (maximum or Decimal(1)) / target_divisions
    power = Decimal(10) ** target.adjusted()
    step = next(factor * power for factor in map(Decimal, ('1','2','2.5','5','10'))
                if factor * power >= target)
    divisions = int(((maximum or Decimal(1)) / step).to_integral_value(rounding=ROUND_CEILING))
    if fixed_divisions:
        divisions = target_divisions
    return step * divisions, step, divisions


def speed_label(value):
    units = ('bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s')
    unit = 0
    while value >= 1000 and unit < len(units)-1:
        value /= 1000
        unit += 1
    text = format(value, '.3f').rstrip('0').rstrip('.') if value >= Decimal('0.001') or not value else format(value, '.3g')
    return f'{text} {units[unit]}'


def observed_peaks(series):
    peaks, incomplete = {}, {}
    for direction in ('in', 'out'):
        totals = []
        incomplete[direction] = False
        with localcontext() as context:
            context.prec = 100
            for i in range(len(series['timestamps'])):
                values = [link[direction][i] for link in series['links']]
                incomplete[direction] |= any(value is None for value in values)
                available = [Decimal(value) for value in values if value is not None]
                if available:
                    totals.append(sum(available, Decimal(0)))
        peaks[direction] = max(totals) if totals else None
    return peaks, incomplete


def render_svg(series, height=260, time_intervals=6, y_divisions=None, tz='UTC'):
    zone = validate_timezone(tz)
    bands, maximum = stack_bands(series)
    count = len(series['timestamps'])
    root = Element('svg', {'xmlns':'http://www.w3.org/2000/svg','width':'840','height':str(height),
                           'viewBox':f'0 0 840 {height}','role':'img','aria-label':'Output up, Input down'})
    SubElement(root, 'rect', {'width':'840','height':str(height),'fill':'#18212d'})
    def text(x, y, content, anchor='start', size=11):
        SubElement(root, 'text', {'x':str(x),'y':str(y),'text-anchor':anchor,
            'font-family':'sans-serif','font-size':str(size),'fill':'#d5deea'}).text = content
    text(105, 16, 'Output ↑ / Input ↓', size=12)
    has_data = any(value is not None for link in series['links'] for d in ('in','out') for value in link[d])
    if not has_data:
        text(420, height//2, 'No data', 'middle', 16)
        text(420, height-13, f'Time ({zone.key})', 'middle', 12)
        return tostring(root, encoding='utf-8', xml_declaration=True)
    peaks, incomplete = observed_peaks(series)
    labels = []
    for direction, name in (('in','input'), ('out','output')):
        prefix = 'Observed peak' if incomplete[direction] else 'Peak'
        value = 'No data' if peaks[direction] is None else speed_label(peaks[direction])
        labels.append(f'{prefix} {name}: {value}')
    text(105, 32, ' | '.join(labels), size=11)
    limit, step, divisions = nice_scale(maximum, y_divisions or 3, y_divisions is not None)
    left, right, top, bottom = 105, 818, 44, height-58
    zero = (top+bottom)//2
    half = (bottom-top)/2
    for tick in range(-divisions, divisions+1):
        value = step * abs(tick)
        y = zero - tick / divisions * half
        SubElement(root, 'line', {'x1':str(left),'x2':str(right),'y1':str(y),'y2':str(y),
                                 'stroke':'#2b394b','stroke-width':'0.7'})
        text(left-8, y+4, speed_label(value), 'end')
    start = series.get('start', series['timestamps'][0])
    end = series.get('end', series['timestamps'][-1]+60)
    for tick in range(time_intervals+1):
        x = left + tick / time_intervals * (right-left)
        timestamp = start + tick / time_intervals * (end-start)
        SubElement(root, 'line', {'x1':str(x),'x2':str(x),'y1':str(top),'y2':str(bottom),
                                 'stroke':'#2b394b','stroke-width':'0.7'})
        label = datetime.fromtimestamp(timestamp, zone).strftime('%H:%M' if time_intervals > 6 else '%m-%d %H:%M')
        text(x, bottom+18, label, 'start' if tick == 0 else 'end' if tick == time_intervals else 'middle', 10)
    if time_intervals > 6:
        for timestamp, x, anchor in ((start,left,'start'),(end,right,'end')):
            text(x, bottom+36, datetime.fromtimestamp(timestamp, zone).strftime('%Y-%m-%d'), anchor, 10)
    text((left+right)/2, height-13, f'Time ({zone.key})', 'middle', 12)
    for direction, color, lower, upper in bands:
        sign = 1 if direction == 'in' else -1
        def point(index, value):
            x = left + index / count * (right-left)
            y = zero + sign * float(value / limit) * half
            return f'{x:.4f},{y:.4f}'
        i = 0
        while i < count:
            if lower[i] is None:
                i += 1
                continue
            first = i
            while i < count and lower[i] is not None:
                i += 1
            # Each sample covers its entire minute; no polygon crosses a missing minute.
            lower_points, upper_points = [], []
            for j in range(first, i):
                lower_points.extend((point(j, lower[j]), point(j+1, lower[j])))
                upper_points.extend((point(j, upper[j]), point(j+1, upper[j])))
            SubElement(root, 'polygon', {'points':' '.join(lower_points + upper_points[::-1]),
                'fill':color,'fill-opacity':'0.55','stroke':color,'stroke-width':'0.4'})
    SubElement(root, 'line', {'x1':str(left),'x2':str(right),'y1':str(zero),'y2':str(zero),
                             'stroke':'#9aadc4','stroke-width':'1.5','class':'zero-line'})
    return tostring(root, encoding='utf-8', xml_declaration=True)


class SVGCache:
    def __init__(self, ttl=1200, max_entries=128, max_bytes=16*1024*1024,
                 max_item_bytes=2*1024*1024, clock=time.monotonic, name='svg'):
        self.name = name
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self.max_item_bytes, self.clock = max_item_bytes, clock
        self.entries, self.pending = OrderedDict(), {}
        self.size = 0
        self.lock = threading.Lock()
        self.workers = threading.BoundedSemaphore(4)

    def get(self, key, factory, ttl=None):
        with self.lock:
            now = self.clock()
            for expired in [k for k, v in self.entries.items() if v[0] <= now]:
                discarded = self.entries.pop(expired)[1]
                self.size -= len(discarded)
                cache_event(self.name,'EXPIRED',expired,size=len(discarded),reason='ttl')
            if key in self.entries:
                self.entries.move_to_end(key)
                cache_event(self.name,'HIT',key,size=len(self.entries[key][1]))
                return self.entries[key][1:]
            future = self.pending.get(key)
            owner = future is None
            if owner:
                if len(self.pending) >= 32:
                    raise VMError('Too many pending sparkline requests', 503)
                future = self.pending[key] = Future()
            cache_event(self.name,'MISS' if owner else 'WAIT',key)
        if not owner:
            return future.result()
        try:
            with self.workers:
                data = factory()
            etag = '"' + hashlib.sha256(data).hexdigest() + '"'
            with self.lock:
                if len(data) <= min(self.max_item_bytes, self.max_bytes) and self.max_entries > 0:
                    self.entries[key] = (self.clock()+(self.ttl if ttl is None else ttl), data, etag)
                    self.size += len(data)
                    while len(self.entries) > self.max_entries or self.size > self.max_bytes:
                        discarded_key, old = self.entries.popitem(last=False)
                        self.size -= len(old[1])
                        cache_event(self.name,'EVICT',discarded_key,size=len(old[1]),reason='capacity')
                    cache_event(self.name,'STORE',key,size=len(data))
                else:
                    cache_event(self.name,'SKIP',key,size=len(data),reason='item-size-or-disabled')
                future.set_result((data, etag))
            return data, etag
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)


def load_svg(settings, asn, start, end, tz='UTC'):
    links = parse_knownlinks(settings.knownlinks_path)
    query = f'sum_over_time(asstat_traffic_bytes{{asn="{asn}"}}[1ms]) * 8 / 60'
    matrix = query_series_vm(settings.victoriametrics_url, query, start, end)
    series = make_series_response(asn, 'both', start, end, links, matrix)
    return render_svg(series,tz=tz)


cache = SVGCache(name="asn-svg")
