import assert from "node:assert/strict";
import test from "node:test";
import process from "node:process";
import { build } from "esbuild";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = fileURLToPath(new URL("../../", import.meta.url));
const component = join(root, "src/components/admin/Queues/index.jsx");
const styled = join(root, "src/components/queue-detail/styled.js");
const dir = mkdtempSync(join(tmpdir(), "admin-registration-"));
const bundle = await build({
  stdin: { resolveDir: root, loader: "jsx", contents: `
    import { renderToStaticMarkup } from 'react-dom/server';
    import { MemoryRouter } from 'react-router-dom';
    import i18n from './src/i18n';
    import { AdminQueues } from './src/components/admin/Queues';
    export function render(language) {
      i18n.changeLanguage(language);
      Object.assign(globalThis.__registrationFixture, {index: 0, buttons: []});
      return renderToStaticMarkup(<MemoryRouter><AdminQueues /></MemoryRouter>);
    }` },
  mainFields: ["module", "main"],
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic",
  loader: { ".js": "jsx", ".png": "dataurl" }, logLevel: "silent",
  define: { "process.env.NODE_ENV": '"production"', "import.meta.env": "{}" },
  plugins: [{ name: "seed-loaded-admin", setup(b) {
    b.onResolve({ filter: /^(react|react-router-dom|\.\.\/\.\.\/queue-detail\/styled)$/ }, a =>
      a.importer === component ? { path: a.path, namespace: "fixture" } : undefined);
    b.onLoad({ filter: /.*/, namespace: "fixture" }, a => ({ resolveDir: root, contents:
      a.path === "react" ? `
        export function useEffect() {}
        export function useState(initial) {
          const f = globalThis.__registrationFixture, index = f.index++;
          if (!(index in f.states)) f.states[index] = initial;
          return [f.states[index], value => { f.states[index] = typeof value === 'function' ? value(f.states[index]) : value; }];
        }` : a.path === "react-router-dom" ? `
        export const useParams = () => ({courseId: 'os', queueId: 'lesson'});
        export const useLocation = () => ({});
        export const useNavigate = () => () => {};` : `
        export * from ${JSON.stringify(styled)};
        import { SecondaryButton as RealButton } from ${JSON.stringify(styled)};
        import { createElement } from 'react';
        export function SecondaryButton(props) {
          globalThis.__registrationFixture.buttons.push(props);
          return createElement(RealButton, props);
        }`
    }));
  }}],
});
const outfile = join(dir, "render.cjs");
writeFileSync(outfile, bundle.outputFiles[0].text);
const { render } = createRequire(import.meta.url)(outfile);
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));
function seed(overrides = {}) {
  const data = { queue_id: "lesson", closed: false, version: 1, archive_pending: false,
    lesson: { starts_at: "2026-11-09T15:10:00+03:00", ends_at: "2026-11-09T16:40:00+03:00", groups: ["4431"], kind: "ЛР", cancelled: false },
    waiting: [{id: "first"}], entries: [], ...overrides };
  return globalThis.__registrationFixture = { states: {1: data}, index: 0, buttons: [] };
}
const button = label => globalThis.__registrationFixture.buttons.find(b => b.children === label);
for (const [lng, close, open] of [["ru", "Закрыть запись", "Открыть запись"], ["en", "Close registration", "Open registration"], ["zh", "关闭报名", "开放报名"]]) {
  test(`registration control is translated and toggles (${lng})`, () => {
    const f = seed(); render(lng);
    assert.ok(button(close), "open registration must have a close button");
    assert.equal(button(close).disabled, false);
    f.states[1] = {...f.states[1], closed: true}; render(lng);
    assert.ok(button(open), "closed registration must have an open button");
  });
}
test("close/reopen sends PUT, refreshes state, disables pending and handles failures", async () => {
  const f = seed(), requests = [], originalFetch = globalThis.fetch;
  let server = {...f.states[1]}, fail = false;
  globalThis.fetch = async (url, options) => {
    requests.push({url, ...options});
    if (fail) return {ok: false, status: 503, json: async () => ({detail: "UNAVAILABLE"})};
    if (options.method === "PUT") server = {...server, ...JSON.parse(options.body), version: server.version + 1};
    return {ok: true, json: async () => server};
  };
  try {
    render("ru");
    const closing = button("Закрыть запись").onClick(); render("ru");
    assert.equal(button("Закрыть запись").disabled, true);
    await closing; render("ru"); assert.ok(button("Открыть запись"));
    assert.equal(requests[0].url, "/api/v1/admin/courses/os/queues/lesson/registration");
    assert.equal(requests[0].method, "PUT"); assert.equal(requests[0].credentials, "include");
    assert.deepEqual(JSON.parse(requests[0].body), {closed: true});
    await button("Открыть запись").onClick(); render("ru"); assert.ok(button("Закрыть запись"));
    assert.deepEqual(JSON.parse(requests[2].body), {closed: false});
    fail = true; await button("Закрыть запись").onClick(); const html = render("ru");
    assert.equal(f.states[1].closed, false);
    assert.ok(f.states[2] && html.includes(f.states[2]));
    assert.equal(button("Закрыть запись").disabled, false);
  } finally { globalThis.fetch = originalFetch; }
});
test("cancelled lessons cannot reopen; archiving hides registration control", () => {
  const f = seed({closed: true}); f.states[1].lesson.cancelled = true; render("ru");
  assert.equal(button("Открыть запись").disabled, true);
  for (const closed of [false, true]) {
    seed({archive_pending: true, closed}); render("ru");
    assert.equal(button("Закрыть запись"), undefined); assert.equal(button("Открыть запись"), undefined);
  }
});
