// Direct HTTP image loading with bounded concurrency and cancellable queues.
window.SvgImageQueue = class {
  constructor(limit) {this.limit=limit;this.queue=[];this.active=new Set();this.paused=false;}
  enqueue(job) {this.queue.push(job);this.pump();}
  reset() {
    this.paused=true;this.queue.length=0;
    for (const task of [...this.active]) {
      task.abort.abort();
      task.job.image.onload=null;task.job.image.onerror=null;
      task.job.image.removeAttribute('src');task.finish();
    }
    this.paused=false;
  }
  pump() {
    while (!this.paused && this.active.size < this.limit && this.queue.length) {
      const job=this.queue.shift();
      if (job.valid && !job.valid()) continue;
      const task={job,abort:new AbortController(),finished:false};
      task.finish=() => {
        if (task.finished) return;
        task.finished=true;job.image.onload=null;job.image.onerror=null;
        this.active.delete(task);this.pump();
      };
      this.active.add(task);job.status.textContent='Loading…';
      job.image.onload=async () => {
        try {
          const message=job.loaded ? await job.loaded(task.abort.signal) : '';
          if (!task.finished) job.status.textContent=message || '';
        } catch (error) {
          if (!task.finished && error.name !== 'AbortError') job.status.textContent='Unable to load link legend';
        } finally {task.finish();}
      };
      job.image.onerror=() => {job.status.textContent='Image unavailable';task.finish();};
      job.image.src=job.url;
    }
  }
};
