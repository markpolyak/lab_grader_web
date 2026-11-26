import assert from "node:assert/strict";
import test from "node:test";

import { nextSort, readSortCookie, sortCookie, sortQuizzes, SORT_COOKIE } from "./sort.js";

const quizzes = [
  { id: "q10", title: "Квиз 10", short_name: "К10", questions: 5, max_points: "10,5", seconds_per_question: 20 },
  { id: "q2", title: "квиз 2", short_name: "", questions: 12, max_points: "2", seconds_per_question: 30 },
  { id: "broken", error: "нет вопросов" },
  { id: "a", title: "Атомарность", short_name: "А", questions: 8, max_points: "10", seconds_per_question: 15 },
];
const ids = (list) => list.map((q) => q.id);

test("без сортировки порядок сервера сохраняется", () => {
  assert.deepEqual(ids(sortQuizzes(quizzes, null)), ["q10", "q2", "broken", "a"]);
});

test("название: естественный порядок чисел, без учёта регистра; квиз с ошибкой — по id", () => {
  // Русская сортировка: кириллица раньше латиницы.
  assert.deepEqual(ids(sortQuizzes(quizzes, { column: "title", direction: "asc" })), ["a", "q2", "q10", "broken"]);
});

test("числа сравниваются как числа, в том числе с запятой", () => {
  assert.deepEqual(ids(sortQuizzes(quizzes, { column: "max_points", direction: "asc" })), ["q2", "a", "q10", "broken"]);
  assert.deepEqual(ids(sortQuizzes(quizzes, { column: "questions", direction: "desc" })), ["q2", "a", "q10", "broken"]);
});

test("пустые значения — в конце при любом направлении", () => {
  for (const direction of ["asc", "desc"]) {
    const sorted = ids(sortQuizzes(quizzes, { column: "short_name", direction }));
    assert.deepEqual(sorted.slice(-2).sort(), ["broken", "q2"]);
  }
});

test("исходный массив не меняется", () => {
  const copy = [...quizzes];
  sortQuizzes(quizzes, { column: "title", direction: "desc" });
  assert.deepEqual(quizzes, copy);
});

test("щелчок по тому же столбцу меняет направление, по другому — по возрастанию", () => {
  assert.deepEqual(nextSort(null, "title"), { column: "title", direction: "asc" });
  assert.deepEqual(nextSort({ column: "title", direction: "asc" }, "title"), { column: "title", direction: "desc" });
  assert.deepEqual(nextSort({ column: "title", direction: "desc" }, "title"), { column: "title", direction: "asc" });
  assert.deepEqual(nextSort({ column: "title", direction: "desc" }, "questions"), { column: "questions", direction: "asc" });
});

test("cookie: запись и чтение", () => {
  const cookie = sortCookie({ column: "max_points", direction: "desc" });
  assert.match(cookie, /Path=\//);
  assert.match(cookie, /Max-Age=\d+/);
  const pair = cookie.split(";")[0];
  assert.deepEqual(readSortCookie(`other=1; ${pair}; x=y`), { column: "max_points", direction: "desc" });
});

test("cookie: неизвестные значения игнорируются", () => {
  assert.equal(readSortCookie(""), null);
  assert.equal(readSortCookie(undefined), null);
  assert.equal(readSortCookie(`${SORT_COOKIE}=id.asc`), null);
  assert.equal(readSortCookie(`${SORT_COOKIE}=title.sideways`), null);
  assert.equal(readSortCookie(`${SORT_COOKIE}=garbage`), null);
});
