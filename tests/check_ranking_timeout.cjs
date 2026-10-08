const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{value:'20',events:{},children:[],addEventListener(k,f){this.events[k]=f},replaceChildren(){this.children=[]},append(...v){this.children.push(...v)}});return elements.get(id)};
get('top-form').requestSubmit=()=>{};
const timers=new Map();let next=0,requests=0;
const context=vm.createContext({Intl,Date,URLSearchParams,AbortController,console,
 window:{ASStat:{rankingTimeoutMs:88000,url:p=>p},SvgImageQueue:class{reset(){}},addEventListener(){}},
 document:{getElementById:get,querySelectorAll:()=>[],createElement:()=>({}),createTextNode:t=>t},
 setTimeout:(f,ms)=>{assert.equal(ms,88000);timers.set(++next,f);return next},clearTimeout:id=>timers.delete(id),
 fetch:(url,options)=>url==='/api/links'?Promise.resolve({ok:true,json:async()=>[]}):new Promise((resolve,reject)=>{requests++;options.signal.addEventListener('abort',()=>{const e=new Error();e.name='AbortError';reject(e)})})});
vm.runInContext(fs.readFileSync('app/static/top-asn.js','utf8'),context);
(async()=>{
 const pending=get('top-form').events.submit({preventDefault(){}});
 assert.equal(get('top-status').textContent,'Loading traffic ranking…');
 assert.equal(requests,1);assert.equal(timers.size,1);
 [...timers.values()][0]();await pending;
 assert.match(get('top-status').textContent,/Traffic ranking request timed out/);
 assert.equal(timers.size,0);assert.equal(requests,1,'No automatic retry');
 console.log('Ranking deadline: configured budget, loading state, abort, timer cleanup and no automatic retries passed');
})().catch(e=>{console.error(e);process.exitCode=1});
