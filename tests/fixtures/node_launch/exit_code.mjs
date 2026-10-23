#!/usr/bin/env node
const code = Number.parseInt(process.argv[2] || "0", 10);
process.exit(Number.isFinite(code) ? code : 1);
