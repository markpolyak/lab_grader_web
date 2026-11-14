// Ревью PR #66: у преподавателя в таблице очереди должно быть видно, когда студент записался.
// Заказчик, #62, 05.11 17:33: «Преподавателю нужно видеть, кто ждёт, по порядку:
// ФИО, GitHub, лабораторная, когда записался».
// Запуск из frontend/courses-front: node --test src/review_repro/queue_signup_time.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

// Собираем настоящий QueueTable с настоящими переводами src/i18n и рендерим в HTML.
async function renderQueueTable(props, lng = "ru") {
  const dir = mkdtempSync(join(tmpdir(), "queue-table-"));
  try {
    const entry = join(dir, "entry.jsx");
    writeFileSync(entry, `
      import i18n from ${JSON.stringify(join(SRC, "i18n.js"))};
      import { renderToStaticMarkup } from "react-dom/server";
      import { QueueTable } from ${JSON.stringify(join(SRC, "components/queue-table/index.jsx"))};
      export function render(props, lng) { i18n.changeLanguage(lng); return renderToStaticMarkup(<QueueTable {...props} />); }
    `);
    const outfile = join(dir, "bundle.mjs");
    await build({
      entryPoints: [entry], outfile, bundle: true, platform: "node", format: "esm", jsx: "automatic",
      loader: { ".js": "jsx", ".png": "file", ".svg": "file" }, logLevel: "silent",
      nodePaths: [join(SRC, "..", "node_modules")], mainFields: ["module", "main"],
      banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
    });
    const { render } = await import(pathToFileURL(outfile).href);
    return render(props, lng);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// Записи в форме GET /admin/courses/{id}/queues/{queue_id} -> entries[]
// (queues/api/presenters.py:55 joined_at=entry.enqueued_time). Время по Москве, как и время занятия в админке.
const entries = [
  { id: "a", position: 1, github: "petrov", full_name: "Петров Пётр", lab_id: "1", lab_name: "ЛР1", status: "called", absent: false, result: null, joined_at: "2026-11-09T14:56:00+03:00", called_at: "2026-11-09T15:10:00+03:00", finished_at: null },
  { id: "b", position: 2, github: "ivanov", full_name: "Иванов Иван", lab_id: "2", lab_name: "ЛР2", status: "waiting", absent: false, result: null, joined_at: "2026-11-09T15:02:00+03:00", called_at: null, finished_at: null },
  { id: "c", position: 3, github: "sidorova", full_name: "Сидорова Анна", lab_id: "3", lab_name: "ЛР3", status: "waiting", absent: false, result: null, joined_at: "2026-11-09T15:07:00+03:00", called_at: null, finished_at: null },
];

const headersOf = (html) => [...html.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);
const rowsOf = (html) => [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)]
  .map((m) => [...m[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)]
    .map((cell) => cell[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()))
  .filter((cells) => cells.length > 0);

for (const [lng, header] of [["ru", "Время записи (МСК)"], ["en", "Joined at (MSK)"], ["zh", "报名时间（莫斯科时间）"]]) {
  test(`signup time column is translated: ${lng}`, async () => {
    const html = await renderQueueTable({ entries, onFinish() {} }, lng);
    assert.ok(headersOf(html).includes(header));
  });
}

test("signup time uses Moscow time for UTC timestamps and works in student view", async () => {
  const html = await renderQueueTable({ entries: [{ ...entries[1], joined_at: "2026-11-09T12:02:00Z" }] });
  assert.equal(rowsOf(html)[0][4], "15:02");
});

test("missing and invalid signup times use a placeholder", async () => {
  const html = await renderQueueTable({ entries: [null, undefined, "invalid", ""].map((joined_at, i) => ({ ...entries[0], id: String(i), joined_at })) });
  assert.deepEqual(rowsOf(html).map((row) => row[4]), ["—", "—", "—", "—"]);
});

for (const admin of [false, true]) {
  test(`empty table spans all columns: admin=${admin}`, async () => {
    const html = await renderQueueTable({ entries: [], ...(admin ? { onFinish() {} } : {}) });
    assert.ok(html.includes(`colSpan="${headersOf(html).length}"`));
  });
}

test("админка: у каждого ожидающего в таблице очереди видно, когда он записался", async () => {
  // Те же props, что передаёт страница преподавателя admin/Queues/index.jsx:182.
  const html = await renderQueueTable({ entries, onFinish() {}, onAbsent() {} });
  const headers = headersOf(html);
  const rows = rowsOf(html);
  for (const [name, time] of [["Иванов Иван", "15:02"], ["Сидорова Анна", "15:07"]]) {
    const row = rows.find((cells) => cells.some((cell) => cell.includes(name)));
    assert.ok(row, `строка «${name}» не отрисована`);
    assert.ok(row.join(" | ").includes(time),
      `время записи ${name} (joined_at ${time}) не выводится. Заголовки: ${JSON.stringify(headers)}; строка: «${row.join(" | ")}»`);
  }
});
