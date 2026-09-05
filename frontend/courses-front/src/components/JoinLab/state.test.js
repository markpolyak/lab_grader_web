import assert from "node:assert/strict";
import test from "node:test";

import {
  ERROR_TRANSLATION_KEYS,
  getSafeRepositoryUrl,
  shouldShowJoinAction,
} from "./state.js";


test("каждый код ошибки бэкенда имеет ключ локализации", () => {
  const codes = [
    "access_denied",
    "missing_code",
    "oauth_exchange_failed",
    "oauth_not_configured",
    "invalid_state",
    "config",
    "provision_failed",
    "INVALID_TEMPLATE_CONFIG",
    "CREATE_VALIDATION_FAILED",
    "CREATE_FORBIDDEN",
    "RATE_LIMITED",
    "TEMPLATE_NOT_FOUND",
    "CREATE_FAILED",
    "INVITATIONS_FETCH_FAILED",
    "REINVITE_DELETE_FAILED",
    "INVITE_FAILED",
    "TEMPLATE_MUST_BE_PRIVATE",
    "FORK_TIMEOUT",
    "ACTIONS_ENABLE_FAILED",
    "NAME_TAKEN_BY_FOREIGN_REPO",
    "FORK_CHECK_FAILED",
    "join_not_found",
    "join_not_configured",
    "rate_limit",
    "request_timeout",
    "unknown",
  ];

  for (const code of codes) {
    assert.equal(
      typeof ERROR_TRANSLATION_KEYS[code],
      "string",
      `код ${code} должен иметь ключ локализации`
    );
  }

  // invalid_state - протухший/подделанный state, семантически совпадает с
  // истечением времени входа через GitHub.
  assert.equal(ERROR_TRANSLATION_KEYS.invalid_state, "join.errors.oauthStateExpired");
  // CREATE_FORBIDDEN - код, специфичный для #49, отдельный от общих ошибок
  // создания репозитория.
  assert.equal(ERROR_TRANSLATION_KEYS.CREATE_FORBIDDEN, "join.errors.createForbidden");
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
