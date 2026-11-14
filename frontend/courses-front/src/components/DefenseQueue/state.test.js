import test from "node:test";
import assert from "node:assert/strict";
import { cleanIdentity, ownEntry, canIdentify, entryLabel, identityKey } from "./state.js";

test("identity keeps only known string fields", () => {
  assert.deepEqual(cleanIdentity({ name: "Имя", github: 42, admin: true }), { surname: "", name: "Имя", patronymic: "", github: "" });
  assert.equal(canIdentify({ surname: "Ф", name: "И", github: "login" }), true);
  assert.equal(canIdentify({ surname: "Ф", name: "И", github: "  " }), false);
});
test("student matching is case insensitive", () => {
  const row = { github: "Login", candidate_id: 3 };
  assert.equal(ownEntry([row], " LOGIN "), row);
  assert.equal(ownEntry([row], "other"), undefined);
  assert.equal(ownEntry([{ github: "", full_name: "Другой студент" }], ""), undefined);
  assert.equal(ownEntry([{ github: "" }], "  "), undefined);
});
test("current, next and repeat states are distinct", () => {
  const row = { attempt: { entry_id: 2, status: "waiting", is_repeat: 1 } };
  assert.equal(entryLabel(row, { current: 2 }), "Сдаёт сейчас");
  assert.equal(entryLabel(row, { next: 2 }), "Следующий · повторно");
  assert.equal(entryLabel({ state: "unconfirmed" }), "Не придёт");
  assert.equal(entryLabel({ state: "declined" }), "Не придёт");
});
test("storage keys cannot collide through separators", () => {
  assert.notEqual(identityKey("a.b", "c"), identityKey("a", "b.c"));
});

test("each simultaneous defense is marked current", () => {
  for (const entry_id of [2, 3]) {
    assert.equal(entryLabel({ attempt: { entry_id, status: "called" } }, { current: [2, 3] }), "Сдаёт сейчас");
  }
  assert.equal(entryLabel({ attempt: { entry_id: 4, status: "waiting" } }, { current: [2, 3], next: 4 }), "Следующий");
});

test("schedule follows course/group filters and All keeps independent sessions", async () => {
  const { filterSessions, ALL_GROUPS } = await import("./state.js");
  const rows = [{session_id:"b",course_id:"c",group_id:"g2",starts_at:200}, {session_id:"a",course_id:"c",group_id:"g1",starts_at:100}, {session_id:"x",course_id:"other",group_id:"g1",starts_at:50}];
  assert.deepEqual(filterSessions(rows,"c","g1").map(row=>row.session_id), ["a"]);
  assert.deepEqual(filterSessions(rows,"c",ALL_GROUPS).map(row=>row.session_id), ["a","b"]);
  assert.equal(rows[0].session_id,"b");
});
test("move editor prefill preserves browser local time", async () => {
  const { localDateTime } = await import("./state.js");
  const stamp = 1794238800;
  assert.ok(Math.abs(new Date(localDateTime(stamp)).getTime()/1000-stamp)<60);
  assert.equal(localDateTime(NaN), "");
});


test("finished repeats do not have ambiguous repeat suffixes", () => {
  assert.equal(entryLabel({attempt:{status:"withdrawn",is_repeat:true}}),"Снят с очереди");
  assert.equal(entryLabel({attempt:{status:"waiting",is_repeat:true}}),"Ожидает повторного вызова");
});
