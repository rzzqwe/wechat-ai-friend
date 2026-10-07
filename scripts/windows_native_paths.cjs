// Project-scoped Windows acceleration. Keep real filesystem/junction resolution.
'use strict';
const fs = require('node:fs');
const { syncBuiltinESMExports } = require('node:module');
if (process.platform === 'win32' && process.env.WECHAT_NATIVE_REALPATH !== '0') {
    const original = fs.realpathSync;
    const native = original.native;
    if (typeof native === 'function') {
        const fastRealpath = function (file, options) {
            try { return native(file, options); }
            catch { return original(file, options); }
        };
        fastRealpath.native = native;
        fs.realpathSync = fastRealpath;
        syncBuiltinESMExports();
    }
}
