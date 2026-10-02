const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function element(){return {value:'',children:[],events:{},style:{},addEventListener(n,c){this.events[n]=c},append(...c){this.children.push(...c)},replaceChildren(){this.children=[]}}}
const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
get('limit').value='20';
const buttons=['20','50','300'].map(limit=>({...element(),dataset:{limit}}));
let submissions=0;get('top-form').requestSubmit=()=>submissions++;
const requests=[];
const context=vm.createContext({window:{addEventListener(){},IntersectionObserver:class {observe(){} disconnect(){} unobserve(){}}},console,Date,Intl,Number,String,URLSearchParams,AbortController,
 document:{getElementById:get,createElement:element,createTextNode:t=>t,querySelectorAll:()=>buttons},
 fetch:async url=>{requests.push(url);return {ok:true,json:async()=>url==='/api/links'?[{link_id:'secret',name:'Visible name',color:'#112233'}]:
 {start:120,end:86520,rows:[{rank:1,asn:'64496',in:'0',out:null,total:'0'}]}}}
});
vm.runInContext(fs.readFileSync('app/static/svg-images.js','utf8'),context);
vm.runInContext(fs.readFileSync('app/static/top-asn.js','utf8'),context);
(async()=>{
 assert.equal(submissions,1);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(get('link-legend').children[0].children[1],'Visible name');
 await get('top-form').events.submit({preventDefault(){}});
 const row=get('ranking-rows').children[0];
 assert.equal(row.children[1].children[0].href,'/view-asn?asn=64496');
 assert.equal(row.children[2].textContent,'0 B');
 assert.equal(row.children[2].title,'0 B');
 assert.equal(row.children[3].textContent,'No data');
 assert.equal(row.children.length,5);
 const graphRow=get('ranking-rows').children[1];
 assert.equal(graphRow.children[0].colSpan,5);
 const imageLink=graphRow.children[0].children[0],image=imageLink.children[0];
 assert.equal(imageLink.href,'/view-asn?asn=64496');
 assert.equal(image.width,840);assert.equal(image.height,260);
 assert.match(image.sparklineJob.url,/v=5/);
 assert.match(image.sparklineJob.url,/tz=/);
 const originalURL=image.sparklineJob.url;
 await get('top-form').events.submit({preventDefault(){}});
 const repeated=get('ranking-rows').children[1].children[0].children[0].children[0];
 assert.equal(repeated.sparklineJob.url,originalURL);
 assert.match(image.sparklineJob.url,/start=120&end=86520/);
 assert.doesNotMatch(fs.readFileSync('app/static/index.html','utf8'),/<th>Traffic<\/th>/);

 assert.ok(requests.every(url=>!url.includes('/series')&&!url.includes('/volumes')));
 get('limit').value='301';await get('top-form').events.submit({preventDefault(){}});
 assert.match(get('top-status').textContent,/1 to 300/);
 buttons[2].events.click();assert.equal(get('limit').value,'300');assert.equal(submissions,2);
 console.log('Top ASN frontend: legend, limits, null/zero, navigation, no per-row queries passed');
})().catch(e=>{console.error(e);process.exitCode=1});
