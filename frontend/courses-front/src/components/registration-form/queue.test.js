import assert from "node:assert/strict";
import test from "node:test";

import ru from "../../locales/ru/translation.json" with { type: "json" };
import en from "../../locales/en/translation.json" with { type: "json" };
import zh from "../../locales/zh/translation.json" with { type: "json" };

const translations = [
  ["ru", ru],
  ["en", en],
  ["zh", zh],
];

const queueErrorCodes = [
  "INVALID_RESPONSE",
  "ALREADY_IN_QUEUE",
  "NOT_IN_QUEUE",
  "CI_NOT_PASSED",
  "CI_NOT_RUN",
  "CI_CHECK_FAILED",
  "ALREADY_PASSED",
  "NO_TEAM",
  "TEAMS_UNAVAILABLE",
  "QUEUE_CLOSED",
  "INVALID_SHIFT",
  "NOT_WAITING",
  "NOT_IN_GROUP",
  "SESSION_REQUIRED",
  "SHEETS_UNAVAILABLE",
];

test("queue screen and errors are translated in every supported language", () => {
  const queueKeys = [
    "title",
    "description",
    "loading",
    "signInRequired",
    "signIn",
    "alreadyPassed",
    "position",
    "closed",
    "ciNotRun",
    "ciFailed",
    "register",
    "withdraw",
    "shift",
    "listTitle",
    "empty",
    "cannotRegister",
    "teamTitle",
    "teamMembers",
  ];

  for (const [language, translation] of translations) {
    for (const key of queueKeys) {
      assert.equal(
        typeof translation.queue[key],
        "string",
        `${language}: queue.${key}`
      );
    }
    for (const code of queueErrorCodes) {
      assert.equal(
        typeof translation.queue.errors[code],
        "string",
        `${language}: queue.errors.${code}`
      );
    }
    for (const status of ["ждёт", "сдал", "не_сдал", "нет", "отозван"]) {
      assert.equal(
        typeof translation.queue.statuses[status],
        "string",
        `${language}: queue.statuses.${status}`
      );
    }
  }
});
