// Маппинг кодов ошибок бэкенда (main.py `_join_result_redirect` reason=..., и
// `result.error_code` из grading/repo_provisioning.py) на ключи локализации.
// См. таблицу в задаче о переносе UI страницы /join из #48 в #49.
export const ERROR_TRANSLATION_KEYS = {
  // main.py: /join/callback redirect reasons
  access_denied: "join.errors.oauthDenied",
  missing_code: "join.errors.oauthFailed",
  oauth_exchange_failed: "join.errors.oauthFailed",
  oauth_not_configured: "join.errors.oauthNotConfigured",
  invalid_state: "join.errors.oauthStateExpired",
  config: "join.errors.notConfigured",
  provision_failed: "join.errors.unknown",

  // grading/repo_provisioning.py: RepoProvisioner result.error_code
  INVALID_TEMPLATE_CONFIG: "join.errors.notConfigured",
  CREATE_VALIDATION_FAILED: "join.errors.repositoryFailed",
  CREATE_FORBIDDEN: "join.errors.createForbidden",
  RATE_LIMITED: "join.errors.rateLimit",
  TEMPLATE_NOT_FOUND: "join.errors.templateUnavailable",
  CREATE_FAILED: "join.errors.repositoryFailed",
  INVITATIONS_FETCH_FAILED: "join.errors.accessFailed",
  REINVITE_DELETE_FAILED: "join.errors.accessFailed",
  INVITE_FAILED: "join.errors.invitationFailed",
  TEMPLATE_MUST_BE_PRIVATE: "join.errors.templateMustBePrivate",
  FORK_TIMEOUT: "join.errors.forkTimeout",
  ACTIONS_ENABLE_FAILED: "join.errors.actionsEnableFailed",
  NAME_TAKEN_BY_FOREIGN_REPO: "join.errors.nameTaken",
  FORK_CHECK_FAILED: "join.errors.forkCheckFailed",

  // src/api/index.js: fetchJoinLab error.code
  join_not_found: "join.errors.notFound",
  join_not_configured: "join.errors.notConfigured",
  rate_limit: "join.errors.rateLimit",
  request_timeout: "join.errors.githubUnavailable",
  unknown: "join.errors.unknown",
};


export function shouldShowJoinAction(callbackStatus, repositoryUrl) {
  // Успех считается завершённым только после проверки безопасной GitHub-ссылки.
  // Повреждённый query-параметр не должен оставлять пользователя без повтора.
  return callbackStatus !== "success" || !repositoryUrl;
}


export function getSafeRepositoryUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    const pathParts = url.pathname.split("/").filter(Boolean);
    // Параметры результата остаются изменяемым вводом из адресной строки.
    // Разрешаем только обычный URL репозитория github.com из двух сегментов,
    // чтобы подделанный query string не превратил страницу в открытый redirect.
    if (
      url.protocol !== "https:" ||
      url.hostname !== "github.com" ||
      url.username !== "" ||
      url.password !== "" ||
      url.port !== "" ||
      url.search !== "" ||
      url.hash !== "" ||
      pathParts.length !== 2 ||
      !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(pathParts[0]) ||
      !/^[A-Za-z0-9_.-]{1,100}$/.test(pathParts[1])
    ) {
      return null;
    }
    return `https://github.com/${pathParts[0]}/${pathParts[1]}`;
  } catch {
    return null;
  }
}
