import assert from "node:assert/strict";
import test from "node:test";

import {
  ADMIN_ERROR_CODES,
  HISTORY_PAGE_SIZE,
  OUTCOMES,
  adminErrorKeys,
  adminLoginPath,
  adminQueuePath,
  canOpenManual,
  csvCell,
  entryActions,
  entryDay,
  historyDayLabel,
  historyErrorCode,
  historyFileName,
  historyMoment,
  historyOutcomeButton,
  historyPage,
  historyPeriodError,
  historyQuery,
  historyToCsv,
  manualRequest,
  normalizeEntry,
  outcomeButton,
  pickSessionId,
  queueNotice,
  runAdminAction,
  SUMMARY_STATUSES,
  safeAdminNext,
  sessionActions,
  sessionSummary,
  splitEntries,
  summaryItems,
  tileResults,
} from "./state.js";

const fakeT = (key) => key;

// Пара 15:10–16:40 по Москве, запись за 15 минут: [14:55, 16:40)
const SESSION = {
  id: 1,
  kind: "schedule",
  groups: ["4431"],
  starts_at: "2026-11-05T12:10:00Z",
  ends_at: "2026-11-05T13:40:00Z",
  signup_opens_at: "2026-11-05T11:55:00Z",
  signup_closes_at: "2026-11-05T13:40:00Z",
  day_ends_at: "2026-11-05T21:00:00Z",
  closed_at: null,
  entries: [],
};

const entry = (id, status, extra = {}) => ({
  id,
  status,
  name: `Студент ${id}`,
  group: "4431",
  lab: "ЛР3",
  created_at: `2026-11-05T12:0${id % 10}:00Z`,
  ...extra,
});


test("normalizeEntry переводит запись ответа в поля таблиц", () => {
  const waiting = normalizeEntry(entry(1, "waiting", { github: "ivanov", place: 2 }));
  assert.equal(waiting.name, "Студент 1");
  assert.equal(waiting.group, "4431");
  assert.equal(waiting.github, "ivanov");
  assert.equal(waiting.lab, "ЛР3");
  assert.equal(waiting.place, 2);
  assert.equal(waiting.createdAt, "2026-11-05T12:01:00Z");
  assert.equal(waiting.calledAt, null);

  const called = normalizeEntry(entry(2, "called", { called_at: "2026-11-05T12:30:00Z" }));
  assert.equal(called.calledAt, "2026-11-05T12:30:00Z");
  assert.equal(called.github, "");
});

test("splitEntries раскладывает записи по разделам и порядку очереди", () => {
  const { called, waiting, finished } = splitEntries([
    entry(5, "waiting"),
    entry(3, "waiting"),
    entry(4, "waiting"),
    entry(2, "called", { called_at: "2026-11-05T12:20:00Z" }),
    entry(1, "called", { called_at: "2026-11-05T12:15:00Z" }),
    entry(6, "defended", { finished_at: "2026-11-05T12:40:00Z" }),
    entry(7, "left", { finished_at: "2026-11-05T12:50:00Z" }),
  ]);
  assert.deepEqual(waiting.map((e) => [e.id, e.place]), [[3, 1], [4, 2], [5, 3]]);
  assert.deepEqual(called.map((e) => e.id), [1, 2]);
  assert.deepEqual(finished.map((e) => e.id), [7, 6]);
});

test("splitEntries берёт место с сервера, если оно есть", () => {
  const { waiting } = splitEntries([entry(9, "waiting", { place: 2 }), entry(8, "waiting", { place: 1 })]);
  assert.deepEqual(waiting.map((e) => [e.id, e.place]), [[8, 1], [9, 2]]);
});

test("sessionSummary считает записи занятия по статусам", () => {
  const summary = sessionSummary([
    entry(1, "defended"),
    entry(2, "defended"),
    entry(3, "not_defended"),
    entry(4, "no_show"),
    entry(5, "left"),
    entry(6, "waiting"),
    entry(7, "waiting"),
    entry(8, "waiting"),
    entry(9, "called"),
    entry(10, "expired"),
  ]);
  assert.deepEqual(summary, {
    defended: 2,
    not_defended: 1,
    no_show: 1,
    left: 1,
    waiting: 3,
    called: 1,
    expired: 1,
  });
});

test("sessionSummary: пустое занятие - нули, неизвестный статус не считается", () => {
  const zero = { defended: 0, not_defended: 0, no_show: 0, left: 0, waiting: 0, called: 0, expired: 0 };
  assert.deepEqual(sessionSummary([]), zero);
  assert.deepEqual(sessionSummary(undefined), zero);
  assert.deepEqual(sessionSummary([entry(1, "archived"), null, entry(2, "toString")]), zero);
});

test("summaryItems: шесть итогов по порядку, «запись закрыта» - только если есть", () => {
  const items = summaryItems(sessionSummary([entry(1, "defended"), entry(2, "waiting")]));
  assert.deepEqual(items.map((i) => i.status), SUMMARY_STATUSES);
  assert.deepEqual(
    items.map((i) => i.count),
    [1, 0, 0, 0, 1, 0]
  );
  const withExpired = summaryItems(sessionSummary([entry(1, "expired"), entry(2, "expired")]));
  assert.deepEqual(withExpired.at(-1), { status: "expired", count: 2 });
  assert.equal(withExpired.length, SUMMARY_STATUSES.length + 1);
});

test("tileResults: исходы защит для карточки, пока их нет - null", () => {
  assert.equal(tileResults(sessionSummary([entry(1, "waiting"), entry(2, "called"), entry(3, "left")])), null);
  assert.deepEqual(
    tileResults(sessionSummary([entry(1, "defended"), entry(2, "no_show"), entry(3, "no_show")])),
    { defended: 1, notDefended: 0, noShow: 2 }
  );
});

test("sessionActions: открытая запись с очередью", () => {
  const session = { ...SESSION, signup: "open", entries: [entry(1, "waiting"), entry(2, "called")] };
  const actions = sessionActions(session, "2026-11-05T12:30:00Z");
  assert.equal(actions.canCallNext, true);
  assert.equal(actions.canClose, true);
  assert.equal(actions.canReopen, false);
  assert.equal(actions.canFinish, true);
});

test("sessionActions: состояние записи берётся с сервера", () => {
  // курс в архиве: по времени запись шла бы, но сервер отдаёт closed
  const session = { ...SESSION, signup: "closed", entries: [entry(1, "waiting")] };
  const actions = sessionActions(session, "2026-11-05T12:30:00Z");
  assert.equal(actions.canClose, false);
  assert.equal(actions.canReopen, false);
  assert.equal(actions.canCallNext, true);
});

test("sessionActions: закрытую вручную можно открыть снова до конца окна", () => {
  const session = { ...SESSION, signup: "closed", closed_at: "2026-11-05T12:20:00Z", entries: [entry(1, "waiting")] };
  const during = sessionActions(session, "2026-11-05T12:30:00Z");
  assert.equal(during.canClose, false);
  assert.equal(during.canReopen, true);
  // ждущие остались - приём можно завершить
  assert.equal(during.canFinish, true);

  const after = sessionActions(session, "2026-11-05T14:00:00Z");
  assert.equal(after.canReopen, false);
});

test("sessionActions: после пары записи нет, но ждущих принимают до конца дня", () => {
  const session = { ...SESSION, signup: "closed", entries: [entry(1, "waiting")] };
  const evening = sessionActions(session, "2026-11-05T17:00:00Z");
  assert.equal(evening.canCallNext, true);
  assert.equal(evening.canClose, false);
  assert.equal(evening.canFinish, true);

  const dayOver = sessionActions(session, "2026-11-05T21:00:00Z");
  assert.equal(dayOver.dayOver, true);
  assert.equal(dayOver.canCallNext, false);
  assert.equal(dayOver.canFinish, false);
});

test("sessionActions: пустая сессия после завершения приёма", () => {
  const session = { ...SESSION, signup: "closed", closed_at: "2026-11-05T13:00:00Z", entries: [entry(1, "expired")] };
  const actions = sessionActions(session, "2026-11-05T13:10:00Z");
  assert.equal(actions.canCallNext, false);
  assert.equal(actions.canFinish, false);
});

test("entryActions повторяет правила set_outcome и return_to_queue", () => {
  const called = normalizeEntry(entry(1, "called", { called_at: "2026-11-05T12:30:00Z" }));
  assert.deepEqual(entryActions(called, false), { outcomes: OUTCOMES, canReturn: true, fix: false });
  assert.equal(entryActions(called, true).canReturn, false);

  const noShow = normalizeEntry(entry(2, "no_show"));
  assert.deepEqual(entryActions(noShow, false), {
    outcomes: ["defended", "not_defended"],
    canReturn: true,
    fix: true,
  });

  const defended = normalizeEntry(entry(3, "defended"));
  assert.deepEqual(entryActions(defended, false).outcomes, ["not_defended", "no_show"]);
  assert.equal(entryActions(defended, false).canReturn, false);

  // Закрыта в конце дня после вызова - исход забыли отметить
  const forgotten = normalizeEntry(entry(4, "expired", { called_at: "2026-11-05T12:30:00Z" }));
  assert.deepEqual(entryActions(forgotten, true), { outcomes: OUTCOMES, canReturn: false, fix: true });

  for (const status of ["expired", "left", "waiting"]) {
    assert.deepEqual(entryActions(normalizeEntry(entry(5, status)), false).outcomes, []);
  }
});

test("pickSessionId: выбор сохраняется, иначе - где ждут, где открыто, первая", () => {
  const sessions = [
    { id: 1, signup: "closed", entries: [] },
    { id: 2, signup: "open", entries: [] },
    { id: 3, signup: "closed", entries: [entry(1, "waiting")] },
  ];
  assert.equal(pickSessionId(sessions, 1), 1);
  assert.equal(pickSessionId(sessions, 99), 3);
  assert.equal(pickSessionId(sessions.slice(0, 2), null), 2);
  assert.equal(pickSessionId([{ id: 5, entries: [] }], null), 5);
  assert.equal(pickSessionId([], 1), null);
});

test("manualRequest: все группы, выбранные группы, время", () => {
  assert.deepEqual(manualRequest({ allGroups: true, selected: ["4431"], closesAt: "" }), {
    groups: [],
    closesAt: null,
  });
  assert.deepEqual(manualRequest({ allGroups: false, selected: ["4431", "4432"], closesAt: "18:30" }), {
    groups: ["4431", "4432"],
    closesAt: "18:30",
  });
  assert.deepEqual(manualRequest({ allGroups: false, selected: [], closesAt: "" }), { error: "INVALID_GROUPS" });
  assert.deepEqual(manualRequest({ allGroups: true, selected: [], closesAt: "25:00" }), { error: "INVALID_TIME" });
  assert.deepEqual(manualRequest({ allGroups: true, selected: [], closesAt: "9:30" }), { error: "INVALID_TIME" });
});

test("historyQuery убирает пустые фильтры", () => {
  assert.deepEqual(historyQuery({ from: "2026-10-01", to: "", group: " ", q: " Иван ", lab: null }), {
    from: "2026-10-01",
    q: "Иван",
  });
});

test("csvCell экранирует разделитель, кавычки и формулы", () => {
  assert.equal(csvCell("Иванов Иван"), "Иванов Иван");
  assert.equal(csvCell("a;b"), '"a;b"');
  assert.equal(csvCell('ЛР "3"'), '"ЛР ""3"""');
  assert.equal(csvCell("строка\nвторая"), '"строка\nвторая"');
  assert.equal(csvCell("=HYPERLINK(\"x\")"), '"\'=HYPERLINK(""x"")"');
  assert.equal(csvCell("+7"), "'+7");
  assert.equal(csvCell(null), "");
  assert.equal(csvCell(3), "3");
});

test("historyToCsv: дата и время в поясе курса, статус переведён", () => {
  const csv = historyToCsv(
    [
      {
        id: 1,
        name: "Иванов И. И.",
        group: "4431",
        github: "ivanov",
        lab: "ЛР3",
        status: "defended",
        created_at: "2026-11-05T21:30:00Z",
        called_at: "2026-11-05T21:40:00Z",
        finished_at: "2026-11-05T21:50:00Z",
      },
    ],
    { t: fakeT, offsetMinutes: 180 }
  );
  const [header, row] = csv.split("\r\n");
  assert.equal(header.split(";").length, 9);
  assert.equal(header.split(";")[0], "adminQueue.columns.day");
  // 21:30 UTC - это уже 6 ноября по Москве
  assert.equal(row, "2026-11-06;00:30;Иванов И. И.;4431;ivanov;ЛР3;queue.status.defended;00:40;00:50");
});

test("historyToCsv: день из ответа главнее, пустые моменты - пустые ячейки", () => {
  const csv = historyToCsv(
    [{ id: 2, day: "2026-11-04", name: "П", status: "expired", created_at: "2026-11-04T10:00:00Z" }],
    { t: fakeT, offsetMinutes: 180 }
  );
  assert.equal(csv.split("\r\n")[1], "2026-11-04;13:00;П;;;;queue.status.expired;;");
});

test("historyToCsv: вызов и исход в другой день - с датой", () => {
  // вызван 05.11 в 21:09 по Москве, исход отмечен из истории 06.11 в 20:08
  const csv = historyToCsv(
    [
      {
        id: 3,
        day: "2026-11-05",
        name: "Иванов Иван",
        status: "defended",
        created_at: "2026-11-05T18:05:00Z",
        called_at: "2026-11-05T18:09:00Z",
        finished_at: "2026-11-06T17:08:00Z",
      },
    ],
    { t: fakeT, offsetMinutes: 180 }
  );
  assert.equal(csv.split("\r\n")[1], "2026-11-05;21:05;Иванов Иван;;;;queue.status.defended;21:09;2026-11-06 20:08");
});

test("historyToCsv: запись накануне пары в 00:05 - с датой записи", () => {
  const csv = historyToCsv(
    [{ id: 4, day: "2026-11-06", name: "П", status: "waiting", created_at: "2026-11-05T20:50:00Z" }],
    { t: fakeT, offsetMinutes: 180 }
  );
  assert.equal(csv.split("\r\n")[1], "2026-11-06;2026-11-05 23:50;П;;;;queue.status.waiting;;");
});

test("historyMoment: время того же дня, иначе дата и время", () => {
  assert.equal(historyMoment("2026-11-05T18:09:00Z", "2026-11-05", 180, "ru"), "21:09");
  assert.equal(historyMoment("2026-11-06T17:08:00Z", "2026-11-05", 180, "ru"), "06.11, 20:08");
  // день считается в поясе курса: 21:30 UTC - уже 6 ноября по Москве
  assert.equal(historyMoment("2026-11-05T21:30:00Z", "2026-11-06", 180, "ru"), "00:30");
  assert.equal(historyMoment("2026-11-05T21:30:00Z", "2026-11-05", 180, "ru"), "06.11, 00:30");
  assert.equal(historyMoment(null, "2026-11-05", 180, "ru"), "");
  assert.equal(historyMoment("не дата", "2026-11-05", 180, "ru"), "");
});

test("entryDay и historyDayLabel: день сессии главнее дня записи", () => {
  const raw = { id: 1, status: "waiting", created_at: "2026-11-05T20:50:00Z" };
  assert.equal(entryDay(normalizeEntry({ ...raw, day: "2026-11-06" }), 180), "2026-11-06");
  assert.equal(entryDay(normalizeEntry(raw), 180), "2026-11-05");
  assert.equal(entryDay(normalizeEntry(raw), 240), "2026-11-06");
  assert.equal(historyDayLabel("2026-11-06", "ru"), "06.11.2026");
  assert.equal(historyDayLabel("", "ru"), "");
  assert.equal(historyDayLabel(undefined, "ru"), "");
});

test("historyPage: по HISTORY_PAGE_SIZE строк, номер не выходит за последнюю страницу", () => {
  const rows = Array.from({ length: 250 }, (_, i) => ({ id: i }));
  assert.equal(HISTORY_PAGE_SIZE, 100);
  const first = historyPage(rows, 0);
  assert.equal(first.page, 0);
  assert.equal(first.rows.length, 100);
  assert.equal(first.rows[0].id, 0);
  const last = historyPage(rows, 2);
  assert.deepEqual([last.page, last.rows.length, last.rows[0].id], [2, 50, 200]);
  // после исхода строк стало меньше: остаёмся на последней существующей странице
  assert.deepEqual([historyPage(rows.slice(0, 120), 2).page, historyPage(rows.slice(0, 120), 2).rows.length], [1, 20]);
  assert.deepEqual(historyPage([], 3), { page: 0, rows: [] });
  assert.equal(historyPage(rows, -1).page, 0);
  assert.equal(historyPage(rows, undefined).page, 0);
  assert.equal(historyPage(rows, 1, 50).rows[0].id, 50);
});

test("historyFileName не пропускает в имя файла лишних символов", () => {
  assert.equal(historyFileName("os-2026", "2026-10-01", "2026-11-05"), "queue-os-2026-2026-10-01-2026-11-05.csv");
  assert.equal(historyFileName("a/b", null, undefined), "queue-a_b--.csv");
});

test("safeAdminNext возвращает только на страницы админки", () => {
  assert.equal(safeAdminNext("/admin/courses/os/queue"), "/admin/courses/os/queue");
  assert.equal(safeAdminNext("/admin/courses/os/queue?x=1"), "/admin/courses/os/queue?x=1");
  assert.equal(safeAdminNext(null), "/admin/courses");
  assert.equal(safeAdminNext("/admin"), "/admin/courses");
  assert.equal(safeAdminNext("https://evil.example/admin/"), "/admin/courses");
  assert.equal(safeAdminNext("//evil.example/admin/"), "/admin/courses");
  assert.equal(safeAdminNext("/course/os/queue"), "/admin/courses");
  assert.equal(safeAdminNext("/admin/\\evil"), "/admin/courses");
});

test("adminLoginPath кодирует адрес возврата", () => {
  const path = adminLoginPath("/admin/courses/os/queue?tab=1");
  assert.equal(path, "/admin?next=%2Fadmin%2Fcourses%2Fos%2Fqueue%3Ftab%3D1");
  assert.equal(new URLSearchParams(path.split("?")[1]).get("next"), "/admin/courses/os/queue?tab=1");
});


// --- Исход из истории: запись прошлого дня ---

// Вызвана в пн 09.11 в 15:20 по Москве, исход не отмечен; в полночь запись
// закрылась. Строка истории - как её отдаёт GET .../queue/history.
const FORGOTTEN = {
  id: 7,
  session_id: 3,
  status: "expired",
  name: "Иванов Иван",
  group: "4431",
  github: "ivanov",
  lab: "ЛР3",
  lab_key: "3",
  day: "2026-11-09",
  created_at: "2026-11-09T12:00:00+00:00",
  called_at: "2026-11-09T12:20:00+00:00",
  finished_at: "2026-11-09T21:00:00+00:00",
};

test("история: у забытого вызванного - «Отметить исход», у завершённых - «Исправить исход»", () => {
  const forgotten = normalizeEntry(FORGOTTEN);
  assert.equal(historyOutcomeButton(forgotten), "set");
  // Диалог открывается с dayOver=true: любой исход, вернуть в очередь нельзя
  assert.deepEqual(entryActions(forgotten, true), { outcomes: OUTCOMES, canReturn: false, fix: true });

  for (const status of OUTCOMES) {
    const done = normalizeEntry({ ...FORGOTTEN, status });
    assert.equal(historyOutcomeButton(done), "fix", status);
    assert.equal(entryActions(done, true).canReturn, false, status);
  }
});

test("история: без кнопки исхода - не вызванные, вышедшие и ещё идущие записи", () => {
  const cases = [
    { status: "expired", called_at: null },
    { status: "left", called_at: null },
    { status: "waiting", called_at: null },
    // вызванный сегодня - его исход ставят на вкладке «Сегодня»
    { status: "called" },
  ];
  for (const extra of cases) {
    assert.equal(historyOutcomeButton(normalizeEntry({ ...FORGOTTEN, finished_at: null, ...extra })), null, extra.status);
  }
});

test("outcomeButton: те же кнопки в завершённых записях сессии дня", () => {
  assert.equal(outcomeButton(normalizeEntry(entry(1, "defended")), false), "fix");
  assert.equal(outcomeButton(normalizeEntry(entry(2, "expired", { called_at: "2026-11-05T12:30:00Z" })), true), "set");
  assert.equal(outcomeButton(normalizeEntry(entry(3, "expired")), true), null);
  assert.equal(outcomeButton(normalizeEntry(entry(4, "left")), false), null);
});


// --- Период истории ---

test("historyPeriodError: «С» позже «По» отклоняется до запроса", () => {
  assert.equal(historyPeriodError({ from: "2026-11-10", to: "2026-11-01" }), "INVALID_PERIOD");
  assert.equal(historyPeriodError({ from: "2026-12-31", to: "2027-01-01" }), null);
  assert.equal(historyPeriodError({ from: "2026-11-05", to: "2026-11-05" }), null);
  // пустая дата - период выбирает сервер
  assert.equal(historyPeriodError({ from: "2026-11-10", to: "" }), null);
  assert.equal(historyPeriodError({ from: "", to: "2026-11-01" }), null);
  assert.equal(historyPeriodError(null), null);
});

test("historyErrorCode: неразобранная дата истории - ошибка периода, не окна записи", () => {
  assert.equal(historyErrorCode("INVALID_TIME"), "INVALID_PERIOD");
  assert.equal(historyErrorCode("INVALID_PERIOD"), "INVALID_PERIOD");
  assert.equal(historyErrorCode("STORAGE_UNAVAILABLE"), "STORAGE_UNAVAILABLE");
  assert.equal(historyErrorCode(undefined), "unknown");
});


// --- Ошибки и состояние курса ---

test("adminErrorKeys: сначала текст для преподавателя, затем студенческий", () => {
  assert.deepEqual(adminErrorKeys("ALREADY_IN_QUEUE"), [
    "adminQueue.errors.ALREADY_IN_QUEUE",
    "queue.errors.ALREADY_IN_QUEUE",
  ]);
  for (const code of ["ENTRY_NOT_CALLED", "DAY_OVER", "SIGNUP_CLOSED", "QUEUE_CHANGED", "QUEUE_EMPTY", "INVALID_PERIOD"]) {
    assert.ok(ADMIN_ERROR_CODES.includes(code), code);
    assert.equal(adminErrorKeys(code)[0], `adminQueue.errors.${code}`);
  }
  // Свой текст не нужен - перевод как у студента
  assert.deepEqual(adminErrorKeys("STORAGE_UNAVAILABLE"), ["queue.errors.STORAGE_UNAVAILABLE"]);
  // Неизвестный код в ключ не попадает
  assert.deepEqual(adminErrorKeys("SOMETHING_NEW"), ["queue.errors.unknown"]);
  assert.deepEqual(adminErrorKeys("INVALID_PERIOD"), ["adminQueue.errors.INVALID_PERIOD", "queue.errors.unknown"]);
});

test("queueNotice: архивный курс - не «выключена в конфигурации»", () => {
  const base = { queue_enabled: false, config_error: null };
  assert.equal(queueNotice({ ...base, course_status: "archived" }), "archived");
  assert.equal(queueNotice({ ...base, course_status: "hidden" }), "archived");
  assert.equal(queueNotice({ ...base, course_status: "active" }), "disabled");
  // без course_status курс считается активным
  assert.equal(queueNotice(base), "disabled");
  assert.equal(queueNotice({ ...base, queue_enabled: true, course_status: "active" }), null);
  assert.equal(queueNotice(null), null);
});

test("queueNotice: ошибка в секции queue - не «выключена в конфигурации»", () => {
  // enabled: "true" - ошибка конфигурации, её показывает config_error
  const broken = { queue_enabled: false, course_status: "active", config_error: "Некорректное значение queue.enabled" };
  assert.equal(queueNotice(broken), null);
  // курс не активен - это сказать всё равно нужно
  assert.equal(queueNotice({ ...broken, course_status: "archived" }), "archived");
});

test("canOpenManual: только у работающей очереди с верным конфигом", () => {
  assert.equal(canOpenManual({ queue_enabled: true, config_error: null }), true);
  assert.equal(canOpenManual({ queue_enabled: false, course_status: "archived", config_error: null }), false);
  assert.equal(canOpenManual({ queue_enabled: true, config_error: "queue.schedule: ..." }), false);
  assert.equal(canOpenManual(null), false);
});


// --- Действие преподавателя и истёкший вход ---

function actionSpy() {
  const calls = { success: [], error: [], unauthorized: 0, refresh: 0 };
  return {
    calls,
    handlers: {
      onSuccess: (result) => calls.success.push(result),
      onError: (error) => calls.error.push(error.code),
      onUnauthorized: () => {
        calls.unauthorized += 1;
      },
      refresh: () => {
        calls.refresh += 1;
      },
    },
  };
}

const failWith = (status, code) => async () => {
  const error = new Error("Queue request failed");
  error.status = status;
  error.code = code;
  throw error;
};

test("runAdminAction: после 401 экран не перечитывается - на вход уходят один раз", async () => {
  const { calls, handlers } = actionSpy();
  const result = await runAdminAction(failWith(401, "SESSION_REQUIRED"), handlers);
  assert.equal(result, null);
  assert.equal(calls.unauthorized, 1);
  assert.equal(calls.refresh, 0);
  assert.deepEqual(calls.error, []);
});

test("runAdminAction: отказ сервера показывается, экран перечитывается", async () => {
  const { calls, handlers } = actionSpy();
  assert.equal(await runAdminAction(failWith(409, "ALREADY_IN_QUEUE"), handlers), null);
  assert.deepEqual(calls.error, ["ALREADY_IN_QUEUE"]);
  assert.equal(calls.refresh, 1);
  assert.equal(calls.unauthorized, 0);
});

test("runAdminAction: успех отдаёт ответ и перечитывает экран", async () => {
  const { calls, handlers } = actionSpy();
  const state = { sessions: [], entry: { id: 7, status: "defended" } };
  assert.equal(await runAdminAction(async () => state, handlers), state);
  assert.deepEqual(calls.success, [state]);
  assert.equal(calls.refresh, 1);
  // пустой ответ - всё равно успех
  assert.deepEqual(await runAdminAction(async () => undefined, actionSpy().handlers), {});
});

test("вход после 401 возвращает на очередь курса, а не на /admin/courses", () => {
  const path = adminLoginPath(adminQueuePath("os"));
  assert.equal(path, "/admin?next=%2Fadmin%2Fcourses%2Fos%2Fqueue");
  const next = new URLSearchParams(path.split("?")[1]).get("next");
  assert.equal(safeAdminNext(next), "/admin/courses/os/queue");
  // Адрес не зависит от адресной строки: второй 401, пойманный уже на
  // странице входа, даёт тот же путь, без вложенного next=/admin?next=...
  assert.equal(adminLoginPath(adminQueuePath("os")), path);
  assert.equal(adminQueuePath("a/b"), "/admin/courses/a%2Fb/queue");
});
