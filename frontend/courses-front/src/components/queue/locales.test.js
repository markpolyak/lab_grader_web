import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import i18next from "i18next";

import { ENTRY_STATUSES, QUEUE_ERROR_CODES, SESSION_KINDS } from "./state.js";
import { ADMIN_ERROR_CODES, OUTCOMES, adminErrorKeys } from "../admin/Queue/state.js";

import ru from "../../locales/ru/translation.json" with { type: "json" };
import en from "../../locales/en/translation.json" with { type: "json" };
import zh from "../../locales/zh/translation.json" with { type: "json" };

const LOCALES = { ru, en, zh };

// Сравниваются только ключи очереди: в остальных zh и так расходится с ru.
const SUBTREES = ["queue", "adminQueue"];

function flatten(node, prefix = "") {
  return Object.entries(node).flatMap(([key, value]) =>
    value && typeof value === "object"
      ? flatten(value, `${prefix}${key}.`)
      : [[`${prefix}${key}`, value]]
  );
}

function lookup(translations, key) {
  return key.split(".").reduce((node, part) => (node ? node[part] : undefined), translations);
}

const placeholders = (text) => [...String(text).matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort();


test("ключи queue.* и adminQueue.* совпадают во всех языках", () => {
  for (const subtree of SUBTREES) {
    const reference = flatten(ru[subtree]).map(([key]) => key).sort();
    for (const [lang, translations] of Object.entries(LOCALES)) {
      assert.ok(translations[subtree], `${lang}: нет ${subtree}`);
      const keys = flatten(translations[subtree]).map(([key]) => key).sort();
      assert.deepEqual(keys, reference, `${lang}.${subtree}`);
    }
  }
});

test("переводы непустые и с теми же параметрами, что в русском", () => {
  for (const subtree of SUBTREES) {
    const reference = Object.fromEntries(flatten(ru[subtree]));
    for (const [lang, translations] of Object.entries(LOCALES)) {
      for (const [key, value] of flatten(translations[subtree])) {
        assert.equal(typeof value, "string", `${lang}.${subtree}.${key}`);
        assert.ok(value.trim(), `${lang}.${subtree}.${key} пустой`);
        assert.deepEqual(placeholders(value), placeholders(reference[key]), `${lang}.${subtree}.${key}`);
      }
    }
  }
});

test("у каждого кода ошибки есть перевод", () => {
  for (const [lang, translations] of Object.entries(LOCALES)) {
    for (const code of QUEUE_ERROR_CODES) {
      assert.ok(lookup(translations, `queue.errors.${code}`), `${lang}: queue.errors.${code}`);
    }
  }
});

test("у каждого типа сессии, статуса записи и исхода есть перевод", () => {
  for (const [lang, translations] of Object.entries(LOCALES)) {
    for (const kind of SESSION_KINDS) {
      assert.ok(lookup(translations, `queue.session.kind.${kind}`), `${lang}: kind ${kind}`);
    }
    for (const status of [...ENTRY_STATUSES, "unknown"]) {
      assert.ok(lookup(translations, `queue.status.${status}`), `${lang}: status ${status}`);
    }
    for (const outcome of OUTCOMES) {
      assert.ok(lookup(translations, `adminQueue.outcome.${outcome}`), `${lang}: outcome ${outcome}`);
    }
    for (const code of ["FETCH_FAILED", "BAD_PAGE", "NO_LESSONS", "SNAPSHOT_TOO_OLD"]) {
      assert.ok(lookup(translations, `adminQueue.schedule.errors.${code}`), `${lang}: schedule ${code}`);
    }
    // кнопки страниц истории (getItemAriaLabel в HistoryPanel)
    for (const type of ["first", "previous", "next", "last"]) {
      assert.ok(lookup(translations, `adminQueue.history.page.${type}`), `${lang}: history page ${type}`);
    }
  }
});

test("каждый ключ очереди, упомянутый в компонентах, есть во всех языках", () => {
  const sources = [
    ...readdirSync(new URL("./", import.meta.url)).map((name) => new URL(`./${name}`, import.meta.url)),
    ...readdirSync(new URL("../admin/Queue/", import.meta.url)).map(
      (name) => new URL(`../admin/Queue/${name}`, import.meta.url)
    ),
    new URL("../group-list/index.jsx", import.meta.url),
    new URL("../lab-list/index.jsx", import.meta.url),
    new URL("../course-list/index.jsx", import.meta.url),
    new URL("../registration-form/index.jsx", import.meta.url),
  ].filter((url) => /\.jsx?$/.test(url.pathname) && !url.pathname.endsWith(".test.js"));

  const used = new Set();
  for (const url of sources) {
    const text = readFileSync(url, "utf-8");
    for (const match of text.matchAll(/\bt\(\s*"((?:queue|adminQueue)\.[A-Za-z0-9_.]+)"/g)) {
      used.add(match[1]);
    }
  }
  assert.ok(used.size > 50, `найдено слишком мало ключей: ${used.size}`);

  for (const [lang, translations] of Object.entries(LOCALES)) {
    for (const key of used) {
      assert.equal(typeof lookup(translations, key), "string", `${lang}: ${key}`);
    }
  }
});


test("блок очереди назван как страница очереди, а подписи списков - про сдачу из GitHub", () => {
  for (const [lang, translations] of Object.entries(LOCALES)) {
    const { entry } = translations.queue;
    assert.equal(entry.title, translations.queue.title, lang);
    for (const key of ["groupsHint", "labsHint"]) {
      assert.match(entry[key], /GitHub/, `${lang}.queue.entry.${key}`);
    }
  }
});


test("у каждого кода ошибки для преподавателя есть свой перевод", () => {
  for (const [lang, translations] of Object.entries(LOCALES)) {
    for (const code of ADMIN_ERROR_CODES) {
      assert.equal(typeof lookup(translations, `adminQueue.errors.${code}`), "string", `${lang}: adminQueue.errors.${code}`);
    }
  }
});

test("админка переводит коды текстом для преподавателя, остальные - студенческим", async () => {
  const i18n = i18next.createInstance();
  await i18n.init({
    lng: "ru",
    fallbackLng: "ru",
    resources: Object.fromEntries(Object.entries(LOCALES).map(([lang, translation]) => [lang, { translation }])),
  });
  for (const lang of Object.keys(LOCALES)) {
    await i18n.changeLanguage(lang);
    const tr = LOCALES[lang];
    for (const code of ADMIN_ERROR_CODES) {
      assert.equal(i18n.t(adminErrorKeys(code)), tr.adminQueue.errors[code], `${lang}: ${code}`);
    }
    assert.equal(i18n.t(adminErrorKeys("STORAGE_UNAVAILABLE")), tr.queue.errors.STORAGE_UNAVAILABLE, lang);
    assert.equal(i18n.t(adminErrorKeys("SOMETHING_NEW")), tr.queue.errors.unknown, lang);
  }
  // «Вернуть в очередь» студенту, который уже снова записался: не «Вы уже стоите в очереди»
  await i18n.changeLanguage("ru");
  const text = i18n.t(adminErrorKeys("ALREADY_IN_QUEUE"));
  assert.match(text, /^Студент уже снова стоит в очереди/);
  assert.doesNotMatch(text, /Вы уже/);
});

test("тексты ошибки периода, обрезанной истории и архивного курса", () => {
  assert.match(ru.adminQueue.errors.INVALID_PERIOD, /дата «С» позже даты «По»/);
  assert.match(ru.adminQueue.history.truncated, /Показаны первые \{\{count\}\} записей/);
  assert.match(ru.adminQueue.archived, /^Курс не активен/);
  assert.doesNotMatch(ru.adminQueue.archived, /выключена в конфигурации/);
  for (const [lang, translations] of Object.entries(LOCALES)) {
    assert.deepEqual(placeholders(translations.adminQueue.archived), ["status"], lang);
  }
});
