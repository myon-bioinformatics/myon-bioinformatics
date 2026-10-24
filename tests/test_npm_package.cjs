'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');
const test = require('node:test');
const root = path.resolve(__dirname, '..');
const manifest = require(path.join(root, 'package.json'));

test('Git package entry point and minimal packed files', () => {
  assert.equal(manifest.private, true);
  assert.equal(manifest.engines.node, '>=24');
  assert.deepEqual(manifest.files, ['py_launch.cjs', 'LICENSE']);
  assert.equal(manifest.exports['./py_launch'], './py_launch.cjs');
  assert.deepEqual(Object.keys(manifest.dependencies || {}), []);
  assert.equal(typeof require(path.join(root, 'py_launch.cjs')).run, 'function');
});

test('packed Node module installs into independent consumer offline', (t) => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'npm-parent-portable-'));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  const output = cp.execFileSync('npm', ['pack', '--json', '--ignore-scripts', '--pack-destination', tmp],
    { cwd: root, encoding: 'utf8' });
  const packed = JSON.parse(output);
  assert.equal(packed.length, 1);
  const contents = packed[0].files.map(file => file.path);
  for (const name of ['py_launch.cjs', 'package.json', 'LICENSE']) assert(contents.includes(name), name);
  assert(!contents.some(name => /^(vendor|tests|.github)\//.test(name)));
  const consumer = path.join(tmp, 'consumer');
  fs.mkdirSync(consumer);
  fs.writeFileSync(path.join(consumer, 'package.json'),
    JSON.stringify({ name: 'npm-parent-consumer-test', version: '0.0.0', private: true }));
  cp.execFileSync('npm', ['install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
    '--prefix', consumer, path.join(tmp, packed[0].filename)], { encoding: 'utf8' });
  // Node's package self-reference can resolve our local source from tests/.
  // Execute from the independent consumer to prove the installed export works.
  const probe = 'const p=require.resolve("@myon-bioinformatics/portable-tools/py_launch");' +
    'const m=require(p);if(typeof m.run!=="function")process.exit(3);' +
    'if(!p.includes("node_modules"))process.exit(4);';
  cp.execFileSync(process.execPath, ['-e', probe], { cwd: consumer });
});
