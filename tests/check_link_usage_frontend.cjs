const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
let observed=[],observerCallback,active=0,peak=0,started=0;const requests=[],events={};
function element(){
 const item={children:[],style:{},events:{},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=[...items]},removeAttribute(){if(this._src){this._src='';active--}}};
 Object.defineProperty(item,'src',{get(){return this._src},set(value){this._src=value;active++;started++;peak=Math.max(peak,active)}});
 return item;
}
const links=Array.from({length:24},(_,i)=>({link_id:`internal-${i}`,name:`Display ${i}`}));
const bundle={has_data:true,legend:{in:[{asn:'1',label:'AS1',color:'#123456'},{asn:null,label:'Others',color:'#8491a3'}],out:[{asn:'1',label:'AS1',color:'#123456'}]}};
const context=vm.createContext({console,Date,Intl,URLSearchParams,AbortController,
 document:{getElementById:get,createElement:element,createTextNode:text=>text},
 window:{addEventListener:(name,callback)=>events[name]=callback,IntersectionObserver:class {
 constructor(callback){observerCallback=callback}observe(target){observed.push(target)}unobserve(){}disconnect(){observed=[]}}},
 fetch:async(url,options)=>{
 requests.push(url);
 return {ok:true,json:async()=>url==='/api/link-usage'?{start:0,end:86400,links}:bundle};
 }});
vm.runInContext(fs.readFileSync('app/static/svg-images.js','utf8'),context);
vm.runInContext(fs.readFileSync('app/static/link-usage.js','utf8'),context);
(async()=>{
 await new Promise(r=>setImmediate(r));
 assert.equal(get('usage-links').children.length,24);assert.equal(observed.length,24);
 assert.equal(requests.length,1);assert.equal(started,0);
 const sections=get('usage-links').children;
 assert.equal(sections[0].children[0].textContent,'Display 0');
 observerCallback(observed.slice(0,5).map(target=>({target,isIntersecting:true})));
 assert.equal(started,2);assert.equal(peak,2);
 const first=sections[0].children[1];
 assert.match(first.src,/^\/api\/link-usage\/sparkline.svg\?/);assert.match(first.src,/start=0&end=86400&tz=/);
 active--;first._src='';first.onerror();assert.equal(started,3);
 assert.match(sections[0].children[2].textContent,/Image unavailable/);
 const second=sections[1].children[1];active--;second._src='';await second.onload();
 assert.equal(started,4);assert.equal(peak,2);
 assert.equal(sections[1].children[2].textContent,'');
 const groups=sections[1].children[3].children;
 assert.equal(groups[0].children[0].textContent,'Input');assert.equal(groups[1].children[0].textContent,'Output');
 assert.equal(groups[0].children[1].href,'/view-asn?asn=1');
 assert.equal(groups[0].children[1].children[0].style.backgroundColor,groups[1].children[1].children[0].style.backgroundColor);
 assert.equal(groups[0].children[2].href,undefined);
 assert.ok(requests.every(url=>!url.includes('/metadata')&&!url.includes('sparkline.svg')));
 events.pagehide();await new Promise(r=>setImmediate(r));assert.equal(active,0);assert.equal(started,4);
 assert.doesNotMatch(fs.readFileSync('app/static/link-usage.js','utf8'),/innerHTML|createObjectURL/);
 console.log('Link Usage: direct viewport SVG URLs, two loads, cached legend after image, isolated errors and cancellation passed (DOM mocks)');
})().catch(error=>{console.error(error);process.exitCode=1});
