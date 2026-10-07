'use strict';
const fs = require('node:fs');
const zlib = require('node:zlib');
const source = fs.readFileSync(0, 'utf8');
if (source.length > 24 * 1024 * 1024) throw new Error('Input too large');
const rows = JSON.parse(source);
if (!Array.isArray(rows) || rows.length > 240) throw new Error('Invalid transcript batch');
const decoded = rows.map(row => {
    if (!Number.isInteger(row.size) || row.size < 1 || row.size > 4 * 1024 * 1024) throw new Error('Invalid event size');
    const input = Buffer.from(row.data, 'base64');
    if (input.length > 4 * 1024 * 1024) throw new Error('Compressed event too large');
    const output = zlib.zstdDecompressSync(input, {maxOutputLength: row.size});
    if (output.length !== row.size) throw new Error('Event size mismatch');
    return output.toString('utf8');
});
process.stdout.write(JSON.stringify(decoded));
