const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const code = fs.readFileSync(path.join(root, 'web/static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'web/static/index.html'), 'utf8');
const idMatches = [...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
const htmlIds = new Set(idMatches);

const jsConsts = [...code.matchAll(/const (\w+)\s*=\s*document\.getElementById\("([^"]+)"\)/g)];
jsConsts.forEach(m => {
  const varName = m[1], id = m[2];
  if (!htmlIds.has(id)) console.log('MISSING:', id, ' => var:', varName);
});
console.log("Done. HTML has", htmlIds.size, "IDs.");
