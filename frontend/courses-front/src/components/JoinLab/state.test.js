import assert from "node:assert/strict";
import test from "node:test";

import { ERROR_TRANSLATION_KEYS, getSafeRepositoryUrl } from "./state.js";


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
});
