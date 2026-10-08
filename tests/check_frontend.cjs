const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
  return {value:'', hidden:false, children:[], events:{}, style:{},
    setAttribute(name,value){this[name]=value}, addEventListener(name, callback){this.events[name]=callback},
    append(...items){this.children.push(...items)}, replaceChildren(){this.children=[]}};
}
const elements = new Map();
const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id)};
get('asn').value='64496'; get('family').value='both';
const chartInstances = [];
const links = [{link_id:'a',name:'Link A',color:'#112233',in:'0',out:null}];
const series = {step:300,start:120,end:300,timestamps:[120,180,240],links:[{...links[0],in:['0',null,'10'],out:[null,null,null]}]};
let rejectRequests = false;
let requestCount = 0;
let resizeObserver;
const context = vm.createContext({console,Date,Intl,Number,String,Map,Set,BigInt,Object,URLSearchParams,AbortController,
  document:{getElementById:get,createElement:element,createTextNode:text=>text},
  window:{location:{search:""},echarts:true,addEventListener(){},ResizeObserver:class {constructor(callback){this.callback=callback;resizeObserver=this} observe(target){this.target=target}}},
  echarts:{init(){const chart={setOption(option){this.option=option},resize(){this.resizeCount=(this.resizeCount || 0)+1},clear(){},on(name,callback){this.legend=callback}}; chartInstances.push(chart); return chart}},
  fetch:async url=>(requestCount++, {ok:url === '/api/links' || !rejectRequests,status:rejectRequests?504:200,json:async()=>rejectRequests && url !== '/api/links'?{detail:'VM timeout'}:url === '/api/links'?links:url.includes('/series?')?series:{asn:'64496',ip_version:'both',start:120,end:300,links}})
});
vm.runInContext(fs.readFileSync('app/static/app.js','utf8'),context);
(async()=>{
  await get('form').events.submit({preventDefault(){}});
  assert.equal(get('asn-links').hidden,false);
  assert.equal(get('asn-links').children.length,8);
  assert.equal(get('all-links').checked,true);
  assert.equal(get('all-links').indeterminate,false);
  assert.equal(get('result').hidden,false);
  assert.equal(chartInstances.length,1);
  assert.deepEqual(Array.from(chartInstances[0].option.series[0].data),[0,null,-10]);
  assert.equal(chartInstances[0].option.series[0].connectNulls,false);
  assert.equal(chartInstances[0].option.series[0].smooth,false);
  assert.equal(chartInstances[0].option.series[0].stack,'input');
  assert.equal(chartInstances[0].option.series[1].stack,'output');
  assert.equal(chartInstances[0].option.series[0].areaStyle.color,'#112233');
  assert.equal(chartInstances[0].option.series[0].name, chartInstances[0].option.series[1].name);
  assert.equal(chartInstances[0].option.series[0].itemStyle.color, chartInstances[0].option.series[1].itemStyle.color);
  assert.equal(chartInstances[0].option.yAxis.axisLabel.formatter(-10),'10 bit/s');
  assert.equal(chartInstances[0].option.series.at(-1).markLine.data[0].yAxis,0);
  assert.deepEqual(series.links[0].in,['0',null,'10']);
  const formatter = chartInstances[0].option.tooltip.formatter;
  assert.match(formatter([{axisValue:'120'}]), /Total Input: 0 bit\/s/);
  const emptyTooltip = formatter([{axisValue:'180'}]);
  assert.equal(emptyTooltip.split('\n').length,2);
  assert.match(emptyTooltip,/\nNo data$/);
  assert.doesNotMatch(emptyTooltip,/Link A|Total/);
  assert.match(formatter([{axisValue:'120'}]),/Link A — Input: 0 bit\/s\n/);
  series.links.push({link_id:'b',name:'Link B',color:'#445566',in:['5','5','5'],out:['2',null,'3']});
  links.push({link_id:'b',name:'Link B',color:'#445566',in:'15',out:null});
  await get('form').events.submit({preventDefault(){}});
  assert.match(chartInstances[0].option.tooltip.formatter([{axisValue:'180'}]), /5 bit\/s \(Incomplete data\)/);
  assert.match(chartInstances[0].option.tooltip.formatter([{axisValue:'240'}]), /Total Input: 15 bit\/s/);
  assert.equal(chartInstances[0].option.series[0].stack,chartInstances[0].option.series[2].stack);
  assert.equal(chartInstances[0].option.series[1].stack,chartInstances[0].option.series[3].stack);
  assert.equal(chartInstances[0].option.legend,undefined);
  assert.deepEqual(Array.from(chartInstances[0].option.series[3].data),[2,null,3]);
  assert.match(chartInstances[0].option.tooltip.formatter([{axisValue:'240'}]), /Total Output: 3 bit\/s \(Incomplete data\)/);
  assert.doesNotMatch(chartInstances[0].option.tooltip.formatter([{axisValue:'240'}]), /-10/);
  assert.match(chartInstances[0].option.tooltip.formatter([{axisValue:'180'}]), /Total Output: No data/);
  assert.match(chartInstances[0].option.tooltip.formatter([{axisValue:'240'}]), /Link A — Input: 10 bit\/s\n/);
  assert.doesNotMatch(chartInstances[0].option.tooltip.formatter([{axisValue:'180'}]), /Link A|Link B.*Output:/);
  series.links[1].in[1] = null;
  series.links[1].out[1] = '0';
  const outputOnly = chartInstances[0].option.tooltip.formatter([{axisValue:'180'}]);
  assert.match(outputOnly,/Link B — Output: 0 bit\/s\n/);
  assert.match(outputOnly,/Total Output: 0 bit\/s \(Incomplete data\)/);
  assert.doesNotMatch(outputOnly,/Link B.*Input:/);
  series.links[1].in[1] = '5';
  series.links[1].out[1] = null;
  const checkbox = get('rows').children[0].children[0].children[0];
  checkbox.checked=false; checkbox.events.change();
  assert.equal(get('all-links').checked,false);
  assert.equal(get('all-links').indeterminate,true);
  assert.equal(chartInstances[0].option.series.some(series=>series.name==='a'),false);
  assert.equal(chartInstances[0].option.series.filter(series=>series.name==='b').length,2);
  checkbox.checked=true; checkbox.events.change();
  assert.equal(chartInstances[0].option.series.filter(series=>series.name==='a').length,2);
  links[1].name = 'Link A';
  series.links[1].name = 'Link A';
  series.links.push({link_id:'hidden-historical-id',name:'hidden-historical-id',color:'#808080',in:['1',null,'1'],out:[null,null,null]});
  await get('form').events.submit({preventDefault(){}});
  const option = chartInstances[0].option;
  assert.equal(option.legend,undefined);
  assert.notEqual(option.series[0].name,option.series[2].name);
  const firstCheckbox = get('rows').children[0].children[0].children[0];
  firstCheckbox.checked=false; firstCheckbox.events.change();
  assert.equal(chartInstances[0].option.series.some(series=>series.name==='a'),false);
  assert.equal(chartInstances[0].option.series.filter(series=>series.name==='b').length,2);
  const tooltip = chartInstances[0].option.tooltip.formatter([{axisValue:'240'}]);
  assert.match(tooltip,/Unknown link/);
  assert.doesNotMatch(tooltip,/hidden-historical-id|\(a\)|\(b\)/);
  const labels = get('rows').children.map(row => row.children[0].children[2]);
  assert.deepEqual(labels,['Link A','Link A','Unknown link']);
  assert.equal(resizeObserver.target,get('chart-traffic'));
  const resizeCount=chartInstances[0].resizeCount;
  resizeObserver.callback();
  assert.equal(chartInstances[0].resizeCount,resizeCount+1);
  const html=fs.readFileSync('app/static/view-asn.html','utf8');
  assert.doesNotMatch(html,/<table|Входящий объём|Исходящий объём|knownlinks|sampling|max-width:1000px/);
  assert.match(html,/minmax\(160px,10%\)/);
  assert.match(html,/@media \(max-width:640px\)/);
  const beforeSelection = requestCount;
  get('all-links').checked=false; get('all-links').events.change();
  assert.equal(get('all-links').indeterminate,false);
  assert.equal(chartInstances[0].option.series.length,1);
  assert.equal(chartInstances[0].option.graphic[0].style.text,'Select links');
  assert.ok(get('rows').children.every(row=>!row.children[0].children[0].checked));
  get('all-links').checked=true; get('all-links').events.change();
  assert.equal(get('all-links').indeterminate,false);
  assert.equal(chartInstances[0].option.graphic.length,0);
  assert.equal(chartInstances[0].option.series.length,7);
  assert.equal(requestCount,beforeSelection);
  get('asn').value='42';
  assert.equal(get('asn-links').children[0].href,'https://www.peeringdb.com/asn/64496');
  get('asn').events.input();
  assert.equal(get('asn-links').hidden,true);
  assert.equal(requestCount,beforeSelection);
  vm.runInContext('updateAsnLinks("64501")',context);
  assert.deepEqual(get('asn-links').children.map(link=>link.href),[
    'https://www.peeringdb.com/asn/64501',
    'https://www.robtex.com/as/as64501.html',
    'https://bgp.he.net/AS64501',
    'https://stat.ripe.net/AS64501#tabId=at-a-glance',
    'http://www.cidr-report.org/cgi-bin/as-report?as=AS64501&view=2.0',
    'http://www.cidr-report.org/cgi-bin/as-report?as=AS64501&view=2.0&v=6',
    'https://radar.qrator.net/as64501/',
    'https://bgp.tools/as/64501'
  ]);
  assert.ok(get('asn-links').children.every(link=>link.target==='_blank' && link.rel==='noopener noreferrer'));
  assert.match(html,/<html lang="en">/);
  assert.match(html,/<footer id="timezone"><\/footer>/);
  assert.match(get('timezone').textContent,/^Browser timezone: /);
  assert.doesNotMatch(html,/[А-Яа-яЁё]/);
  assert.match(html,/<nav id="asn-links"[^>]*hidden/);
  assert.match(html,/<button id="show" type="submit">SET<\/button>/);
  const beforeDeepLink = requestCount;
  let autoSubmits = 0;
  get('form').requestSubmit = () => {autoSubmits++;};
  context.window.location.search = '?asn=00064496';
  const deepLinkCode = fs.readFileSync('app/static/app.js','utf8').split('const requestedAsn =')[1];
  vm.runInContext('{ const requestedAsn =' + deepLinkCode + '}',context);
  assert.equal(autoSubmits,1);
  assert.equal(get('asn').value,'64496');
  assert.doesNotMatch(html,/id="(?:start|end)"/);
  context.window.location.search = '?asn=4294967296';
  vm.runInContext('{ const requestedAsn =' + deepLinkCode + '}',context);
  assert.equal(autoSubmits,1);
  assert.match(get('status').textContent,/Invalid ASN/);
  assert.equal(requestCount,beforeDeepLink);
  assert.doesNotMatch(html,/<h1 id="asn-title"/);
  rejectRequests=true;
  await get('form').events.submit({preventDefault(){}});
  assert.equal(get('result').hidden,true);
  assert.equal(get('asn-links').hidden,true);
  assert.match(get('status').textContent,/period 1d/);
  console.log('Frontend logic checks passed (DOM/ECharts mocks, not a real browser)');
})().catch(error=>{console.error(error);process.exitCode=1});

// Exercise real ECharts, including shared legend names and independent stacks.
const echarts = require('../app/static/vendor/echarts/echarts.min.js');
const chart = echarts.init(null,null,{renderer:'svg',ssr:true,width:600,height:300});
chart.setOption({animation:false,legend:{data:['A','B']},
  xAxis:{type:'category',data:['120','180','240']},yAxis:{type:'value'},
  series:[{name:'A',type:'line',stack:'input',areaStyle:{},connectNulls:false,data:[-10,null,-20]},
          {name:'A',type:'line',stack:'output',areaStyle:{},connectNulls:false,data:[2,3,4]},
          {name:'B',type:'line',stack:'input',areaStyle:{},connectNulls:false,data:[-5,-5,-5]},
          {name:'B',type:'line',stack:'output',areaStyle:{},connectNulls:false,data:[1,null,1]}]});
function stacked(index) {
  const data = chart.getModel().getSeries()[index].getData();
  const dimension = data.getCalculationInfo('stackResultDimension');
  return [0,1,2].map(i=>data.get(dimension,i));
}
assert.ok(Number.isNaN(stacked(0)[1]));
assert.deepEqual(stacked(2),[-15,-5,-25]);
assert.equal(stacked(3)[0],3);
assert.ok(Number.isNaN(stacked(3)[1]));
assert.equal(stacked(3)[2],5);
chart.dispatchAction({type:'legendUnSelect',name:'A'});
assert.deepEqual(chart.getModel().getSeries().map(series=>chart.getModel().isSeriesFiltered(series)),[true,true,false,false]);
chart.dispatchAction({type:'legendSelect',name:'A'});
assert.deepEqual(chart.getModel().getSeries().map(series=>chart.getModel().isSeriesFiltered(series)),[false,false,false,false]);
assert.ok(chart.renderToSVGString().includes('<path'));
chart.dispose();
console.log('Real ECharts SVG SSR: independent signed stacks, preserved gaps, paired legend switching passed');
