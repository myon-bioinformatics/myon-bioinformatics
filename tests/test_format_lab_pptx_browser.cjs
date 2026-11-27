"use strict";

// Run against a native-table.md benchmark generated with --preview --table-slide 2.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { pathToFileURL } = require("node:url");

const resultDir = process.env.FORMAT_LAB_PPTX_RESULT;

test("PPTX evidence needs a complete, hash-matching set of rendered images", {
  skip: resultDir ? false : "Set FORMAT_LAB_PPTX_RESULT to a rendered native-table benchmark directory",
}, async () => {
  const { chromium } = require(process.env.PLAYWRIGHT_NODEJS_PATH || "playwright");
  const source = fs.readFileSync(path.join(__dirname, "fixtures/format-lab/native-table.md"), "utf8");
  const report = JSON.parse(fs.readFileSync(path.join(resultDir, "benchmark.json"), "utf8"));
  assert.equal(report.rendering.status, "passed");
  assert.equal(report.preview_pages.length, 2);
  const images = report.preview_pages.map(entry => path.join(resultDir, entry.file));
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH });
  try {
    const page = await browser.newPage();
    const index = pathToFileURL(path.join(__dirname, "../tools/format-lab/index.html")).href;
    await page.goto(index);
    await page.locator("#src").fill(source);

    async function importReport(record) {
      await page.locator('[data-tab="editor"]').click();
      await page.locator("#pptx-result").setInputFiles({
        name: "benchmark.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(record)),
      });
      await page.locator("#deck-summary").waitFor({ state: "visible" });
    }

    await importReport({ ...report, rendering: { status: "not_run", requested: false }, preview_pages: [] });
    assert.match(await page.locator("#deck-summary").textContent(), /最終描画 未実施/);
    assert.equal(await page.locator("#deck-preview").isVisible(), false);

    await importReport({ ...report, schema: "format-lab-pptx/1", preview_error: "missing renderer" });
    assert.match(await page.locator("#deck-summary").textContent(), /構造検査 成功.*最終描画 失敗.*終了コード 0/);
    assert.equal(await page.locator("#deck-preview").isVisible(), false);

    await importReport({ ...report, preview_pages: report.preview_pages.slice(0, 1) });
    assert.match(await page.locator("#deck-summary").textContent(), /最終描画 ページ数不一致/);
    assert.equal(await page.locator("#deck-preview").isVisible(), false);

    await importReport(report);
    assert.equal(await page.locator("#deck-status").textContent(), "PNG未照合");

    const picker = page.locator("#deck-images");
    await picker.setInputFiles(images.slice(0, 1));
    await page.getByText(/PNGページ不足/).waitFor();
    assert.equal(await page.locator("#deck-pages img").count(), 0);

    await picker.setInputFiles([
      { name: path.basename(images[0]), mimeType: "image/png", buffer: fs.readFileSync(images[0]) },
      { name: path.basename(images[1]), mimeType: "image/png", buffer: fs.readFileSync(images[0]) },
    ]);
    await page.getByText(/PNGのSHA-256不一致/).waitFor();
    assert.equal(await page.locator("#deck-pages img").count(), 0);

    await picker.setInputFiles(images.slice().reverse());
    await page.getByText("全2ページのPNGを照合済み").waitFor();
    assert.deepEqual(await page.locator("#deck-pages figcaption").allTextContents(), ["1 / 2", "2 / 2"]);
  } finally {
    await browser.close();
  }
});
