const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const calls=[],timers=[];
let country=null;
const context=vm.createContext({Date,Map,JSON,AbortController,
 window:{},setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length},clearTimeout(){},
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({records:[{asn:'64498',name:'<img src=x onerror=alert(1)>',country,updating:true}]})}}
});
vm.runInContext(fs.readFileSync('app/static/asn-metadata.js','utf8'),context);
(async()=>{
 const api=context.window.AsnMetadata;
 assert.equal(api.label('1',null),'AS1');
 assert.equal(api.label('1',{name:null,country:'UA'}),'AS1');
 assert.equal(api.label('1',{name:'Name',country:null}),'AS1 — Name');
 assert.equal(api.label('1',{name:'Name',country:'UA'}),'AS1 — Name · UA');
 let shown='';
 const cancel=api.load(['64498'],records=>shown=api.label('64498',records.get('64498')));
 await new Promise(r=>setImmediate(r));
 assert.equal(shown,'AS64498 — <img src=x onerror=alert(1)>');
 assert.equal(calls[0].url,'/api/asn/metadata');
 assert.equal(timers[0].delay,60000);
 timers.find(t=>t.delay===2000).fn();await new Promise(r=>setImmediate(r));
 assert.equal(calls[1].url,'/api/asn/metadata/status');
 cancel();const length=calls.length;
 timers.find(t=>t.delay===2000).fn();await new Promise(r=>setImmediate(r));
 assert.equal(calls.length,length);
 // Cancellation aborts pending fetches; callbacks are never applied after it.
 for(const file of ['app/static/top-asn.js','app/static/app.js'])assert.doesNotMatch(fs.readFileSync(file,'utf8'),/innerHTML/);
 console.log('Metadata UI: text-only names, omitted null country, read-only polling and 60-second deadline passed');
})().catch(e=>{console.error(e);process.exitCode=1});
