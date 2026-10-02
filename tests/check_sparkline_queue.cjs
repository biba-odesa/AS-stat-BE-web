const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context=vm.createContext({window:{},AbortController});
vm.runInContext(fs.readFileSync('app/static/svg-images.js','utf8'),context);
(async()=>{
 for(const limit of [2,4]) {
  const loader=new context.window.SvgImageQueue(limit),images=[];
  let active=0,peak=0,started=0;
  for(let i=0;i<12;i++) {
   const image={removeAttribute(){if(this.src){delete this.src;active--}}};
   Object.defineProperty(image,'src',{configurable:true,set(value){Object.defineProperty(this,'src',{configurable:true,value});started++;active++;peak=Math.max(peak,active)}});
   images.push(image);loader.enqueue({image,status:{},url:`/api/test.svg?id=${i}`});
  }
  assert.equal(started,limit);assert.equal(peak,limit);
  active--;images[0].onerror();assert.equal(started,limit+1);assert.equal(peak,limit);
  active--;await images[1].onload();assert.equal(started,limit+2);
  for(const task of loader.active)assert.match(task.job.image.src,/^\/api\//);
  // Count only the active images when their sources are removed on reset.
  for(const image of images.slice(0,2))delete image.src;
  loader.reset();assert.equal(loader.active.size,0);assert.equal(loader.queue.length,0);
  assert.equal(started,limit+2);assert.equal(active,0);
 }
 for(const file of ['app/static/top-asn.js','app/static/link-usage.js','app/static/svg-images.js'])
  assert.doesNotMatch(fs.readFileSync(file,'utf8'),/createObjectURL|revokeObjectURL|new Blob|\.blob\(/);
 console.log('Direct SVG queues: two/four load limits, continuation after image errors, cancellation and no Blob URLs passed');
})().catch(error=>{console.error(error);process.exitCode=1});
