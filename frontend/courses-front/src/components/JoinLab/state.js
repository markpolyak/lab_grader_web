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
  rate_limit: "join.errors.rateLimit",
};


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
      pathParts.length !== 2
    ) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}
