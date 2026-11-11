import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTION_ERROR_MIN_MS,
  POLL_ADMIN_MS,
  POLL_ANONYMOUS_MS,
  POLL_MAX_BACKOFF_MS,
  POLL_STUDENT_MS,
  actionErrorAfterPoll,
  canBeginLogin,
  createLatestGate,
  createPoller,
  errorKey,
  findLab,
  formatDay,
  formatInCourseTz,
  groupsLabel,
  isLabSelectable,
  labsView,
  lastResultToShow,
  loginErrorToShow,
  nextPollDelay,
  openSessionsForMe,
  courseHasQueue,
  queuePagePath,
  readCallback,
  resolveSignupChoice,
  sessionTimeRange,
  sessionTitle,
  shouldOfferQueue,
  signupText,
  statusKey,
  upcomingSessionsForMe,
} from "./state.js";

// Подставляет параметры в ключ, чтобы по результату было видно и ключ, и данные.
const fakeT = (key, params) => (params ? `${key}${JSON.stringify(params)}` : key);


test("errorKey переводит известные коды и сводит прочие к unknown", () => {
  assert.equal(errorKey("SIGNUP_CLOSED"), "queue.errors.SIGNUP_CLOSED");
  assert.equal(errorKey("state_expired"), "queue.errors.state_expired");
  assert.equal(errorKey("rate_limit"), "queue.errors.rate_limit");
  assert.equal(errorKey("SOMETHING_NEW"), "queue.errors.unknown");
  assert.equal(errorKey(undefined), "queue.errors.unknown");
  // Код из адресной строки - ввод пользователя: в ключ попадают только известные
  assert.equal(errorKey("__proto__"), "queue.errors.unknown");
});

test("statusKey: известные статусы и запасной ключ", () => {
  assert.equal(statusKey("no_show"), "queue.status.no_show");
  assert.equal(statusKey("weird"), "queue.status.unknown");
});

test("sessionTitle переводит тип сессии, а тип занятия и предмет оставляет данными", () => {
  assert.equal(sessionTitle({ kind: "all_day" }, fakeT), "queue.session.kind.all_day");
  assert.equal(sessionTitle({ kind: "manual", subject: "ОС" }, fakeT), "queue.session.kind.manual");
  assert.equal(
    sessionTitle({ kind: "schedule", lesson_type: "Лабораторное занятие", subject: "Операционные системы" }, fakeT),
    'queue.session.typeAndSubject{"type":"Лабораторное занятие","subject":"Операционные системы"}'
  );
  assert.equal(sessionTitle({ kind: "schedule", lesson_type: "Лабораторное занятие", subject: "" }, fakeT), "Лабораторное занятие");
  assert.equal(sessionTitle({ kind: "schedule", lesson_type: "", subject: "" }, fakeT), "queue.session.kind.schedule");
  assert.equal(sessionTitle({ kind: "unexpected" }, fakeT), "queue.session.kind.schedule");
});

test("groupsLabel: пустой список - все группы", () => {
  assert.equal(groupsLabel({ groups: ["4431", "4432"] }, fakeT), "4431, 4432");
  assert.equal(groupsLabel({ groups: [] }, fakeT), "queue.session.allGroups");
});

test("formatInCourseTz показывает время пары в поясе курса, а не браузера", () => {
  // 12:10 UTC = 15:10 по Москве: начало 4-й пары ГУАП
  assert.equal(formatInCourseTz("2026-11-05T12:10:00Z", 180, "ru"), "15:10");
  assert.equal(formatInCourseTz("2026-11-05T15:10:00+03:00", 180, "en"), "15:10");
  assert.equal(formatInCourseTz("2026-11-05T12:10:00Z", 300, "zh"), "17:10");
  // переход через полночь
  assert.equal(formatInCourseTz("2026-11-05T22:30:00Z", 180, "ru"), "01:30");
  assert.equal(formatInCourseTz("2026-11-05T12:10:00Z", -120, "ru"), "10:10");
});

test("formatInCourseTz: пустое и некорректное время, нет смещения", () => {
  assert.equal(formatInCourseTz(null, 180, "ru"), "");
  assert.equal(formatInCourseTz("не дата", 180, "ru"), "");
  // без смещения время всё равно показывается - в поясе браузера
  assert.match(formatInCourseTz("2026-11-05T12:10:00Z", null, "ru"), /^\d{2}:\d{2}$/);
});

test("formatInCourseTz принимает свой формат", () => {
  const text = formatInCourseTz("2026-11-05T21:30:00Z", 180, "ru", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  assert.equal(text, "06.11.2026");
});

test("formatDay печатает день курса без сдвига на пояс браузера", () => {
  assert.equal(formatDay("2026-11-05", "ru"), "четверг, 5 ноября");
  assert.equal(formatDay("05.11.2026", "ru"), "");
  assert.equal(formatDay(null, "ru"), "");
});

test("sessionTimeRange и signupText показывают время в поясе курса", () => {
  const session = {
    starts_at: "2026-11-05T12:10:00Z",
    ends_at: "2026-11-05T13:40:00Z",
    signup_opens_at: "2026-11-05T11:55:00Z",
    signup_closes_at: "2026-11-05T13:40:00Z",
  };
  assert.equal(sessionTimeRange(session, 180, "ru"), "15:10–16:40");
  assert.equal(
    signupText({ ...session, signup: "upcoming" }, fakeT, 180, "ru"),
    'queue.signup.upcoming{"time":"14:55"}'
  );
  assert.equal(signupText({ ...session, signup: "open" }, fakeT, 180, "ru"), 'queue.signup.open{"time":"16:40"}');
  assert.equal(signupText({ ...session, signup: "closed" }, fakeT, 180, "ru"), "queue.signup.closed");
});

test("nextPollDelay: студент 10 с, аноним 60 с, админ 5 с", () => {
  assert.equal(nextPollDelay({ loggedIn: true }), POLL_STUDENT_MS);
  assert.equal(POLL_STUDENT_MS, 10000);
  assert.equal(nextPollDelay({}), POLL_ANONYMOUS_MS);
  assert.equal(POLL_ANONYMOUS_MS, 60000);
  assert.equal(nextPollDelay({ admin: true }), POLL_ADMIN_MS);
  assert.equal(POLL_ADMIN_MS, 5000);
});

test("nextPollDelay: скрытая вкладка не опрашивается, после ошибок пауза растёт до минуты", () => {
  assert.equal(nextPollDelay({ loggedIn: true, hidden: true }), null);
  assert.equal(nextPollDelay({ admin: true, failures: 1 }), 10000);
  assert.equal(nextPollDelay({ admin: true, failures: 2 }), 20000);
  assert.equal(nextPollDelay({ admin: true, failures: 10 }), POLL_MAX_BACKOFF_MS);
  assert.equal(nextPollDelay({ loggedIn: true, failures: 3 }), POLL_MAX_BACKOFF_MS);
  assert.equal(nextPollDelay({ failures: 5 }), POLL_ANONYMOUS_MS);
  assert.equal(nextPollDelay({ admin: true, rateLimited: true }), POLL_MAX_BACKOFF_MS);
});

test("createLatestGate: ответ запроса, начатого раньше, не затирает более свежий", () => {
  const gate = createLatestGate();
  const pollTicket = gate.begin(); // опрос ушёл
  const actionTicket = gate.begin(); // студент нажал «Выйти»
  assert.equal(gate.accept(actionTicket), true); // пришёл ответ на POST
  assert.equal(gate.accept(pollTicket), false); // старый GET отброшен
  const nextPoll = gate.begin();
  assert.equal(gate.accept(nextPoll), true);
});

const STATE = {
  viewer: { github: "ivanov", name: "Иванов И. И.", group: "4431" },
  sessions: [
    { id: 1, signup: "open", for_me: true },
    { id: 2, signup: "open", for_me: false },
    { id: 3, signup: "upcoming", for_me: true },
    { id: 4, signup: "closed", for_me: true },
  ],
  my_entry: null,
  my_last_result: { status: "defended", lab: "ЛР2", finished_at: "2026-11-05T12:00:00Z" },
};

test("openSessionsForMe и upcomingSessionsForMe: только своей группы", () => {
  assert.deepEqual(openSessionsForMe(STATE).map((s) => s.id), [1]);
  assert.deepEqual(upcomingSessionsForMe(STATE).map((s) => s.id), [3]);
  assert.deepEqual(openSessionsForMe({ ...STATE, viewer: null }), []);
  assert.deepEqual(openSessionsForMe(null), []);
});

test("lastResultToShow: только вошедшему и пока нет новой записи", () => {
  assert.equal(lastResultToShow(STATE).lab, "ЛР2");
  assert.equal(lastResultToShow({ ...STATE, my_entry: { id: 9 } }), null);
  assert.equal(lastResultToShow({ ...STATE, viewer: null }), null);
});

const LABS = [
  { id: "1", short_name: "ЛР1", status: "defended" },
  { id: "2", short_name: "ЛР2", status: "not_passed" },
  { id: "3", short_name: "ЛР3", status: "passed" },
  { id: "4", short_name: "ЛР4", status: "passed" },
];

test("findLab понимает и ключ конфига, и short-name", () => {
  assert.equal(findLab(LABS, "3").short_name, "ЛР3");
  assert.equal(findLab(LABS, "ЛР4").id, "4");
  assert.equal(findLab(LABS, "ЛР9"), null);
  assert.equal(findLab(LABS, null), null);
});

test("isLabSelectable: защищённую выбрать нельзя, не прошедшую - можно", () => {
  assert.equal(isLabSelectable(LABS[0]), false);
  assert.equal(isLabSelectable(LABS[1]), true);
  assert.equal(isLabSelectable(LABS[2]), true);
  assert.equal(isLabSelectable(null), false);
});

test("resolveSignupChoice: ?lab= выбирает работу заранее", () => {
  const openSessions = [{ id: 1 }, { id: 5 }];
  const empty = { sessionId: null, labId: null };
  assert.deepEqual(resolveSignupChoice({ openSessions, labs: LABS, current: empty, labParam: "ЛР4" }), {
    sessionId: 1,
    labId: "4",
  });
  // Не прошедшую по кэшу таблицы тоже можно предвыбрать: сервер перепроверит
  assert.equal(resolveSignupChoice({ openSessions, labs: LABS, current: empty, labParam: "2" }).labId, "2");
  // Защищённая из ?lab= не выбирается - берётся первая прошедшая
  assert.equal(resolveSignupChoice({ openSessions, labs: LABS, current: empty, labParam: "ЛР1" }).labId, "3");
});

test("resolveSignupChoice: выбор студента сохраняется, пока он допустим", () => {
  const openSessions = [{ id: 1 }, { id: 5 }];
  const kept = resolveSignupChoice({ openSessions, labs: LABS, current: { sessionId: 5, labId: "4" }, labParam: "3" });
  assert.deepEqual(kept, { sessionId: 5, labId: "4" });

  // Сессия закрылась, работа стала защищённой - выбор сбрасывается
  const reset = resolveSignupChoice({
    openSessions: [{ id: 1 }],
    labs: LABS,
    current: { sessionId: 5, labId: "1" },
    labParam: null,
  });
  assert.deepEqual(reset, { sessionId: 1, labId: "3" });
});

test("resolveSignupChoice: числовые id с сервера и пустые списки", () => {
  const numeric = [{ id: 7, short_name: "ЛР7", status: "passed" }];
  assert.equal(resolveSignupChoice({ openSessions: [], labs: numeric, current: { labId: "7" } }).labId, "7");
  assert.deepEqual(resolveSignupChoice({ openSessions: [], labs: null, current: null }), {
    sessionId: null,
    labId: null,
  });
  const noPassed = [{ id: "2", short_name: "ЛР2", status: "not_passed" }];
  assert.equal(resolveSignupChoice({ openSessions: [{ id: 1 }], labs: noPassed, current: null }).labId, null);
});

test("readCallback разбирает возврат из входа через GitHub", () => {
  assert.deepEqual(readCallback(new URLSearchParams("status=error&reason=state_expired")), {
    status: "error",
    reason: "state_expired",
  });
  assert.deepEqual(readCallback(new URLSearchParams("status=error")), { status: "error", reason: "unknown" });
  assert.deepEqual(readCallback(new URLSearchParams("status=ok&lab=3")), { status: "ok", reason: null });
  assert.equal(readCallback(new URLSearchParams("lab=3")), null);
});

test("shouldOfferQueue: только прошедшая и ещё не защищённая работа", () => {
  const cases = [
    [{ status: "updated", result: "v" }, true],
    [{ status: "updated", result: "v-1" }, true],
    [{ status: "updated", result: "v@10,5-3" }, true],
    [{ status: "updated", result: " V " }, true],
    [{ status: "updated", result: "x" }, false],
    [{ status: "updated", result: "?! Wrong TASKID" }, false],
    [{ status: "updated", result: "" }, false],
    [{ status: "rejected", current_grade: "v-2", result: "v-2" }, true],
    [{ status: "rejected", current_grade: "vv", result: "vv" }, false],
    [{ status: "rejected", current_grade: "VV-1" }, false],
    [{ status: "rejected", result: "v" }, true],
    [{ status: "pending", result: "v" }, false],
    [null, false],
  ];
  for (const [response, expected] of cases) {
    assert.equal(shouldOfferQueue(response), expected, JSON.stringify(response));
  }
});

test("courseHasQueue: кнопка очереди только у активного курса с включённой очередью", () => {
  assert.equal(courseHasQueue({ queue_enabled: true, status: "active" }), true);
  assert.equal(courseHasQueue({ queue_enabled: true, status: "archived" }), false);
  assert.equal(courseHasQueue({ queue_enabled: false, status: "active" }), false);
  assert.equal(courseHasQueue({ status: "active" }), false);
  assert.equal(courseHasQueue({ detail: "Course not found" }), false);
  assert.equal(courseHasQueue(null), false);
});

test("queuePagePath кодирует курс и работу", () => {
  assert.equal(queuePagePath("os-2026"), "/course/os-2026/queue");
  assert.equal(queuePagePath("os-2026", "ЛР0.1"), "/course/os-2026/queue?lab=%D0%9B%D0%A00.1");
  assert.equal(queuePagePath("a/b", "x&y"), "/course/a%2Fb/queue?lab=x%26y");
});


// --- Опрос ---

// Таймеры под контролем теста: fire() выполняет запланированный опрос.
function fakeTimers() {
  const pending = new Map();
  let next = 1;
  return {
    pending,
    setTimer: (fn, delay) => {
      const id = next++;
      pending.set(id, { fn, delay });
      return id;
    },
    clearTimer: (id) => pending.delete(id),
    async fire() {
      const [[id, { fn }]] = pending;
      pending.delete(id);
      await fn();
    },
  };
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

test("createPoller: опрос по цепочке таймеров, после stop() запросов нет", async () => {
  const timers = fakeTimers();
  let calls = 0;
  const poller = createPoller(async () => {
    calls += 1;
    return 5000;
  }, timers);

  poller.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.deepEqual([...timers.pending.values()].map((t) => t.delay), [5000]);

  await timers.fire();
  assert.equal(calls, 2);

  poller.stop();
  assert.equal(timers.pending.size, 0);
  // «обновить сейчас» после размонтирования (finally действия) - без запроса
  await poller.run();
  assert.equal(calls, 2);
  assert.equal(timers.pending.size, 0);
});

test("createPoller: запрос, бывший в пути при stop(), не планирует следующий и не повторяется", async () => {
  const timers = fakeTimers();
  const gate = deferred();
  let calls = 0;
  const poller = createPoller(async () => {
    calls += 1;
    await gate.promise;
    return 5000;
  }, timers);

  poller.start();
  // refresh() во время запроса - повтор после него
  poller.run();
  poller.stop();
  gate.resolve();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 1);
  assert.equal(timers.pending.size, 0);
});

test("createPoller: refresh() во время запроса - один повтор сразу после него", async () => {
  const timers = fakeTimers();
  const first = deferred();
  let calls = 0;
  const poller = createPoller(async () => {
    calls += 1;
    if (calls === 1) await first.promise;
    return 5000;
  }, timers);

  poller.start();
  poller.run();
  poller.run();
  first.resolve();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 2);
  assert.equal(timers.pending.size, 1);
  poller.stop();
});

test("createPoller: на скрытой вкладке следующий опрос не планируется", async () => {
  const timers = fakeTimers();
  const poller = createPoller(async () => 5000, { ...timers, isHidden: () => true });
  poller.start();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(timers.pending.size, 0);
  poller.stop();
});


// --- Ошибка действия после восстановления связи ---

test("actionErrorAfterPoll: «нет связи» снимается удачным опросом, начатым позже", () => {
  for (const code of ["network_error", "request_timeout", "rate_limit"]) {
    const error = { code, at: 1000 };
    // опрос сразу после действия ошибку не смывает - её надо успеть прочитать
    assert.equal(actionErrorAfterPoll(error, 1000), error, code);
    assert.equal(actionErrorAfterPoll(error, 1000 + ACTION_ERROR_MIN_MS - 1), error, code);
    assert.equal(actionErrorAfterPoll(error, 1000 + ACTION_ERROR_MIN_MS), null, code);
  }
});

test("actionErrorAfterPoll: отказ сервера остаётся до следующего действия", () => {
  for (const code of ["SIGNUP_CLOSED", "LAB_NOT_PASSED", "STORAGE_UNAVAILABLE", "unknown"]) {
    const error = { code, at: 1000 };
    assert.equal(actionErrorAfterPoll(error, 1000 + 60000), error, code);
  }
  assert.equal(actionErrorAfterPoll(null, 5000), null);
});


// --- Вход и список работ ---

test("canBeginLogin: вход доступен, даже если анонимный опрос упал", () => {
  assert.equal(canBeginLogin(null, "rate_limit"), true);
  assert.equal(canBeginLogin(null, "network_error"), true);
  assert.equal(canBeginLogin(null, "request_timeout"), true);
  assert.equal(canBeginLogin(null, "STORAGE_UNAVAILABLE"), true);
  // курса нет или очередь выключена - входить незачем
  assert.equal(canBeginLogin(null, "QUEUE_DISABLED"), false);
  assert.equal(canBeginLogin(null, "COURSE_NOT_FOUND"), false);
  assert.equal(canBeginLogin(null, "not_found"), false);
  // первый ответ ещё не пришёл
  assert.equal(canBeginLogin(null, null), false);
  // есть ответ: вход - только анониму, и при ошибке следующего опроса тоже
  assert.equal(canBeginLogin({ viewer: null, sessions: [] }, null), true);
  assert.equal(canBeginLogin({ viewer: null, sessions: [] }, "rate_limit"), true);
  assert.equal(canBeginLogin({ viewer: { github: "ivanov" }, sessions: [] }, null), false);
});

test("canBeginLogin: при ошибке настроек очереди и чужом адресе страницы вход не предлагается", () => {
  // вход вернул бы ту же причину: петля «Войти» -> та же ошибка -> «Войти»
  assert.equal(canBeginLogin(null, "QUEUE_MISCONFIGURED"), false);
  assert.equal(canBeginLogin(null, "BAD_ORIGIN"), false);
  // и когда очередь сломалась уже после первого ответа
  assert.equal(canBeginLogin({ viewer: null, sessions: [] }, "QUEUE_MISCONFIGURED"), false);
  assert.equal(canBeginLogin({ viewer: null, sessions: [] }, "QUEUE_DISABLED"), false);
});

test("loginErrorToShow: та же причина, что у опроса, - одна плашка", () => {
  const failed = { status: "error", reason: "QUEUE_MISCONFIGURED" };
  assert.equal(loginErrorToShow(failed, "QUEUE_MISCONFIGURED"), null);
  assert.equal(loginErrorToShow(failed, null), "QUEUE_MISCONFIGURED");
  assert.equal(loginErrorToShow({ status: "error", reason: "STUDENT_NOT_FOUND" }, "rate_limit"), "STUDENT_NOT_FOUND");
  assert.equal(loginErrorToShow({ status: "ok", reason: null }, null), null);
  assert.equal(loginErrorToShow(null, "QUEUE_MISCONFIGURED"), null);
});

test("labsView: без списка и без загрузки - состояние с кнопкой «Повторить»", () => {
  assert.equal(labsView({ labs: null, labsError: null, labsLoading: false }), "idle");
  assert.equal(labsView({ labs: null, labsError: null, labsLoading: true }), "loading");
  assert.equal(labsView({ labs: null, labsError: "network_error", labsLoading: false }), "error");
  assert.equal(labsView({ labs: { labs: [] }, labsError: null, labsLoading: false }), "empty");
  // список есть - показывается, пока перечитывается и даже после ошибки
  const labs = { labs: [{ id: "3", short_name: "ЛР3", status: "passed" }] };
  assert.equal(labsView({ labs, labsError: "network_error", labsLoading: true }), "list");
  // ответ без списка - не вечная загрузка
  assert.equal(labsView({ labs: {}, labsError: null, labsLoading: false }), "idle");
});
