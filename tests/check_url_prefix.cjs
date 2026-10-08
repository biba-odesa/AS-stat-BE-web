// Reuse the actual frontend checks with a nonempty server-provided URL base.
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
for (const name of ['check_frontend.cjs','check_top_frontend.cjs','check_link_usage_frontend.cjs','check_metadata_frontend.cjs']) {
  let source = fs.readFileSync(`tests/${name}`, 'utf8');
  source = source.replaceAll('/api/', '/asstat2/api/').replaceAll('/view-asn?', '/asstat2/view-asn?');
  source = source.replaceAll("require('../app/", "require('./app/");
  source = source.replaceAll(String.raw`\/api\/`, String.raw`\/asstat2\/api\/`);
  const injection = 'context.window.ASStat={basePath:"/asstat2",url(path){return this.basePath+path;}};\n';
  source = source.replace('vm.runInContext(', injection+'vm.runInContext(');
  const result = spawnSync(process.execPath, ['-e',source], {encoding:'utf8'});
  if (result.status !== 0) {process.stderr.write(result.stdout+result.stderr);process.exit(1);}
}
console.log('Prefixed frontend API URLs, View ASN links, image queues and metadata polling passed');
