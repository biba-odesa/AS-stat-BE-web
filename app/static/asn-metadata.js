// Metadata is independent of traffic rendering; polling never starts a refresh.
window.AsnMetadata = (() => {
  function label(asn, record) {
    let text = `AS${asn}`;
    if (record?.name) text += ` — ${record.name}`;
    if (record?.name && record?.country) text += ` · ${record.country}`;
    return text;
  }
  function load(asns, apply) {
    const abort = new AbortController();
    let timer;
    const deadline = Date.now() + 60000;
    let stopped = false;
    async function request(poll) {
      if (stopped || Date.now() >= deadline) return;
      try {
        const response = await fetch(`/api/asn/metadata${poll ? '/status' : ''}`, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({asns}), cache:'no-store', signal:abort.signal
        });
        if (!response.ok || stopped) return;
        const data = await response.json();
        if (stopped) return;
        const records = new Map(data.records.map(record => [record.asn, record]));
        apply(records);
        if (data.records.some(record => record.updating) && Date.now() + 2000 < deadline) {
          timer = setTimeout(() => request(true), 2000);
        }
      } catch (error) {
        // Metadata failures never remove traffic data or expose external service details.
      }
    }
    const deadlineTimer = setTimeout(() => {stopped=true;abort.abort();clearTimeout(timer);},60000);
    request(false);
    return () => {stopped=true;abort.abort();clearTimeout(timer);clearTimeout(deadlineTimer);};
  }
  return {label,load};
})();
