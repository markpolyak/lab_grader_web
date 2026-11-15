import assert from "node:assert/strict";
import test from "node:test";
import i18next from "i18next";
import { readFileSync } from "node:fs";
import { translateLessonKind } from "./lessonKind.js";

for (const language of ["ru", "en", "zh"]) {
  test(`lesson kinds and aliases (${language})`, async () => {
    const dictionary = JSON.parse(readFileSync(new URL(`./locales/${language}/translation.json`, import.meta.url)));
    const i18n = i18next.createInstance();
    await i18n.init({ lng: language, fallbackLng: false, resources: { [language]: { translation: dictionary } } });
    for (const alias of ["Лабораторное занятие", "Лабораторная работа", " ЛР. ", "ЛБ"]) {
      assert.equal(translateLessonKind(alias, i18n.t), dictionary.lessonKinds.laboratory);
    }
    for (const alias of ["Практическое занятие", "Практическая работа", "Практика", "ПР", "ПЗ."]) {
      assert.equal(translateLessonKind(alias, i18n.t), dictionary.lessonKinds.practical);
    }
    assert.equal(translateLessonKind("Seminar", i18n.t), "Seminar");
    assert.equal(translateLessonKind(null, i18n.t), "");
  });
}
