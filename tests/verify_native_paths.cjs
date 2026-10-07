'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const original = fs.realpathSync;
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wechat-native-paths-'));
assert.ok(path.resolve(root).startsWith(path.resolve(os.tmpdir()) + path.sep));
try {
    const first = path.join(root, 'first');
    const second = path.join(root, 'second');
    fs.mkdirSync(first); fs.mkdirSync(second);
    const file = path.join(first, 'test.txt');
    fs.writeFileSync(file, 'fixture');
    const junction = path.join(root, 'junction');
    fs.symlinkSync(first, junction, process.platform === 'win32' ? 'junction' : 'dir');
    const inputs = [root, first, file, junction, path.join(junction, 'test.txt')];
    const expected = inputs.map(value => original(value));
    require('../scripts/windows_native_paths.cjs');
    const normalized = value => path.normalize(String(value)).toLowerCase();
    inputs.forEach((value, i) => assert.equal(normalized(fs.realpathSync(value)), normalized(expected[i])));
    assert.ok(Buffer.isBuffer(fs.realpathSync(file, {encoding: 'buffer'})));
    assert.throws(() => fs.realpathSync(path.join(root, 'missing')), {code: 'ENOENT'});
    fs.unlinkSync(junction);
    fs.symlinkSync(second, junction, process.platform === 'win32' ? 'junction' : 'dir');
    assert.equal(normalized(fs.realpathSync(junction)), normalized(original(second)));
    console.log('Native paths: regular files, junctions, changed targets, buffers and missing files PASS');
} finally {
    fs.rmSync(root, {recursive: true, force: true});
}
