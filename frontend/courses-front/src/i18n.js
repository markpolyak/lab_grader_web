import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import translationEN from "./locales/en/translation.json";
import translationRU from "./locales/ru/translation.json";
import translationZh from "./locales/zh/translation.json";
import { persistLanguage, readStoredLanguage } from "./language";


import queueEN from "./locales/en/queue.json";
import queueRU from "./locales/ru/queue.json";
import queueZH from "./locales/zh/queue.json";

const resources = {
  en: { queue: queueEN, translation: translationEN },
  ru: { queue: queueRU, translation: translationRU },
  zh: { queue: queueZH, translation: translationZh },
};

i18n.use(initReactI18next).init({
  resources,
  lng: readStoredLanguage(),
  fallbackLng: "ru",
  interpolation: {
    escapeValue: false,
  },
});

function applyDocumentLanguage(language) {
  if (typeof document !== "undefined") {
    document.documentElement.lang = persistLanguage(language);
  }
}

// Сохраняем язык и синхронизируем атрибут html[lang], чтобы скринридеры и
// браузерные переводчики правильно определяли язык после каждого переключения.
i18n.on("languageChanged", applyDocumentLanguage);
applyDocumentLanguage(i18n.language);

export default i18n;
