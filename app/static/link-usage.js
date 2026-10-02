const $ = id => document.getElementById(id);
const imageLoader = new window.SvgImageQueue(2);
const controllers = new Set();
let stopped = false;
let observer;
const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
$('usage-timezone').textContent = `Browser timezone: ${browserTimezone}`;
function legendGroup(direction, entries) {
  const group = document.createElement('div'); group.className = 'usage-legend-group';
  const title = document.createElement('strong'); title.textContent = direction === 'in' ? 'Input' : 'Output';
  group.append(title);
  for (const entry of entries) {
    const item = document.createElement(entry.asn === null ? 'span' : 'a'); item.className = 'usage-legend-item';
    if (entry.asn !== null) item.href = `/view-asn?asn=${encodeURIComponent(entry.asn)}`;
    const dot = document.createElement('span'); dot.className = 'usage-dot'; dot.style.backgroundColor = entry.color;
    item.append(dot,document.createTextNode(entry.label)); group.append(item);
  }
  return group;
}
function watch(job) {
  if (!window.IntersectionObserver) {imageLoader.enqueue(job);return;}
  if (!observer) observer = new window.IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      observer.unobserve(entry.target);imageLoader.enqueue(entry.target.usageJob);
    }
  },{rootMargin:'300px 0px'});
  job.section.usageJob = job; observer.observe(job.section);
}
async function initialize() {
  try {
    const response = await fetch('/api/link-usage',{cache:'no-store'});
    if (!response.ok) throw new Error('links');
    const data = await response.json();
    if (stopped) return;
    $('usage-period').textContent = `[${new Date(data.start*1000).toLocaleString()}, ${new Date(data.end*1000).toLocaleString()})`;
    const jobs = [];
    for (const link of data.links) {
      const section = document.createElement('section'); section.className = 'usage-section';
      const title = document.createElement('h2'); title.textContent = link.name;
      const image = document.createElement('img'); image.className = 'usage-image'; image.width=840; image.height=340;
      image.alt = `Input and Output for ${link.name}`;
      const status = document.createElement('p');status.className='traffic-status';status.role='status';status.textContent='Waiting to load';
      const legend = document.createElement('div');legend.className='usage-legend';
      section.append(title,image,status,legend);$('usage-links').append(section);
      const parameters = new URLSearchParams({link_id:link.link_id,start:String(data.start),end:String(data.end),tz:browserTimezone,v:'2'});
      jobs.push({section,image,status,legend,url:`/api/link-usage/sparkline.svg?${parameters}`,
        loaded:async signal => {
          // The image request has already populated the shared SVG/legend cache.
          const response=await fetch(`/api/link-usage/link?${parameters}`,{signal});
          if (!response.ok) throw new Error('legend');
          const data=await response.json();
          if (signal.aborted || stopped) return '';
          legend.replaceChildren(legendGroup('in',data.legend.in),legendGroup('out',data.legend.out));
          return data.has_data ? '' : 'No data';
        }});
    }
    $('usage-status').textContent = data.links.length ? '' : 'No links configured';
    // Create every placeholder before starting any expensive link request.
    for (const job of jobs) watch(job);
  } catch (error) {if (!stopped) $('usage-status').textContent='Unable to load links. Please try again later.';}
}
window.addEventListener('pagehide',() => {
  stopped=true;observer?.disconnect();imageLoader.reset();
  for (const controller of controllers) controller.abort();
});
initialize();
