#!/usr/bin/env node
if (process.stdin.destroy) {
  process.stdin.destroy();
} else {
  process.stdin.resume();
}
process.exit(0);
