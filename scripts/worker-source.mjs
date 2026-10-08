/** Shared production/test bundler. No regex removal of module imports. */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {build} from 'esbuild';

export const repositoryRoot = path.resolve(import.meta.dirname, '..');
const textTypes = {
  '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8',
};
const binaryTypes={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2'};
const excludedDirectories = new Set(['tests', 'test', '__tests__', 'docs', 'node_modules', 'private']);
const hash = content => crypto.createHash('sha256').update(content).digest('hex');

export async function loadWebAssets(directory = path.join(repositoryRoot, 'web')) {
  const assets = {};
  async function visit(current, prefix = '') {
    for (const item of await fs.readdir(current, {withFileTypes: true})) {
      if (item.name.startsWith('.')) continue;
      const relative = prefix + item.name;
      if (item.isDirectory()) {
        if (!excludedDirectories.has(item.name)) await visit(path.join(current, item.name), relative + '/');
      } else if (item.isFile() && (textTypes[path.extname(item.name)]||binaryTypes[path.extname(item.name)]) && !/\.(test|spec)\.[cm]?js$/.test(item.name)) {
        const extension=path.extname(item.name),binary=!!binaryTypes[extension];
        const bytes=await fs.readFile(path.join(current,item.name));
        assets[relative]={body:bytes.toString(binary?'base64':'utf8'),type:binaryTypes[extension]||textTypes[extension],sha256:hash(bytes),...(binary?{encoding:'base64'}:{})};
      }
    }
  }
  await visit(directory);
  if (!assets['index.html']) throw new Error('Frontend index.html missing');
  // Ship the browser entry's complete dependency graph. Its shared validators
  // live outside web/ and import JSON contracts; neither /edge URLs nor native
  // JSON import attributes are part of the public asset API. Bundle the actual
  // sources here so preview and production enforce exactly the same rules.
  if (assets['main.js']) {
    const frontend = await build({
      entryPoints: [path.join(directory, 'main.js')], bundle: true, write: false,
      format: 'esm', platform: 'browser', target: 'es2022', legalComments: 'none',
      logLevel: 'silent', metafile: true,
    });
    const output = frontend.outputFiles[0];
    if (Object.values(frontend.metafile.outputs).some(item => item.imports.length)) {
      throw new Error('Frontend entry has unresolved browser imports');
    }
    assets['main.js'] = {body: output.text, type: textTypes['.js'], sha256: hash(output.contents)};
  }
  // Any graph change invalidates entry URLs. Worker/iframe scripts remain
  // separate resources with their existing sandbox and CSP boundaries.
  const revision = hash(JSON.stringify(assets)).slice(0, 16);
  for (const [name, asset] of Object.entries(assets)) {
    if (name.endsWith('.html')) {
      asset.body = asset.body.replace(/((?:src|href)=["'])([^"'?]+\.(?:js|mjs|css))(?:\?[^"']*)?(["'])/g,
        (all, before, url, after) => /^(?:https?:|\/\/)/.test(url) ? all : before + url + '?v=' + revision + after);
    }
    if(asset.encoding!=='base64') asset.sha256 = hash(asset.body);
  }
  return assets;
}

export async function buildWorkerSource({assets = {}, catalog = {factors: [], models: [], templates: []}, presets = {}, buildId = 'test', wrapper = null} = {}) {
  const entry = wrapper
    ? {stdin: {contents: `import productionWorker from './edge/worker.mjs';\n${wrapper}`, resolveDir: repositoryRoot, sourcefile: 'worker-test-entry.mjs'}}
    : {entryPoints: [path.join(repositoryRoot, 'edge/worker.mjs')]};
  const result = await build({
    ...entry, bundle: true, write: false, format: 'esm', platform: 'browser', target: 'es2022',
    minify: false, legalComments: 'none', logLevel: 'silent',
    define: {WEB_ASSETS: JSON.stringify(assets), CATALOG: JSON.stringify(catalog), RESEARCH_PRESETS: JSON.stringify(presets), BUILD_ID: JSON.stringify(buildId)},
  });
  return result.outputFiles[0].text;
}
