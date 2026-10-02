
const $ = id => document.getElementById(id);
let rows = [];
let generation = 0;
let controller;
let cancelMetadata;
let seriesData = null;
let knownLinkNames = new Map();
const charts = {};
const selections = new Map();
function userError(message) {
  const error = new Error(message);
  error.userMessage = message;
  return error;
}
function updateAsnLinks(asn) {
  const urls = [
    ['PeeringDB', `https://www.peeringdb.com/asn/${asn}`],
    ['robtex', `https://www.robtex.com/as/as${asn}.html`],
    ['HE', `https://bgp.he.net/AS${asn}`],
    ['RIPEstat', `https://stat.ripe.net/AS${asn}#tabId=at-a-glance`],
    ['CIDR v4', `http://www.cidr-report.org/cgi-bin/as-report?as=AS${asn}&view=2.0`],
    ['CIDR v6', `http://www.cidr-report.org/cgi-bin/as-report?as=AS${asn}&view=2.0&v=6`],
    ['Radar Qrator', `https://radar.qrator.net/as${asn}/`],
    ['BGP.Tools', `https://bgp.tools/as/${asn}`]
  ];
  $('asn-links').replaceChildren();
  for (const [name, url] of urls) {
    const link = document.createElement('a');
    link.textContent = `${name} ↗`;
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    $('asn-links').append(link);
  }
  $('asn-links').hidden = false;
}
function displayLinkName(link) {
  return knownLinkNames.get(link.link_id) ?? 'Unknown link';
}
const pad = n => String(n).padStart(2, '0');
function localInput(seconds) {
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const B = Math.floor(Date.now()/60000)*60;
$('start').value = localInput(B-23*3600);
$('end').value = localInput(B);
$('timezone').textContent = `Browser timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
function clearResult() {
  generation++;
  cancelMetadata?.();
  $('asn-metadata').hidden = true;
  $('asn-metadata').textContent = '';
  controller?.abort();
  $('result').hidden = true;
  $('rows').replaceChildren();
  $('asn-links').hidden = true;
  $('asn-links').replaceChildren();
  $('all-links').checked = false;
  $('all-links').indeterminate = false;
  $('all-links').disabled = true;
  rows = [];
  seriesData = null;
  for (const chart of Object.values(charts)) chart.clear();
}
for (const id of ['asn','family','start','end']) {
  $(id).addEventListener('input', () => {
    clearResult();
    $('status').textContent = 'Filters changed. Press SET.';
  });
}
function exactSum(values) {
  if (!values.length) return null;
  // Sum decimal strings without converting byte totals to floating point.
  const scale = Math.max(...values.map(v => (v.split('.')[1] || '').length));
  let sum = 0n;
  for (const value of values) {
    const [whole, fraction=''] = value.split('.');
    sum += BigInt(whole + fraction.padEnd(scale, '0'));
  }
  if (!scale) return sum.toString();
  const digits = sum.toString().padStart(scale+1, '0');
  return `${digits.slice(0,-scale)}.${digits.slice(-scale)}`;
}
$('all-links').addEventListener('change', () => {
  for (const row of rows) row.checkbox.checked = $('all-links').checked;
  updateSelection();
});
function updateSelection() {
  const count = rows.filter(row => row.checkbox.checked).length;
  $('all-links').disabled = rows.length === 0;
  $('all-links').checked = rows.length > 0 && count === rows.length;
  $('all-links').indeterminate = count > 0 && count < rows.length;
  for (const row of rows) selections.set(row.data.link_id, row.checkbox.checked);
  renderCharts();
}
$('form').addEventListener('submit', async event => {
  event.preventDefault();
  clearResult();
  const requestGeneration = generation;
  controller = new AbortController();
  const start = new Date($('start').value).getTime()/1000;
  const end = new Date($('end').value).getTime()/1000;
  if (!Number.isInteger(start) || !Number.isInteger(end)) {
    $('status').textContent = 'Invalid date or time'; return;
  }
  $('status').textContent = 'Loading…';
  const params = new URLSearchParams({asn:$('asn').value, ip_version:$('family').value, start:String(start), end:String(end)});
  try {
    const signal = controller.signal;
    async function get(endpoint) {
      const response = await fetch(`/api/asn/${endpoint}?${params}`, {cache:'no-store', signal});
      const payload = await response.json();
      if (!response.ok) throw userError(response.status === 422
        ? (typeof payload.detail === 'string' ? payload.detail : 'Invalid request parameters')
        : response.status === 504 ? 'The traffic data request timed out. Please try again.'
        : response.status === 503 ? 'Unable to load links' : 'Unable to load traffic data. Please try again.');
      return payload;
    }
    const [data, minuteSeries, knownLinks] = await Promise.all([get('volumes'), get('series'),
      fetch('/api/links', {cache:'no-store', signal}).then(async response => {
        if (!response.ok) throw userError('Unable to load links');
        return response.json();
      })]);
    if (requestGeneration !== generation) return;
    knownLinkNames = new Map(knownLinks.map(link => [link.link_id, link.name]));
    seriesData = minuteSeries;
    // Retain historical links present in either independent VM response.
    const tableLinks = new Map(data.links.map(link => [link.link_id, link]));
    for (const link of minuteSeries.links) {
      if (!tableLinks.has(link.link_id)) tableLinks.set(link.link_id, {...link, in:null, out:null});
    }
    data.links = [...tableLinks.values()].sort((a,b) => a.link_id.localeCompare(b.link_id));
    for (const link of data.links) {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.checked = selections.get(link.link_id) ?? true;
      checkbox.addEventListener('change', updateSelection);
      const swatch = document.createElement('span');
      swatch.className = 'swatch'; swatch.style.backgroundColor = link.color;
      label.append(checkbox, swatch, document.createTextNode(displayLinkName(link)));
      item.append(label);
      $('rows').append(item); rows.push({data:link, checkbox});
    }
    const family = data.ip_version === 'both' ? 'IPv4 + IPv6' : `IPv${data.ip_version}`;
    $('period').textContent = `ASN ${data.asn}, ${family}: [${new Date(data.start*1000).toLocaleString()}, ${new Date(data.end*1000).toLocaleString()})`;
    updateAsnLinks(data.asn);
    $('asn-metadata').textContent = `AS${data.asn}`;
    $('asn-metadata').hidden = false;
    if (window.AsnMetadata) cancelMetadata = window.AsnMetadata.load([data.asn], records => {
      if (requestGeneration === generation) $('asn-metadata').textContent = window.AsnMetadata.label(data.asn, records.get(data.asn));
    });
    $('result').hidden = false; updateSelection();
    $('status').textContent = '';
  } catch (error) {
    if (requestGeneration !== generation || error.name === 'AbortError') return;
    controller.abort();
    $('result').hidden = true;
    $('status').textContent = `Error: ${error.userMessage || "Unable to load data. Please try again."}`;
  }
});

function speed(value) {
  const units = ['bit/s','kbit/s','Mbit/s','Gbit/s'];
  let amount = Number(value), unit = 0;
  while (amount >= 1000 && unit < units.length-1) {amount /= 1000; unit++;}
  return `${amount.toLocaleString(undefined, {maximumFractionDigits:3})} ${units[unit]}`;
}
function renderCharts() {
  if (!seriesData || $('result').hidden) return;
  if (!window.echarts) {
    $('empty-traffic').textContent = 'Unable to load chart';
    return;
  }
  if (!charts.traffic) {
    charts.traffic = echarts.init($('chart-traffic'));
    if (window.ResizeObserver) {
      const observer = new window.ResizeObserver(() => charts.traffic.resize());
      observer.observe($('chart-traffic'));
    }
  }
  const selected = new Set(rows.filter(row => row.checkbox.checked).map(row => row.data.link_id));
  const hasData = seriesData.links.some(link => selected.has(link.link_id)
    && ['in','out'].some(direction => link[direction].some(value => value !== null)));
  $('empty-traffic').textContent = selected.size === 0 || hasData ? '' : 'No data for the selected period';
  const series = seriesData.links.filter(link => selected.has(link.link_id)).flatMap(link => ['in','out'].map(direction => ({
    id:`${link.link_id}:${direction}`, name:link.link_id, type:'line',
    stack:direction === 'in' ? 'input' : 'output',
    areaStyle:{color:link.color,opacity:0.4},
    smooth:false, connectNulls:false, showSymbol:true, symbolSize:4,
    lineStyle:{color:link.color,width:2}, itemStyle:{color:link.color},
    // Negate input only for rendering; preserve missing samples and real zeros.
    data:link[direction].map(value => value === null ? null
      : direction === 'in' && Number(value) !== 0 ? -Number(value) : Number(value))
  })));
  // A separate silent overlay keeps zero visible even when every link is hidden.
  series.push({id:'zero-baseline',name:'',type:'line',data:[],silent:true,
    tooltip:{show:false},markLine:{silent:true,symbol:'none',label:{show:false},
      lineStyle:{color:'#9aadc4',width:2,type:'solid'},data:[{yAxis:0}]}});
  // Read the shared palette; defaults also support server-side rendering checks.
  const css = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement) : null;
  const color = (name, fallback) => css?.getPropertyValue(name).trim() || fallback;
  const theme = {background:color('--panel-bg','#18212d'), text:color('--text','#d5deea'),
    muted:color('--muted','#a2b0c2'), grid:color('--grid','#2b394b'),
    border:color('--border','#354459'), zero:color('--zero','#9aadc4')};
  series.at(-1).markLine.lineStyle.color = theme.zero;
  charts.traffic.setOption({
    backgroundColor:theme.background, textStyle:{color:theme.text},
    animation:false,
    graphic:selected.size === 0 ? [{type:'text',left:'center',top:'middle',silent:true,
      style:{text:'Select links',font:'16px sans-serif',fill:theme.muted}}] : [],
    grid:{left:90,right:25,top:20,bottom:90},
    tooltip:{trigger:'axis',renderMode:'richText',backgroundColor:theme.background,
      borderColor:theme.border,textStyle:{color:theme.text},
      axisPointer:{lineStyle:{color:theme.zero}},formatter:items => {
      const timestamp = Number(items[0]?.axisValue);
      if (!Number.isFinite(timestamp)) return '';
      const lines = [`[${new Date(timestamp*1000).toLocaleString()}, ${new Date((timestamp+60)*1000).toLocaleString()})`];
      const offset = seriesData.timestamps.indexOf(timestamp);
      const values = {in:[],out:[]};
      const missing = {in:0,out:0};
      // Tooltip totals use the unchanged positive source values, not stack coordinates.
      for (const row of rows.filter(row => selected.has(row.data.link_id))) {
        const link = seriesData.links.find(link => link.link_id === row.data.link_id);
        const parts = [];
        for (const direction of ['in','out']) {
          const value = link?.[direction][offset] ?? null;
          if (value === null) missing[direction]++;
          else {
            values[direction].push(value);
            parts.push(`${direction === 'in' ? 'Input' : 'Output'}: ${speed(value)}`);
          }
        }
        if (parts.length) lines.push(`${displayLinkName(row.data)} — ${parts.join('; ')}`);
      }
      if (!values.in.length && !values.out.length) return [...lines, 'No data'].join('\n');
      for (const direction of ['in','out']) {
        const sum = exactSum(values[direction]);
        lines.push(`Total ${direction === 'in' ? 'Input' : 'Output'}: ${sum === null ? 'No data' : speed(sum)}${sum !== null && missing[direction] ? ' (Incomplete data)' : ''}`);
      }
      return lines.join('\n');
    }},
    xAxis:{type:'category',boundaryGap:false,data:seriesData.timestamps.map(String),
      axisLine:{lineStyle:{color:theme.border}},axisTick:{lineStyle:{color:theme.border}},
      splitLine:{show:true,lineStyle:{color:theme.grid}},
      axisLabel:{color:theme.muted,formatter:timestamp => new Date(Number(timestamp)*1000).toLocaleString()}},
    yAxis:{type:'value',axisLine:{lineStyle:{color:theme.border}},axisTick:{lineStyle:{color:theme.border}},
      splitLine:{lineStyle:{color:theme.grid}},axisLabel:{color:theme.muted,formatter:value => speed(Math.abs(value))}},
    dataZoom:[{type:'inside'},{type:'slider',bottom:15,backgroundColor:theme.background,
      borderColor:theme.border,textStyle:{color:theme.muted},fillerColor:'rgba(143,190,242,0.15)',
      dataBackground:{lineStyle:{color:theme.muted},areaStyle:{color:theme.grid}},
      selectedDataBackground:{lineStyle:{color:theme.text},areaStyle:{color:theme.border}},
      handleStyle:{color:theme.border,borderColor:theme.zero},moveHandleStyle:{color:theme.zero}}],
    series
  }, {notMerge:true});
  charts.traffic.resize();
}
window.addEventListener('resize', () => {for (const chart of Object.values(charts)) chart.resize();});

const requestedAsn = new URLSearchParams(window.location.search).get('asn');
if (requestedAsn !== null) {
  if (/^[0-9]{1,10}$/.test(requestedAsn) && Number(requestedAsn) <= 4294967295) {
    $('asn').value = String(Number(requestedAsn));
    $('form').requestSubmit();
  } else {
    $('status').textContent = 'Invalid ASN: enter a whole number from 0 to 4294967295.';
  }
}
