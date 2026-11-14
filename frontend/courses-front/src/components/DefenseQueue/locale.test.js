import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
const read = lang => JSON.parse(fs.readFileSync(new URL(`../../locales/${lang}/queue.json`, import.meta.url), "utf8"));

test("queue languages have identical keys and interpolation fields", () => {
  const ru = read("ru");
  for (const language of ["en", "zh"]) {
    const translated = read(language);
    assert.deepEqual(Object.keys(translated).sort(), Object.keys(ru).sort());
    for (const key of Object.keys(ru)) {
      assert.ok(translated[key].trim(), `${language}: ${key}`);
      assert.deepEqual(translated[key].match(/\{\{\w+\}\}/g) || [], ru[key].match(/\{\{\w+\}\}/g) || []);
    }
  }
});

test("all literal queue UI copy has a translation", () => {
  const ru = read("ru");
  for (const file of ["TeacherQueue.jsx", "StudentQueue.jsx", "RoundQueue.jsx", "../registration-form/index.jsx"]) {
    const source = fs.readFileSync(new URL(file, import.meta.url), "utf8");
    for (const match of source.matchAll(/\bq\(("(?:[^"\\]|\\.)*")/g)) {
      const key = JSON.parse(match[1]);
      assert.ok(key in ru, `${file}: ${key}`);
    }
  }
});


test("all help tooltips are translated", () => {
  const ru = read("ru");
  for (const file of ["TeacherQueue.jsx", "StudentQueue.jsx"]) {
    const source = fs.readFileSync(new URL(file, import.meta.url), "utf8");
    for (const match of source.matchAll(/<Hint(?: control)? text=("(?:[^"\\]|\\.)*")/g)) {
      const key = JSON.parse(match[1]);
      assert.ok(key in ru, key);
    }
  }
});
