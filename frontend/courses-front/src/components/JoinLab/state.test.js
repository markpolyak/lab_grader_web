import assert from "node:assert/strict";
import test from "node:test";

import {
  ERROR_TRANSLATION_KEYS,
  getSafeRepositoryUrl,
  shouldShowJoinAction,
} from "./state.js";


test("ошибки OAuth state имеют отдельные ключи локализации", () => {
  assert.deepEqual(
    [
      "oauth_state_missing",
      "oauth_state_invalid",
      "oauth_state_expired",
      "oauth_state_mismatch",
    ].map((code) => ERROR_TRANSLATION_KEYS[code]),
    [
      "join.errors.oauthStateMissing",
      "join.errors.oauthStateInvalid",
      "join.errors.oauthStateExpired",
      "join.errors.oauthStateMismatch",
    ]
  );
});


test("страница принимает только обычную ссылку репозитория github.com", () => {
  assert.equal(
    getSafeRepositoryUrl("https://github.com/test-org/test-repository"),
    "https://github.com/test-org/test-repository"
  );
  assert.equal(getSafeRepositoryUrl("https://example.com/test-org/repository"), null);
  assert.equal(getSafeRepositoryUrl("javascript:alert(1)"), null);
  assert.equal(getSafeRepositoryUrl("https://github.com/test-org/repository/issues"), null);
  assert.equal(getSafeRepositoryUrl("https://user:password@github.com/org/repo"), null);
  assert.equal(getSafeRepositoryUrl("https://github.com:444/org/repo"), null);
  assert.equal(getSafeRepositoryUrl("https://github.com/org/repo?tab=readme"), null);
  assert.equal(getSafeRepositoryUrl("https://github.com/org/repo#readme"), null);
  assert.equal(getSafeRepositoryUrl("https://github.com/org%2Frepo/other"), null);
});


test("повреждённая ссылка успеха оставляет кнопку повторного входа", () => {
  assert.equal(shouldShowJoinAction("success", null), true);
  assert.equal(
    shouldShowJoinAction("success", "https://github.com/org/repository"),
    false
  );
  assert.equal(shouldShowJoinAction("error", null), true);
});
