const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function element(){return {children:[],style:{},events:{},dataset:{},textContent:'',append(...v){this.children.push(...v)},replaceChildren(...v){this.children=v},setAttribute(k,v){this[k]=v},addEventListener(k,fn){this.events[k]=fn}};}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
const modes=['6','4','compare'].map(mode=>Object.assign(element(),{dataset:{mode}}));
const periods=['1d','1w','1m','1y'].map(period=>Object.assign(element(),{dataset:{period}}));
const pending=[],jobs=[],events={};let resets=0;
const context=vm.createContext({Intl,Date,URLSearchParams,AbortController,setTimeout,clearTimeout,
 window:{ASStat:{url:path=>'/asstat2'+path},addEventListener:(k,f)=>events[k]=f,SvgImageQueue:class {constructor(limit){assert.equal(limit,2)}reset(){resets++;jobs.length=0}enqueue(job){jobs.push(job)}}},
 document:{getElementById:get,querySelectorAll:s=>s==='[data-mode]'?modes:periods,createElement:element,createTextNode:text=>({textContent:text})},
 fetch:(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}))});
vm.runInContext(fs.readFileSync('app/static/ipv.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const data=period=>({mode:'compare',start:120,end:360,step:300,history_limited:period==='1m',legend:[{name:'<fixture>',color:'#66c2a5'}],svg_urls:['/asstat2/api/ipv/traffic.svg?direction=in','/asstat2/api/ipv/traffic.svg?direction=out']});
(async()=>{
 assert.equal(pending.length,1,'Default Compare + 1w loads once');
 pending[0].resolve({ok:true,json:async()=>data('1w')});await tick();pending.length=0;jobs.length=0;
 assert.equal(vm.runInContext('ipvMode',context),'compare');assert.equal(vm.runInContext('ipvPeriod',context),'1w');
 periods[1].events.click();assert.match(pending[0].url,/^\/asstat2\/api\/ipv\?mode=compare&period=1w/);
 periods[2].events.click();assert.equal(pending[0].options.signal.aborted,true);
 pending[0].resolve({ok:true,json:async()=>data('1w')});await tick();assert.equal(jobs.length,0,'Old selection ignored');
 pending[1].resolve({ok:true,json:async()=>data('1m')});await tick();
 assert.equal(jobs.length,2);assert.ok(jobs.every(job=>job.url.startsWith('/asstat2/api/')));
 assert.match(get('ipv-history').textContent,/Retention does not guarantee/);
 assert.equal(get('ipv-legend').children[0].children[1].textContent,'<fixture>');
 modes[0].events.click();assert.match(pending[2].url,/mode=6&period=1m/);assert.equal(jobs.length,0);
 pending[2].resolve({ok:false});await tick();assert.match(get('ipv-status').textContent,/Unable to load/);assert.equal(get('ipv-graphs').children.length,0);
 events.pagehide();assert.ok(resets>=4);
 assert.doesNotMatch(fs.readFileSync('app/static/ipv.js','utf8'),/createObjectURL|Blob/);
 console.log('IPv frontend: defaults, explicit requests, prefix, direct SVG, stale results, errors and safe legend passed');
})().catch(error=>{console.error(error);process.exitCode=1});
