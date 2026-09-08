import assert from "node:assert/strict";
import test from "node:test";

import {
  ERROR_TRANSLATION_KEYS,
  cleanTeamText,
  findMyTeam,
  getSafeRepositoryUrl,
  resolveJoinView,
  shouldShowJoinAction,
  validateTeamForm,
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


test("каждый код ошибки командных эндпоинтов имеет ключ локализации", () => {
  const codes = [
    "NOT_A_TEAM_LAB",
    "LAB_NOT_CONFIGURED",
    "SESSION_REQUIRED",
    "TEAMS_UNAVAILABLE",
    "TEAM_NOT_FOUND",
    "ALREADY_IN_TEAM",
    "TEAM_FULL",
    "TEAM_LIMIT_REACHED",
    "TITLE_TAKEN",
    "INVALID_TITLE",
    "SLUG_RACE",
    "PROVISION_FAILED",
    "title_too_short",
    "title_too_long",
    "title_has_separator",
    "description_too_long",
  ];

  for (const code of codes) {
    assert.equal(
      typeof ERROR_TRANSLATION_KEYS[code],
      "string",
      `код ${code} должен иметь ключ локализации`
    );
  }
});


test("состояние экрана выбирается по данным backend, а не по адресной строке", () => {
  // Индивидуальная лаба - прежний экран
  assert.equal(
    resolveJoinView({ teamEnabled: false, teamsData: null, teamsError: null }),
    "individual"
  );

  // Пока список команд не пришёл - загрузка
  assert.equal(
    resolveJoinView({ teamEnabled: true, teamsData: null, teamsError: null }),
    "loading"
  );

  // Нет сессии - лендинг с кнопкой входа, а не ошибка
  assert.equal(
    resolveJoinView({ teamEnabled: true, teamsData: null, teamsError: "SESSION_REQUIRED" }),
    "landing"
  );

  // Любая другая ошибка - экран ошибки
  assert.equal(
    resolveJoinView({ teamEnabled: true, teamsData: null, teamsError: "TEAMS_UNAVAILABLE" }),
    "error"
  );

  // Авторизован, команды нет - выбор команды
  assert.equal(
    resolveJoinView({
      teamEnabled: true,
      teamsData: { my_team: null, teams: [] },
      teamsError: null,
    }),
    "picker"
  );

  // Авторизован и состоит в команде - карточка своей команды
  assert.equal(
    resolveJoinView({
      teamEnabled: true,
      teamsData: { my_team: "team-2", teams: [] },
      teamsError: null,
    }),
    "member"
  );
});


test("своя команда находится по slug из ответа backend", () => {
  const teamsData = {
    my_team: "team-2",
    teams: [
      { slug: "team-1", title: "Пингвины" },
      { slug: "team-2", title: "Тюлени" },
    ],
  };

  assert.equal(findMyTeam(teamsData).title, "Тюлени");
  assert.equal(findMyTeam({ my_team: null, teams: teamsData.teams }), null);
  assert.equal(findMyTeam(null), null);
  // Ссылка на несуществующую команду не должна ронять страницу
  assert.equal(findMyTeam({ my_team: "team-9", teams: teamsData.teams }), null);
});


test("клиентская валидация формы создания команды повторяет правила backend", () => {
  assert.equal(validateTeamForm("Пингвины", ""), null);
  assert.equal(validateTeamForm("  Пингвины  ", "  учим планировщик "), null);

  assert.equal(validateTeamForm("", ""), "title_too_short");
  assert.equal(validateTeamForm("ab", ""), "title_too_short");
  assert.equal(validateTeamForm("я".repeat(61), ""), "title_too_long");
  assert.equal(validateTeamForm("я".repeat(60), ""), null);
  assert.equal(validateTeamForm("Пингвины — лучшие", ""), "title_has_separator");
  assert.equal(validateTeamForm("Пингвины", "я".repeat(201)), "description_too_long");
  // Разделитель в описании допустим - разбор идёт по первому вхождению
  assert.equal(validateTeamForm("Пингвины", "первый — второй"), null);
});


test("схлопывание пробелов совпадает с очисткой на backend", () => {
  assert.equal(cleanTeamText("  Весёлые   пингвины  "), "Весёлые пингвины");
  assert.equal(cleanTeamText("Пингвины\nи тюлени"), "Пингвины и тюлени");
  assert.equal(cleanTeamText(null), "");
});
