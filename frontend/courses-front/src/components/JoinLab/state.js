export const ERROR_TRANSLATION_KEYS = {
  oauth_denied: "join.errors.oauthDenied",
  oauth_failed: "join.errors.oauthFailed",
  oauth_unavailable: "join.errors.oauthUnavailable",
  oauth_not_configured: "join.errors.oauthNotConfigured",
  oauth_state_missing: "join.errors.oauthStateMissing",
  oauth_state_invalid: "join.errors.oauthStateInvalid",
  oauth_state_expired: "join.errors.oauthStateExpired",
  oauth_state_mismatch: "join.errors.oauthStateMismatch",
  join_not_found: "join.errors.notFound",
  join_not_configured: "join.errors.notConfigured",
  template_unavailable: "join.errors.templateUnavailable",
  repository_lookup_failed: "join.errors.repositoryFailed",
  repository_create_failed: "join.errors.repositoryFailed",
  access_check_failed: "join.errors.accessFailed",
  invitation_lookup_failed: "join.errors.accessFailed",
  invitation_delete_failed: "join.errors.accessFailed",
  invitation_create_failed: "join.errors.invitationFailed",
  github_rate_limit: "join.errors.rateLimit",
  github_unavailable: "join.errors.githubUnavailable",
  request_timeout: "join.errors.githubUnavailable",
  rate_limit: "join.errors.rateLimit",
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
