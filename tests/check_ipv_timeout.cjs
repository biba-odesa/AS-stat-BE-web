const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{events:{},children:[],textContent:'',append(...v){this.children.push(...v)},replaceChildren(){this.children=[]},addEventListener(k,f){this.events[k]=f}});return elements.get(id)};
const budgets={'1d':860000,'1w':1180000,'1m':860000,'1y':2428000},timers=new Map(),pending=[],events={};let sequence=0;
const context=vm.createContext({Intl,Date,URLSearchParams,AbortController,console,
 window:{ASStat:{url:p=>'/asstat2'+p,ipvTimeoutMs:budgets},SvgImageQueue:class{reset(){} enqueue(job){assert.equal(job.loadingMessage,'Loading traffic…')}},addEventListener:(k,f)=>events[k]=f},
 document:{getElementById:get,querySelectorAll:()=>[],createElement:()=>({append(){},style:{}}),createTextNode:t=>t},
 setTimeout:(f,ms)=>{timers.set(++sequence,{f,ms});return sequence},clearTimeout:id=>timers.delete(id),
 fetch:(url,options)=>new Promise((resolve,reject)=>{pending.push({url,resolve,options});options.signal.addEventListener('abort',()=>{const e=new Error();e.name='AbortError';reject(e)})})});
vm.runInContext(fs.readFileSync('app/static/ipv.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const payload={mode:'compare',start:0,end:300,step:300,legend:[],svg_urls:['/asstat2/api/ipv/traffic.svg']};
(async()=>{
 pending[0].resolve({ok:true,json:async()=>payload});await tick();
 for(const mode of ['4','6','compare'])for(const period of Object.keys(budgets)){
  const promise=vm.runInContext(`ipvMode='${mode}';ipvPeriod='${period}';loadIpv()`,context);
  assert.equal(get('ipv-status').textContent,'Loading traffic…');
  assert.equal(timers.size,1);assert.equal([...timers.values()][0].ms,budgets[period]);
  const job=pending.at(-1);assert.ok(job.url.includes(`mode=${mode}&period=${period}`));
  job.resolve({ok:true,json:async()=>payload});await promise;assert.equal(timers.size,0);
 }
 const promise=vm.runInContext('loadIpv()',context),count=pending.length;
 [...timers.values()][0].f();await promise;
 assert.match(get('ipv-status').textContent,/Traffic request timed out/);
 assert.equal(pending.length,count,'No automatic retries');assert.equal(timers.size,0);
 const old=vm.runInContext('loadIpv()',context),oldRequest=pending.at(-1);
 const latest=vm.runInContext('loadIpv()',context);
 assert.equal(oldRequest.options.signal.aborted,true);assert.equal(timers.size,1);
 events.pagehide();await Promise.all([old,latest]);assert.equal(timers.size,0);
 console.log('IPv deadlines: all 12 combinations, configured whole-calculation budgets, cancellation, cleanup and no retries passed');
})().catch(e=>{console.error(e);process.exitCode=1});
