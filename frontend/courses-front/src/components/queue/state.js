// Чистая логика страницы очереди на защиту (без React): её и тестируют.
// Ответ backend - GET /courses/{id}/queue в main.py (_student_state).

// Коды ошибок backend (QueueError и HTTPException в main.py), причины
// неудачного входа из _queue_oauth_callback и коды, которые выводит src/api/queue.js.
// Для каждого есть перевод queue.errors.<CODE> во всех трёх языках.
export const QUEUE_ERROR_CODES = [
  "SESSION_REQUIRED",
  "QUEUE_DISABLED",
  "SESSION_NOT_FOUND",
  "LAB_NOT_FOUND",
  "ENTRY_NOT_FOUND",
  "NOT_IN_QUEUE",
  "COURSE_NOT_FOUND",
  "NOT_YOUR_GROUP",
  "STUDENT_NOT_FOUND",
  "BAD_ORIGIN",
  "SIGNUP_CLOSED",
  "LAB_NOT_PASSED",
  "LAB_ALREADY_DEFENDED",
  "ALREADY_IN_QUEUE",
  "ALREADY_CALLED",
  "NOTHING_TO_SKIP",
  "QUEUE_EMPTY",
  "QUEUE_CHANGED",
  "ENTRY_NOT_CALLED",
  "DAY_OVER",
  "STUDENT_AMBIGUOUS",
  "INVALID_OUTCOME",
  "INVALID_GROUPS",
  "INVALID_TIME",
  "QUEUE_MISCONFIGURED",
  "SHEETS_UNAVAILABLE",
  "STORAGE_UNAVAILABLE",
  // /join/callback для очереди: ?status=error&reason=...
  "state_expired",
  "state_mismatch",
  "access_denied",
  "missing_code",
  "oauth_not_configured",
  "oauth_exchange_failed",
  // src/api/queue.js: ответа с кодом нет
  "request_timeout",
  "network_error",
  "rate_limit",
  "not_found",
  "unknown",
];

const KNOWN_ERROR_CODES = new Set(QUEUE_ERROR_CODES);

// Ключ перевода ошибки. Код может прийти из адресной строки, поэтому в ключ
// попадают только известные коды.
export function errorKey(code) {
  return KNOWN_ERROR_CODES.has(code) ? `queue.errors.${code}` : "queue.errors.unknown";
}

export const SESSION_KINDS = ["schedule", "manual", "all_day"];

export const ENTRY_STATUSES = [
  "waiting",
  "called",
  "defended",
  "not_defended",
  "no_show",
  "left",
  "expired",
];

// Ключ перевода статуса записи.
export function statusKey(status) {
  return ENTRY_STATUSES.includes(status) ? `queue.status.${status}` : "queue.status.unknown";
}


/**
 * Заголовок сессии. Тип сессии переводится, а lesson_type и subject - данные
 * источника расписания («Лабораторное занятие», «Операционные системы») и
 * показываются как есть.
 */
export function sessionTitle(session, t) {
  const kind = session && SESSION_KINDS.includes(session.kind) ? session.kind : "schedule";
  if (kind !== "schedule") return t(`queue.session.kind.${kind}`);

  const type = (session.lesson_type || "").trim();
  const subject = (session.subject || "").trim();
  if (type && subject) return t("queue.session.typeAndSubject", { type, subject });
  return type || subject || t("queue.session.kind.schedule");
}

// Группы сессии через запятую; пустой список - все группы курса.
export function groupsLabel(session, t) {
  const groups = (session && session.groups) || [];
  return groups.length ? groups.join(", ") : t("queue.session.allGroups");
}


/**
 * Время в поясе курса: «15:10» у всех студентов - время пары, а не их ноутбука.
 * Intl не принимает «UTC+3» как timeZone, поэтому backend отдаёт смещение в
 * минутах: момент сдвигается на него и печатается как UTC. Без смещения
 * (состояние дня ещё не пришло) - пояс браузера.
 */
export function formatInCourseTz(iso, offsetMinutes, lang, options) {
  if (!iso) return "";
  const millis = Date.parse(iso);
  if (Number.isNaN(millis)) return "";

  const format = options || { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  if (typeof offsetMinutes !== "number" || !Number.isFinite(offsetMinutes)) {
    return new Date(millis).toLocaleString(lang || undefined, format);
  }
  return new Date(millis + offsetMinutes * 60000).toLocaleString(lang || undefined, {
    ...format,
    timeZone: "UTC",
  });
}

// День "YYYY-MM-DD" (уже в поясе курса) в читаемом виде.
export function formatDay(day, lang) {
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  const millis = Date.parse(`${day}T00:00:00Z`);
  if (Number.isNaN(millis)) return "";
  return new Date(millis).toLocaleDateString(lang || undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
}

// «15:10–16:40» в поясе курса.
export function sessionTimeRange(session, offsetMinutes, lang) {
  const start = formatInCourseTz(session.starts_at, offsetMinutes, lang);
  const end = formatInCourseTz(session.ends_at, offsetMinutes, lang);
  return start && end ? `${start}–${end}` : start || end;
}

// Состояние записи на сессию: «откроется в», «открыта до» или «закрыта».
export function signupText(session, t, offsetMinutes, lang) {
  if (session.signup === "upcoming") {
    return t("queue.signup.upcoming", {
      time: formatInCourseTz(session.signup_opens_at, offsetMinutes, lang),
    });
  }
  if (session.signup === "open") {
    return t("queue.signup.open", {
      time: formatInCourseTz(session.signup_closes_at, offsetMinutes, lang),
    });
  }
  return t("queue.signup.closed");
}


// --- Опрос ---

export const POLL_ADMIN_MS = 5000;
export const POLL_STUDENT_MS = 10000;
export const POLL_ANONYMOUS_MS = 60000;
export const POLL_MAX_BACKOFF_MS = 60000;

/**
 * Пауза до следующего опроса; null - не опрашивать (вкладка скрыта).
 *
 * Аноним видит только счётчики, ему хватает раза в минуту. После ошибок
 * интервал удваивается до минуты: недоступный backend или лимит запросов
 * не должны превращаться в поток повторов от всей группы.
 */
export function nextPollDelay({
  admin = false,
  loggedIn = false,
  failures = 0,
  rateLimited = false,
  hidden = false,
} = {}) {
  if (hidden) return null;
  const base = admin ? POLL_ADMIN_MS : loggedIn ? POLL_STUDENT_MS : POLL_ANONYMOUS_MS;
  const cap = Math.max(base, POLL_MAX_BACKOFF_MS);
  if (rateLimited) return cap;
  if (failures > 0) return Math.min(base * 2 ** Math.min(failures, 4), cap);
  return base;
}

/**
 * Цепочка опросов на setTimeout: следующий запрос уходит только после ответа
 * на предыдущий, и медленный backend не копит запросы, как с setInterval.
 *
 * `poll` выполняет один запрос и возвращает паузу до следующего (мс) или
 * null. После stop() запросов больше нет, в том числе повтора того, что был в пути.
 */
export function createPoller(
  poll,
  { isHidden = () => false, setTimer = setTimeout, clearTimer = clearTimeout } = {}
) {
  let timer = null;
  let running = false;
  let rerun = false;
  let active = false;

  const pause = () => {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  };

  const run = async () => {
    pause();
    if (!active) return;
    if (running) {
      // Запрос уже в пути: повторить сразу после него, а не параллельно.
      rerun = true;
      return;
    }
    running = true;
    let delay = null;
    try {
      delay = await poll();
    } catch {
      delay = null;
    } finally {
      running = false;
    }
    if (!active) return;
    if (rerun) {
      rerun = false;
      run();
      return;
    }
    if (typeof delay === "number" && !isHidden()) timer = setTimer(run, delay);
  };

  return {
    run,
    pause,
    start() {
      active = true;
      run();
    },
    stop() {
      active = false;
      rerun = false;
      pause();
    },
  };
}

// Ошибки связи, а не отказ сервера: после удачного опроса они уже неправда.
const TRANSIENT_ERROR_CODES = new Set(["network_error", "request_timeout", "rate_limit"]);
export const ACTION_ERROR_MIN_MS = 5000;

/**
 * Ошибка действия ({ code, at }) после удачного опроса, начатого в `pollStartedAt`.
 *
 * Ошибка связи снимается, но только опросом, начатым не раньше чем через
 * ACTION_ERROR_MIN_MS после неё: опрос сразу после действия смыл бы её
 * раньше, чем студент её прочитал. Отказ сервера (SIGNUP_CLOSED и т. п.)
 * остаётся до следующего действия.
 */
export function actionErrorAfterPoll(actionError, pollStartedAt) {
  if (!actionError || !TRANSIENT_ERROR_CODES.has(actionError.code)) return actionError;
  return pollStartedAt - actionError.at >= ACTION_ERROR_MIN_MS ? null : actionError;
}

/**
 * Ответ запроса, начатого позже, всегда побеждает.
 *
 * Опрос и действие студента идут параллельно: GET, отправленный до «Выйти»,
 * может вернуться после ответа на POST и вернуть на экран старую запись.
 */
export function createLatestGate() {
  let issued = 0;
  let applied = 0;
  return {
    begin() {
      issued += 1;
      return issued;
    },
    accept(ticket) {
      if (ticket <= applied) return false;
      applied = ticket;
      return true;
    },
  };
}


// --- Студент ---

// Без этих ответов опрашивать дальше бессмысленно: курс не найден или
// очередь у него выключена.
export const STOP_POLLING_CODES = new Set(["QUEUE_DISABLED", "COURSE_NOT_FOUND", "not_found"]);

// Ответы, которые вход не исправит. Курс без очереди или с ошибкой в её
// настройках вход отклонит с той же причиной, а при BAD_ORIGIN сервер не
// считает эту страницу своей и после входа вернёт на другой адрес.
const LOGIN_USELESS_CODES = new Set([...STOP_POLLING_CODES, "QUEUE_MISCONFIGURED", "BAD_ORIGIN"]);

/**
 * Показывать ли «Войти через GitHub». Вход - переход браузера и от опроса не
 * зависит: если анонимный опрос упал (лимит запросов, сеть), войти всё равно
 * можно. Не предлагается, только когда вход заведомо вернёт ошибку.
 */
export function canBeginLogin(state, pollError) {
  if (LOGIN_USELESS_CODES.has(pollError)) return false;
  if (state) return !state.viewer;
  return Boolean(pollError);
}

/**
 * Причина неудачного входа для отдельной плашки. Если опрос упал по той
 * же причине, вторая плашка с тем же текстом не нужна: остаётся ошибка
 * опроса с «Повторить».
 */
export function loginErrorToShow(callback, pollError) {
  if (!callback || callback.status !== "error") return null;
  return callback.reason === pollError ? null : callback.reason;
}

/**
 * Что показать на месте списка работ в форме записи. "idle" - списка нет и
 * он не грузится (запрос не начат или ответ без списка): нужна кнопка
 * «Повторить», иначе форма навсегда осталась бы без работ.
 */
export function labsView({ labs, labsError, labsLoading }) {
  const list = labs && Array.isArray(labs.labs) ? labs.labs : null;
  if (list) return list.length ? "list" : "empty";
  if (labsError) return "error";
  return labsLoading ? "loading" : "idle";
}

function isMySession(session) {
  return Boolean(session && session.for_me);
}

// Сессии группы студента, на которые запись открыта сейчас.
export function openSessionsForMe(state) {
  if (!state || !state.viewer) return [];
  return (state.sessions || []).filter((s) => isMySession(s) && s.signup === "open");
}

// Сессии группы студента, запись на которые ещё не открылась.
export function upcomingSessionsForMe(state) {
  if (!state || !state.viewer) return [];
  return (state.sessions || []).filter((s) => isMySession(s) && s.signup === "upcoming");
}

/**
 * Лабораторная из ?lab=. Автопредложение после проверки знает только
 * short-name ("ЛР3"), ссылка из других мест - ключ конфига ("3"), поэтому
 * подходит любое из двух.
 */
export function findLab(labs, param) {
  if (!param || !Array.isArray(labs)) return null;
  const wanted = String(param);
  return (
    labs.find((lab) => String(lab.id) === wanted) ||
    labs.find((lab) => lab.short_name === wanted) ||
    null
  );
}

// Защищённую работу выбрать нельзя. Не прошедшую - можно: таблица в кэше
// могла отстать, окончательно её перепроверит сервер при записи.
export function isLabSelectable(lab) {
  return Boolean(lab) && lab.status !== "defended";
}

/**
 * Выбор в форме записи после очередного обновления: сохраняем выбранное
 * студентом, пока оно допустимо, иначе - первое подходящее.
 */
export function resolveSignupChoice({ openSessions, labs, current, labParam }) {
  const sessions = openSessions || [];
  const sessionIds = sessions.map((s) => s.id);
  const sessionId = sessionIds.includes(current && current.sessionId)
    ? current.sessionId
    : sessionIds.length
      ? sessionIds[0]
      : null;

  // Значение <select> - всегда строка, а id с сервера может прийти числом.
  const selectable = (labs || []).filter(isLabSelectable);
  const wanted = current && current.labId != null ? String(current.labId) : null;
  let labId = selectable.some((lab) => String(lab.id) === wanted) ? wanted : null;
  if (labId === null) {
    const fromParam = findLab(labs, labParam);
    const passed = selectable.find((lab) => lab.status === "passed");
    if (isLabSelectable(fromParam)) labId = String(fromParam.id);
    else if (passed) labId = String(passed.id);
  }
  return { sessionId, labId };
}

// Итог последней записи показывается, только пока студент снова не встал.
export function lastResultToShow(state) {
  if (!state || !state.viewer || state.my_entry) return null;
  return state.my_last_result || null;
}

// Результат входа из адреса возврата /join/callback: ?status=ok|error&reason=CODE.
export function readCallback(searchParams) {
  const status = searchParams.get("status");
  if (status === "error") {
    return { status, reason: searchParams.get("reason") || "unknown" };
  }
  if (status === "ok") return { status, reason: null };
  return null;
}


// --- Автопредложение после проверки (registration-form) ---

/**
 * Показывать ли после проверки кнопку «Встать в очередь на защиту».
 * Правило ячейки то же, что lab_cell_status на сервере: начинается с «v», но
 * не с «vv» (уже защищена). При записи сервер проверит ячейку ещё раз.
 */
export function shouldOfferQueue(gradeResponse) {
  if (!gradeResponse) return false;
  let grade = null;
  if (gradeResponse.status === "updated") grade = gradeResponse.result;
  else if (gradeResponse.status === "rejected") {
    grade = gradeResponse.current_grade ?? gradeResponse.result;
  }
  const value = String(grade ?? "").trim().toLowerCase();
  return value.startsWith("v") && !value.startsWith("vv");
}

/**
 * Показывать ли студенту вход в очередь по карточке курса (GET /courses/{id}):
 * очередь включена и курс активный - у архивного запись всё равно не откроется.
 */
export function courseHasQueue(details) {
  return Boolean(details && details.queue_enabled && details.status === "active");
}

// Адрес страницы очереди; lab - работа, которая будет выбрана в форме записи.
export function queuePagePath(courseId, lab) {
  const base = `/course/${encodeURIComponent(courseId)}/queue`;
  return lab ? `${base}?lab=${encodeURIComponent(lab)}` : base;
}
