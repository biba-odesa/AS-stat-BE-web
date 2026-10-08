const localUrl = path => window.ASStat ? window.ASStat.url(path) : path;
const $ = id => document.getElementById(id);
let generation = 0;
let controller;
let cancelMetadata;
const imageLoader = new window.SvgImageQueue(4);
const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
let imageObserver;
function resetImages() {
  imageObserver?.disconnect();imageLoader.reset();
}
function observeImage(job) {
  if (!window.IntersectionObserver) {
    imageLoader.enqueue(job); return;
  }
  if (!imageObserver) imageObserver = new window.IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      imageObserver.unobserve(entry.target);
      imageLoader.enqueue(entry.target.sparklineJob);
    }
  }, {rootMargin:'300px 0px'});
  job.valid = () => job.generation === generation;
  job.image.sparklineJob = job;
  imageObserver.observe(job.image);
}
$('top-timezone').textContent = `Browser timezone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
function clearRanking() {
  generation++;
  cancelMetadata?.();
  resetImages();
  controller?.abort();
  $('ranking').hidden = true;
  $('ranking-rows').replaceChildren();
  $('top-period').textContent = '';
}
function volumeCell(value) {
  const cell = document.createElement('td');
  if (value === null) {cell.textContent = 'No data'; return cell;}
  cell.title = `${value} B`;
  const units = ['B','kB','MB','GB','TB'];
  let amount = Number(value), unit = 0;
  while (amount >= 1000 && unit < units.length-1) {amount /= 1000; unit++;}
  cell.textContent = `${amount.toLocaleString(undefined,{maximumFractionDigits:2})} ${units[unit]}`;
  return cell;
}
$('limit').addEventListener('input', () => {
  clearRanking(); $('top-status').textContent = 'Top changed. Press SET.';
});
for (const button of document.querySelectorAll('[data-limit]')) {
  button.addEventListener('click', () => {
    $('limit').value = button.dataset.limit;
    $('top-form').requestSubmit();
  });
}
$('top-form').addEventListener('submit', async event => {
  event.preventDefault();
  clearRanking();
  const current = generation;
  const limit = $('limit').value;
  if (!/^[0-9]{1,3}$/.test(limit) || Number(limit)<1 || Number(limit)>300) {
    $('top-status').textContent = 'Top must be a whole number from 1 to 300'; return;
  }
  controller = new AbortController();
  $('top-status').textContent = 'Loading traffic ranking…';
  const requestController = controller;
  let timedOut = false;
  const timeout = setTimeout(() => {timedOut = true; requestController.abort();}, window.ASStat?.rankingTimeoutMs || 74000);
  try {
    const response = await fetch(localUrl(`/api/top-asn?limit=${Number(limit)}`),{cache:'no-store',signal:controller.signal});
    if (current !== generation) return;
    if (!response.ok) {
      $('top-status').textContent = response.status === 504 ? 'Traffic data request timed out. Please try again.' : 'Unable to load traffic ranking. Please try again.';
      return;
    }
    const data = await response.json();
    if (current !== generation) return;
    const metadataLabels = new Map();
    for (const row of data.rows) {
      const item = document.createElement('tr');
      const rank = document.createElement('td'); rank.textContent = row.rank;
      const asn = document.createElement('td');
      const link = document.createElement('a'); link.textContent = `AS${row.asn}`;
      link.href = localUrl(`/view-asn?asn=${encodeURIComponent(row.asn)}&period=1d`);
      link.className = 'asn-metadata'; asn.append(link); metadataLabels.set(row.asn, link);
      item.append(rank,asn,volumeCell(row.in),volumeCell(row.out),volumeCell(row.total));
      item.className = 'asn-summary';
      const graphRow = document.createElement('tr'); graphRow.className = 'asn-graph';
      const traffic = document.createElement('td'); traffic.colSpan = 5;
      const imageLink = document.createElement('a'); imageLink.href = link.href;
      const image = document.createElement('img'); image.className = 'sparkline'; image.width = 840; image.height = 260;
      image.alt = `Traffic for AS${row.asn}`;
      const status = document.createElement('span'); status.className = 'traffic-status';
      imageLink.append(image); traffic.append(imageLink,status); graphRow.append(traffic);
      $('ranking-rows').append(item,graphRow);
      observeImage({image,status,generation:current,
        url:localUrl(`/api/asn/sparkline.svg?${new URLSearchParams({asn:row.asn,start:String(data.start),end:String(data.end),tz:browserTimezone,v:"5"})}`)});
    }
    $('top-period').textContent = `[${new Date(data.start*1000).toLocaleString()}, ${new Date(data.end*1000).toLocaleString()})`;
    $('ranking').hidden = data.rows.length === 0;
    $('top-status').textContent = data.rows.length ? '' : 'No data for the selected period';
    if (data.rows.length && window.AsnMetadata) cancelMetadata = window.AsnMetadata.load(data.rows.map(row => row.asn), records => {
      if (current !== generation) return;
      for (const [asn, element] of metadataLabels) element.textContent = window.AsnMetadata.label(asn, records.get(asn));
    });
  } catch (error) {
    if (current !== generation || (error.name === 'AbortError' && !timedOut)) return;
    $('top-status').textContent = timedOut ? 'Traffic ranking request timed out. Please try again.' : 'Unable to load traffic ranking. Please try again.';
  } finally {
    clearTimeout(timeout);
  }
});
async function loadLegend() {
  try {
    const response = await fetch(localUrl('/api/links'),{cache:'no-store'});
    if (!response.ok) throw new Error('links');
    for (const link of await response.json()) {
      const item = document.createElement('li');
      const color = document.createElement('span'); color.className = 'swatch'; color.style.backgroundColor = link.color;
      item.append(color,document.createTextNode(link.name)); $('link-legend').append(item);
    }
  } catch (error) { $('legend-status').textContent = 'Unable to load link legend'; }
}
loadLegend();
$('top-form').requestSubmit();

window.addEventListener('pagehide',resetImages);
