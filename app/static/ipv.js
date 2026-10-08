// Load the default once; discard obsolete responses after selection changes.
const ipvUrl = path => window.ASStat ? window.ASStat.url(path) : path;
const ipvElement = id => document.getElementById(id);
const ipvTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const ipvQueue = new window.SvgImageQueue(2);
let ipvMode='compare', ipvPeriod='1w', ipvGeneration=0, ipvController, ipvTimer;
ipvElement('ipv-timezone').textContent=`Browser timezone: ${ipvTimezone}`;
async function loadIpv() {
  const generation=++ipvGeneration;
  clearTimeout(ipvTimer);ipvController?.abort();ipvQueue.reset();ipvController=new AbortController();
  ipvElement('ipv-graphs').replaceChildren();ipvElement('ipv-legend').replaceChildren();
  ipvElement('ipv-period').textContent='';ipvElement('ipv-history').textContent='';
  ipvElement('ipv-status').textContent='Loading traffic…';
  const requestController=ipvController;
  let timedOut=false;
  const fallbackBudgets={'1d':860000,'1w':1180000,'1m':860000,'1y':2428000};
  const timeout=ipvTimer=setTimeout(()=>{timedOut=true;requestController.abort();},window.ASStat?.ipvTimeoutMs?.[ipvPeriod] || fallbackBudgets[ipvPeriod]);
  for (const button of document.querySelectorAll('[data-mode]')) button.setAttribute('aria-pressed',String(button.dataset.mode===ipvMode));
  for (const button of document.querySelectorAll('[data-period]')) button.setAttribute('aria-pressed',String(button.dataset.period===ipvPeriod));
  try {
    const params=new URLSearchParams({mode:ipvMode,period:ipvPeriod,tz:ipvTimezone});
    const response=await fetch(ipvUrl(`/api/ipv?${params}`),{cache:'no-store',signal:ipvController.signal});
    if (generation!==ipvGeneration)return;
    if (!response.ok)throw new Error(`Unable to load IPv traffic for period ${ipvPeriod}. Please try again.`);
    const data=await response.json();
    if (generation!==ipvGeneration)return;
    ipvElement('ipv-period').textContent=`[${new Date(data.start*1000).toLocaleString()}, ${new Date(data.end*1000).toLocaleString()}) · ${data.step}s intervals`;
    ipvElement('ipv-history').textContent='Only available archive history is shown. Retention does not guarantee accumulated history. Missing intervals remain gaps; the cache is not an archive.';
    for (const entry of data.legend) {
      const item=document.createElement('li'),dot=document.createElement('span');
      dot.className='ipv-dot';dot.style.backgroundColor=entry.color;
      item.append(dot,document.createTextNode(entry.name));ipvElement('ipv-legend').append(item);
    }
    data.svg_urls.forEach((url,index)=>{
      const section=document.createElement('section'),image=document.createElement('img'),status=document.createElement('p');
      image.className='ipv-image';image.alt=data.mode==='compare'?(index===0?'Input: IPv4 / IPv6':'Output: IPv4 / IPv6'):'Output up, Input down: traffic by link';
      section.append(image,status);ipvElement('ipv-graphs').append(section);
      ipvQueue.enqueue({image,status,url,loadingMessage:'Loading traffic…',errorMessage:`Unable to load IPv traffic image for period ${ipvPeriod}. Please try again.`,valid:()=>generation===ipvGeneration});
    });
    ipvElement('ipv-status').textContent='';
  } catch(error) {
    if (generation!==ipvGeneration || (error.name==='AbortError' && !timedOut))return;
    ipvElement('ipv-status').textContent=timedOut ? `Traffic request timed out for period ${ipvPeriod}. Please try again.` : `Unable to load IPv traffic for period ${ipvPeriod}. Please try again.`;
  } finally {
    clearTimeout(timeout);
  }
}
for (const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>{ipvMode=button.dataset.mode;loadIpv();});
for (const button of document.querySelectorAll('[data-period]'))button.addEventListener('click',()=>{ipvPeriod=button.dataset.period;loadIpv();});
window.addEventListener('pagehide',()=>{ipvGeneration++;clearTimeout(ipvTimer);ipvController?.abort();ipvQueue.reset();});

loadIpv();
