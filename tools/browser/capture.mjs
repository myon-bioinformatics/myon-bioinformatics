import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, rename, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [engine, url, root, runId, readySelector] = process.argv.slice(2);
if (!['stagehand', 'playwright'].includes(engine) || !url || !root || !runId) {
  throw new Error('Usage: node capture.mjs <stagehand|playwright> <url> <output-directory> <run-id> [ready-selector]');
}

const project = fileURLToPath(new URL('../../', import.meta.url));
const sha = execFileSync('git', ['rev-parse', '--verify', 'HEAD'], { cwd: project, encoding: 'utf8' }).trim();
const executablePath = process.env.CHROME_BIN;
await mkdir(root, { recursive: true });
const directory = await mkdtemp(path.join(root, '.' + engine + '-'));
try {
  const screenshot = path.resolve(directory, 'screenshot.png');
  if (engine === 'stagehand') {
    const { localBrowser, Stagehand } = await import('@browserbasehq/stagehand');
    const browser = await localBrowser.launch({ headless: true, executablePath });
    try {
      const sdk = await Stagehand.create({ browser });
      try {
        const page = await browser.context.newPage();
        await page.goto(url);
        if (readySelector && !await page.waitForSelector(readySelector)) {
          throw new Error('Capture page did not become ready');
        }
        await page.screenshot({ path: screenshot });
      } finally {
        await sdk.close();
      }
    } finally {
      await browser.close();
    }
  } else {
    const { chromium } = await import('playwright');
    const browser = await chromium.launch({ executablePath });
    try {
      const page = await browser.newPage();
      await page.goto(url);
      if (readySelector) await page.waitForSelector(readySelector);
      await page.screenshot({ path: screenshot });
    } finally {
      await browser.close();
    }
  }
  execFileSync('python3', [path.join(project, 'browser_evidence.py'),
    '--engine', engine, '--root', directory, '--screenshot', screenshot, '--run-id', runId,
    '--head-sha', sha, '--output', path.join(directory, 'evidence.json')], { stdio: 'inherit' });
  // Publish the PNG and manifest together, only after both validate.
  await rename(directory, path.join(root, engine));
} finally {
  await rm(directory, { recursive: true, force: true });
}
