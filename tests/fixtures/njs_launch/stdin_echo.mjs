#!/usr/bin/env node
import { readFileSync } from "node:fs";
const text = readFileSync(0, "utf8");
process.stdout.write(text);
