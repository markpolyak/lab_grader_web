export const SUPPORTED_LANGUAGES = [
  { code: "ru", label: "Русский" },
  { code: "en", label: "English" },
  { code: "zh", label: "中文" },
];

export const LANGUAGE_STORAGE_KEY = "lab-grader-language";

export function normalizeLanguage(value) {
  // i18next и браузер иногда возвращают региональную форму (например, ru-RU).
  // Для интерфейса достаточно основной части, если такой перевод существует.
  const baseLanguage = typeof value === "string" ? value.split("-")[0] : "";
  return SUPPORTED_LANGUAGES.some(({ code }) => code === baseLanguage)
    ? baseLanguage
    : "ru";
}

function getBrowserStorage() {
  try {
    return globalThis.localStorage;
  } catch {
    // Некоторые privacy-режимы запрещают даже чтение свойства localStorage.
    return null;
  }
}

export function readStoredLanguage(storage) {
  const effectiveStorage = storage === undefined ? getBrowserStorage() : storage;
  try {
    return normalizeLanguage(effectiveStorage?.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    // Запрещённый localStorage не должен мешать открытию страницы студента.
    return "ru";
  }
}

export function persistLanguage(language, storage) {
  const normalizedLanguage = normalizeLanguage(language);
  const effectiveStorage = storage === undefined ? getBrowserStorage() : storage;
  try {
    effectiveStorage?.setItem(LANGUAGE_STORAGE_KEY, normalizedLanguage);
  } catch {
    // В приватном режиме хранилище может быть недоступно: перевод всё равно
    // действует до закрытия страницы благодаря внутреннему состоянию i18next.
  }
  return normalizedLanguage;
}
