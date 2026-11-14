#!/usr/bin/env node
/* Capture the actual sandbox UI. No reset, grades, attendance or schedule writes. */
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
function option(name, fallback) {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
}
if (args.includes('--help')) {
  console.log('node scripts/capture_queue_screenshots.cjs [--frontend http://localhost:5180] [--backend http://localhost:8010] [--channel chrome|chromium]');
  process.exit(0);
}
const frontend = option('--frontend', 'http://localhost:5180');
const backend = option('--backend', 'http://localhost:8010');
for (const address of [frontend, backend]) {
  const url = new URL(address);
  if (url.protocol !== 'http:' || url.hostname !== 'localhost') {
    throw new Error('Используется только локальная песочница: http://localhost:<порт>');
  }
}
const output = path.join(root, 'docs/assets/screenshots');
async function main() {
  const load = createRequire(path.join(root, 'data/readme-capture/package.json'));
  const { chromium } = load('playwright');
  const channel = option('--channel', 'chrome');
  const browser = await chromium.launch(channel === 'chromium' ? {} : { channel });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1,
      locale: 'ru-RU', timezoneId: 'Europe/Moscow', reducedMotion: 'reduce',
    });
    // Refuse a production endpoint even if its URL happens to be localhost.
    const login = await context.request.post(`${backend}/admin/login`, {
      data: { login: 'sandbox', password: 'sandbox123' },
    });
    if (!login.ok()) throw new Error('Вход sandbox/sandbox123 не прошёл. Проверь порты песочницы.');
    const state = await context.request.get(`${backend}/sandbox/state`);
    const course = await context.request.get(`${backend}/courses/queue-sandbox`);
    if (!state.ok() || !course.ok()) throw new Error('На выбранном backend нет песочницы; съёмка остановлена.');
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    async function capture(filename, locator) {
      await page.evaluate(() => document.fonts.ready);
      // Two paint frames after data arrives, with CSS animations disabled by screenshot().
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await locator.screenshot({ path: path.join(output, filename), animations: 'disabled' });
      console.log(`Сохранено: docs/assets/screenshots/${filename}`);
    }
    fs.mkdirSync(output, { recursive: true });
    await page.goto(`${frontend}/course/queue-sandbox/group/DEMO-01/lab/1`);
    await page.evaluate(() => {
      sessionStorage.setItem('queue.identity.["queue-sandbox","DEMO-01"]', JSON.stringify({
        surname: 'Иванов', name: 'Иван', patronymic: 'Иванович', github: 'demo-ivan',
      }));
    });
    await page.reload();
    await page.getByRole('tab', { name: 'Список на занятие', exact: true }).click();
    await page.getByText('Иванов Иван Иванович', { exact: true }).first().waitFor();
    await capture('student.png', page.locator('.queue-registration'));

    await page.goto(`${frontend}/admin/courses/queue-sandbox/queue`);
    await page.locator('.queue-admin').waitFor();
    const group = page.locator('.queue-filters select').nth(1);
    await group.selectOption('DEMO-01');
    const session = page.locator('.queue-filters select').nth(2);
    await session.locator('option[value="DEMO-01-nearest"]').waitFor({ state: 'attached' });
    await session.selectOption('DEMO-01-nearest');
    await page.getByText('Иванов Иван Иванович', { exact: true }).first().waitFor();
    await capture('teacher.png', page.locator('.queue-admin'));
    await page.getByRole('button', { name: 'Расписание', exact: true }).click();
    await page.locator('.queue-content table').first().waitFor();
    await capture('schedule.png', page.locator('.queue-admin'));
    await context.close();
  } finally {
    await browser.close();
  }
}
main().catch(error => {
  console.error(`Съёмка не завершена: ${error.message}`);
  process.exitCode = 1;
});
