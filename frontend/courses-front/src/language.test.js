import assert from "node:assert/strict";
import test from "node:test";

import {
  LANGUAGE_STORAGE_KEY,
  normalizeLanguage,
  persistLanguage,
  readStoredLanguage,
} from "./language.js";


test("normalizeLanguage принимает поддерживаемый язык и региональную форму", () => {
  assert.equal(normalizeLanguage("en"), "en");
  assert.equal(normalizeLanguage("zh-CN"), "zh");
  assert.equal(normalizeLanguage("unknown"), "ru");
});

test("выбранный язык сохраняется и восстанавливается", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  assert.equal(persistLanguage("en-US", storage), "en");
  assert.equal(values.get(LANGUAGE_STORAGE_KEY), "en");
  assert.equal(readStoredLanguage(storage), "en");
});

test("ошибка localStorage безопасно возвращает русский язык", () => {
  const unavailableStorage = {
    getItem: () => {
      throw new Error("storage is unavailable");
    },
  };

  assert.equal(readStoredLanguage(unavailableStorage), "ru");
});

test("запрет доступа к самому свойству localStorage не ломает страницу", () => {
  const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("property access is blocked");
    },
  });

  try {
    assert.equal(readStoredLanguage(), "ru");
    assert.equal(persistLanguage("en"), "en");
  } finally {
    if (originalDescriptor) {
      Object.defineProperty(globalThis, "localStorage", originalDescriptor);
    } else {
      delete globalThis.localStorage;
    }
  }
});
