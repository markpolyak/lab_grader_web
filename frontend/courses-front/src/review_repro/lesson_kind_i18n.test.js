// frontend/courses-front/src/review_repro/lesson_kind_i18n.test.js
// Ревью PR #66: тип занятия (lesson.kind) выводится без перевода, в en/zh видно
// «Лабораторное занятие» / «Практическое занятие». Требование: «Интерфейс на трёх языках: ru, en, zh».
// 1) Рендерим настоящий QueueLessons (esbuild + react-dom/server, переводы проекта, язык en/zh).
//    Ответ GET /courses/{id}/lessons подставлен фикстурой, effect загрузки выполняется
//    один раз синхронно, сети нет. 2) Остальные экраны проверяем по исходникам.
import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = join(SRC, "..");
const QUEUE_LESSONS = join(SRC, "components", "queue-lessons", "index.jsx");
const RU_KIND = /Лабораторное занятие|Практическое занятие/g;

// Так отвечает GET /courses/os-2026/lessons (main.py, CourseLessonsResponse):
// занятие из выгрузки ГУАП (kind из schedule/suai/...) и внештатное, созданное
// преподавателем (kind="Практическое занятие" задаёт queues/usecase/queue_service.py).
const LESSONS = {
  schedule_id: "00000000-0000-0000-0000-000000000001", course_id: "os-2026",
  course_name: "Operating Systems", week_start: "2026-11-09", week_end: "2026-11-15",
  lessons: [
    { id: "00000000-0000-0000-0000-00000000000a", starts_at: "2026-11-09T15:10:00+03:00",
      ends_at: "2026-11-09T16:40:00+03:00", groups: ["4431"], kind: "Лабораторное занятие",
      room: null, cancelled: false, original_starts_at: null, queue: { closed: false, entries_count: 0 } },
    { id: "00000000-0000-0000-0000-00000000000b", starts_at: "2026-11-12T10:00:00+03:00",
      ends_at: "2026-11-12T11:30:00+03:00", groups: ["4431"], kind: "Практическое занятие",
      room: null, cancelled: false, original_starts_at: null, queue: { closed: false, entries_count: 0 } },
  ],
};

const stubs = {
  name: "queue-lessons-stubs",
  setup(b) {
    // Только для queue-lessons/index.jsx: API отдаёт фикстуру, useEffect выполняется сразу.
    b.onResolve({ filter: /^(react|\.\.\/\.\.\/api)$/ }, (args) =>
      args.importer === QUEUE_LESSONS ? { path: args.path, namespace: "stub" } : undefined);
    b.onLoad({ filter: /.*/, namespace: "stub" }, (args) => ({
      resolveDir: ROOT, loader: "js",
      contents: args.path === "react"
        ? `export { useState } from "react";
           const done = new Set();
           export function useEffect(effect) {
             const key = String(effect);
             if (!done.has(key)) { done.add(key); effect(); }
           }`
        : `const LESSONS = ${JSON.stringify(LESSONS)};
           const sync = (value) => ({ then: (ok) => sync(ok(value)), catch: () => sync(value),
                                       finally: (cb) => { cb(); return sync(value); } });
           export const fetchCourseLessons = () => sync(LESSONS);`,
    }));
  },
};

async function renderQueueLessons(language) {
  const out = await build({
    stdin: {
      contents: `
        import { renderToStaticMarkup } from "react-dom/server";
        import { MemoryRouter } from "react-router-dom";
        import i18n from "./src/i18n";
        import { QueueLessons } from "./src/components/queue-lessons";
        export async function render(language) {
          await i18n.changeLanguage(language);
          return { groups: i18n.t("queueLessons.groups"), html: renderToStaticMarkup(
            <MemoryRouter><QueueLessons courseId="os-2026" onBack={() => {}} /></MemoryRouter>) };
        }`,
      resolveDir: ROOT, loader: "jsx",
    },
    bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic",
    loader: { ".js": "jsx", ".json": "json" }, logLevel: "silent", mainFields: ["module", "main"],
    define: { "process.env.NODE_ENV": '"production"', "import.meta.env": "{}" }, plugins: [stubs],
  });
  const dir = mkdtempSync(join(tmpdir(), "lesson-kind-"));
  const file = join(dir, "bundle.cjs");
  writeFileSync(file, out.outputFiles[0].text);
  try { return await createRequire(import.meta.url)(file).render(language); }
  finally { rmSync(dir, { recursive: true, force: true }); }
}

for (const language of ["en", "zh"]) {
  test(`список занятий студента (${language}): тип занятия переведён`, async () => {
    const { groups, html } = await renderQueueLessons(language);
    // Страница действительно отрисована на нужном языке и с данными занятий.
    assert.ok(html.includes(`${groups}: 4431`), `на странице нет «${groups}: 4431»`);
    const shown = [...new Set(html.match(RU_KIND) ?? [])];
    assert.deepEqual(shown, [], `в ${language}-интерфейсе тип занятия по-русски: ${shown.join(", ")}`);
  });
}

test("очередь студента и админка: lesson.kind выводится через перевод", () => {
  const raw = [];
  for (const rel of ["components/queue-lessons/index.jsx", "components/queue-detail/index.jsx",
                     "components/admin/Queues/index.jsx"]) {
    readFileSync(join(SRC, rel), "utf8").split("\n").forEach((line, index) => {
      // JSX-выражение {lesson.kind} / {snapshot.lesson.kind}: поле выводится как есть, без t(...).
      if (/(?<![$\w])\{\s*[\w?.]*\.kind\s*\}/.test(line)) raw.push(`src/${rel}:${index + 1}`);
    });
  }
  assert.deepEqual(raw, [], "lesson.kind выводится как есть, без перевода");
});
