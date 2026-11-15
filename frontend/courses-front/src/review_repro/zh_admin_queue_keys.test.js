import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import i18next from "i18next";

const src = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, src), "utf8");
const dictionary = (language) => JSON.parse(read(`locales/${language}/translation.json`));

test("queue admin screens have Chinese translations for their static keys", () => {
  const zh = dictionary("zh");
  const missing = [];
  for (const file of ["components/admin/layout/index.jsx", "components/admin/Queues/index.jsx"]) {
    for (const [, key] of read(file).matchAll(/\bt\(\s*"([\w.]+)"/g)) {
      const value = key.split(".").reduce((node, part) => node?.[part], zh);
      if (typeof value !== "string" || !value.trim()) missing.push(`${file}: ${key}`);
    }
  }
  assert.deepEqual(missing, []);
});

test("logout and confirmation stay Chinese with the Russian fallback enabled", async () => {
  const i18n = i18next.createInstance();
  await i18n.init({
    lng: "zh", fallbackLng: "ru",
    resources: { zh: { translation: dictionary("zh") }, ru: { translation: dictionary("ru") } },
  });
  assert.equal(i18n.t("logout"), "退出");
  assert.equal(i18n.t("yes"), "是");
});
