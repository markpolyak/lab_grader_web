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

  // main.py: коды секретной ссылки /j/{token} (§7.3 плана секретных ссылок)
  LINK_NOT_FOUND: "join.errors.linkNotFound",
  JOIN_NOT_OPEN: "join.errors.notOpen",
  JOIN_CLOSED: "join.errors.closed",
  LAB_MISCONFIGURED: "join.errors.notConfigured",
  OAUTH_NOT_CONFIGURED: "join.errors.oauthNotConfigured",

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


// Коды ошибок командных эндпоинтов (main.py §8.3 плана командных лаб).
// Отдаются backend'ом в `detail` как стабильные строки - фронтенд переводит
// их сам, как и коды RepoProvisioner выше.
Object.assign(ERROR_TRANSLATION_KEYS, {
  NOT_A_TEAM_LAB: "join.errors.notATeamLab",
  LAB_NOT_CONFIGURED: "join.errors.notConfigured",
  SESSION_REQUIRED: "join.errors.sessionRequired",
  TEAMS_UNAVAILABLE: "join.errors.teamsUnavailable",
  TEAM_NOT_FOUND: "join.errors.teamNotFound",
  ALREADY_IN_TEAM: "join.errors.alreadyInTeam",
  TEAM_FULL: "join.errors.teamFull",
  TEAM_LIMIT_REACHED: "join.errors.teamLimitReached",
  TITLE_TAKEN: "join.errors.titleTaken",
  INVALID_TITLE: "join.errors.invalidTitle",
  SLUG_RACE: "join.errors.slugRace",
  PROVISION_FAILED: "join.errors.unknown",

  // Клиентская валидация формы создания команды (§3.4 плана)
  title_too_short: "join.errors.titleTooShort",
  title_too_long: "join.errors.titleTooLong",
  title_has_separator: "join.errors.titleHasSeparator",
  description_too_long: "join.errors.descriptionTooLong",
});


// Ограничения названия и описания команды. Должны совпадать с
// grading/teams.py: клиентская проверка только избавляет от лишнего запроса,
// решение всё равно принимает backend.
export const TITLE_MIN_LENGTH = 3;
export const TITLE_MAX_LENGTH = 60;
export const DESCRIPTION_MAX_LENGTH = 200;
export const DESCRIPTION_SEPARATOR = " — ";


export function cleanTeamText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}


export function validateTeamForm(title, description) {
  const cleanTitle = cleanTeamText(title);
  const cleanDescription = cleanTeamText(description);

  if (cleanTitle.length < TITLE_MIN_LENGTH) return "title_too_short";
  if (cleanTitle.length > TITLE_MAX_LENGTH) return "title_too_long";
  if (cleanTitle.includes(DESCRIPTION_SEPARATOR)) return "title_has_separator";
  if (cleanDescription.length > DESCRIPTION_MAX_LENGTH) return "description_too_long";
  return null;
}


/**
 * Экран, который видит студент (§5 плана командных лаб).
 *
 * Признак «студент авторизован» - не query-параметр, а успешно полученный
 * список команд: cookie join_session помечена HttpOnly и странице не видна,
 * зато переживает перезагрузку, поэтому источником истины должен быть ответ
 * backend, а не адресная строка.
 */
export function resolveJoinView({ teamEnabled, teamsData, teamsError }) {
  if (!teamEnabled) return "individual";
  if (teamsData) return teamsData.my_team ? "member" : "picker";
  if (teamsError === "SESSION_REQUIRED") return "landing";
  if (teamsError) return "error";
  return "loading";
}


export function findMyTeam(teamsData) {
  if (!teamsData || !teamsData.my_team) return null;
  return teamsData.teams.find((team) => team.slug === teamsData.my_team) || null;
}


// --- Секретная ссылка /j/{token} (docs/SECRET_JOIN_LINKS_PLAN.md §10) ---

/**
 * Момент открытия или закрытия работы в читаемом виде.
 *
 * Backend отдаёт ISO-строку со смещением часового пояса курса; здесь она
 * показывается в поясе браузера студента, чтобы «10:00» не означало разного
 * времени для разных людей.
 */
export function formatMoment(iso, language) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(language || undefined, {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}


/**
 * Экран секретной ссылки.
 *
 * "not_open" - работа ещё не опубликована: backend отвечает на неё ошибкой,
 * не раскрывая названия. "closed" - приём закрыт, но войти всё равно можно:
 * студент с уже созданным репозиторием чинит по той же ссылке доступ.
 */
export function resolveSecretJoinView({ lab, loadError }) {
  if (loadError === "JOIN_NOT_OPEN") return "not_open";
  if (loadError) return "error";
  if (!lab) return "loading";
  return lab.join_state === "closed" ? "closed" : "open";
}
