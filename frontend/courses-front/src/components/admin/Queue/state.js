// Чистая логика страницы очереди преподавателя (без React).
// Ответ backend - GET /admin/courses/{id}/queue в main.py (_admin_queue_state).

import { errorKey, formatInCourseTz, statusKey } from "../../queue/state.js";

export const OUTCOMES = ["defended", "not_defended", "no_show"];
const FINISHED_STATUSES = [...OUTCOMES, "left", "expired"];

// Цвета Chip из MUI для статуса записи и состояния записи на сессию.
export const STATUS_COLOR = {
  waiting: "default",
  called: "info",
  defended: "success",
  not_defended: "error",
  no_show: "warning",
  left: "default",
  expired: "default",
};

export const SIGNUP_COLOR = {
  open: "success",
  upcoming: "warning",
  closed: "default",
};


// Запись очереди из ответа backend (_entry_out) в одном виде для таблиц и CSV.
export function normalizeEntry(raw) {
  return {
    id: raw.id,
    status: raw.status,
    place: raw.place ?? null,
    name: raw.name ?? "",
    group: raw.group ?? "",
    github: raw.github ?? "",
    lab: raw.lab ?? "",
    day: raw.day ?? null,
    createdAt: raw.created_at ?? null,
    calledAt: raw.called_at ?? null,
    finishedAt: raw.finished_at ?? null,
  };
}

const millis = (iso) => {
  const value = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(value) ? null : value;
};

const byMoment = (a, b) => (millis(a) ?? 0) - (millis(b) ?? 0);

// Порядок ждущих - место с сервера, без него - id.
const queueOrder = (entry) => entry.place ?? Number.MAX_SAFE_INTEGER;

/**
 * Записи сессии по разделам страницы: у преподавателя (called), ждут
 * (waiting, по порядку очереди) и завершённые (сначала свежие).
 */
export function splitEntries(entries) {
  const list = (entries || []).map(normalizeEntry);
  const waiting = list
    .filter((e) => e.status === "waiting")
    .sort((a, b) => queueOrder(a) - queueOrder(b) || a.id - b.id)
    .map((e, index) => (e.place === null ? { ...e, place: index + 1 } : e));
  const called = list
    .filter((e) => e.status === "called")
    .sort((a, b) => byMoment(a.calledAt, b.calledAt) || a.id - b.id);
  const finished = list
    .filter((e) => FINISHED_STATUSES.includes(e.status))
    .sort((a, b) => byMoment(b.finishedAt, a.finishedAt) || b.id - a.id);
  return { called, waiting, finished };
}

// Итоги занятия в порядке показа. «Запись закрыта» (expired) - в конце и
// только если такие записи есть: до конца дня или «Завершить приём» их нет.
export const SUMMARY_STATUSES = ["defended", "not_defended", "no_show", "left", "waiting", "called"];

/**
 * Итоги занятия: сколько записей сессии в каждом статусе. Неизвестный
 * статус не считается никуда, а не ломает остальные счётчики.
 */
export function sessionSummary(entries) {
  const counted = [...SUMMARY_STATUSES, "expired"];
  const counts = Object.fromEntries(counted.map((status) => [status, 0]));
  for (const entry of entries || []) {
    if (entry && counted.includes(entry.status)) counts[entry.status] += 1;
  }
  return counts;
}

/**
 * Строки итогов для экрана: [{ status, count }]. Нули показываются - «не
 * явились: 0» тоже итог, кроме expired, у которого 0 - обычное дело.
 */
export function summaryItems(summary) {
  const items = SUMMARY_STATUSES.map((status) => ({ status, count: summary[status] || 0 }));
  if (summary.expired) items.push({ status: "expired", count: summary.expired });
  return items;
}

/**
 * Кратко для карточки сессии: исходы защит, если хоть одна уже закончилась,
 * иначе null - пустая строка «защитили: 0 ...» в начале пары не нужна.
 */
export function tileResults(summary) {
  const { defended, not_defended: notDefended, no_show: noShow } = summary;
  return defended + notDefended + noShow > 0 ? { defended, notDefended, noShow } : null;
}

function activeCount(session) {
  const entries = (session && session.entries) || [];
  return entries.filter((e) => e.status === "waiting" || e.status === "called").length;
}

function waitingCount(session) {
  const entries = (session && session.entries) || [];
  return entries.filter((e) => e.status === "waiting").length;
}

// Кончился ли день сессии: после него не вызывают и не возвращают в очередь.
export function isDayOver(session, nowIso) {
  const now = millis(nowIso);
  const dayEnds = millis(session.day_ends_at);
  return now !== null && dayEnds !== null && now >= dayEnds;
}

/**
 * Какие кнопки сессии доступны. Состояние записи (signup) приходит с сервера,
 * и окончательно решает тоже он: кнопка, нажатая по устаревшему экрану,
 * получит код ошибки.
 */
export function sessionActions(session, nowIso) {
  const dayOver = isDayOver(session, nowIso);
  const { signup } = session;
  const now = millis(nowIso);
  const closes = millis(session.signup_closes_at);
  const waiting = waitingCount(session);
  return {
    dayOver,
    canCallNext: !dayOver && waiting > 0,
    canClose: !dayOver && !session.closed_at && signup !== "closed",
    canReopen:
      !dayOver && Boolean(session.closed_at) && now !== null && closes !== null && now < closes,
    canFinish: !dayOver && (waiting > 0 || (!session.closed_at && signup !== "closed")),
  };
}

/**
 * Действия с записью (DefenseQueue.set_outcome и return_to_queue):
 * - вызванному - любой исход или «вернуть в очередь»;
 * - завершённому с исходом - исправить исход; «не явился» можно вернуть;
 * - закрытой в конце дня, но уже вызванной - отметить забытый исход.
 */
export function entryActions(entry, dayOver) {
  if (entry.status === "called") {
    return { outcomes: OUTCOMES, canReturn: !dayOver, fix: false };
  }
  if (OUTCOMES.includes(entry.status)) {
    return {
      outcomes: OUTCOMES.filter((o) => o !== entry.status),
      canReturn: entry.status === "no_show" && !dayOver,
      fix: true,
    };
  }
  if (entry.status === "expired" && entry.calledAt) {
    return { outcomes: OUTCOMES, canReturn: false, fix: true };
  }
  return { outcomes: [], canReturn: false, fix: false };
}

/**
 * Кнопка у завершённой записи: "set" - отметить забытый исход (закрыта в
 * конце дня после вызова), "fix" - исправить исход, null - кнопки нет.
 */
export function outcomeButton(entry, dayOver) {
  const { fix, outcomes } = entryActions(entry, dayOver);
  if (!fix || !outcomes.length) return null;
  return entry.status === "expired" ? "set" : "fix";
}

/**
 * Кнопка у строки истории. День записи мог давно закончиться, поэтому
 * правила как после конца дня: исход можно отметить или исправить, а
 * вернуть в очередь - нет (это делают на вкладке «Сегодня»).
 */
export function historyOutcomeButton(entry) {
  return outcomeButton(entry, true);
}

/**
 * Какую сессию показать. Выбор преподавателя сохраняется, пока сессия
 * есть; иначе - где кто-то ждёт, затем где запись открыта, затем первая.
 */
export function pickSessionId(sessions, currentId) {
  const list = sessions || [];
  if (list.some((s) => s.id === currentId)) return currentId;
  const withPeople = list.find((s) => activeCount(s) > 0);
  const open = list.find((s) => s.signup === "open");
  const chosen = withPeople || open || list[0];
  return chosen ? chosen.id : null;
}


// --- Ручное открытие записи ---

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * Тело POST /sessions или код ошибки формы. Пустое время - до конца дня,
 * пустой список групп при «все группы» - [].
 */
export function manualRequest({ allGroups, selected, closesAt }) {
  const time = (closesAt || "").trim();
  if (time && !TIME_PATTERN.test(time)) return { error: "INVALID_TIME" };
  if (!allGroups && !(selected || []).length) return { error: "INVALID_GROUPS" };
  return { groups: allGroups ? [] : [...selected], closesAt: time || null };
}


// --- История ---

/**
 * Код ошибки периода до запроса: «С» позже «По». Даты из <input type="date">
 * - "YYYY-MM-DD", их порядок совпадает с порядком строк.
 */
export function historyPeriodError(filters) {
  const from = ((filters && filters.from) || "").trim();
  const to = ((filters && filters.to) || "").trim();
  return from && to && from > to ? "INVALID_PERIOD" : null;
}

// Дата истории, которую сервер не разобрал, - тоже ошибка периода, а не
// времени закрытия записи (INVALID_TIME ручного открытия).
export function historyErrorCode(code) {
  return code === "INVALID_TIME" ? "INVALID_PERIOD" : code || "unknown";
}

// Фильтры истории для запроса: без пустых полей и лишних пробелов.
export function historyQuery(filters) {
  const result = {};
  Object.entries(filters || {}).forEach(([key, value]) => {
    const text = typeof value === "string" ? value.trim() : value;
    if (text !== undefined && text !== null && text !== "") result[key] = text;
  });
  return result;
}

// Момент в поясе курса как "YYYY-MM-DD" и "HH:MM": в CSV даты и время
// не зависят от языка интерфейса, их одинаково читают Excel и LibreOffice.
function courseParts(iso, offsetMinutes) {
  const value = millis(iso);
  if (value === null) return { day: "", time: "" };
  const offset =
    typeof offsetMinutes === "number" && Number.isFinite(offsetMinutes)
      ? offsetMinutes
      : -new Date(value).getTimezoneOffset();
  const text = new Date(value + offset * 60000).toISOString();
  return { day: text.slice(0, 10), time: text.slice(11, 16) };
}

/**
 * Ячейка CSV. Значения, с которых табличный редактор начинает формулу,
 * экранируются апострофом: ФИО приходит из таблицы курса.
 */
export function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  if (/[";\r\n]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
  return text;
}

/**
 * День строки истории в поясе курса ("YYYY-MM-DD"): день сессии из ответа,
 * без него - день записи.
 */
export function entryDay(entry, offsetMinutes) {
  return entry.day || courseParts(entry.createdAt, offsetMinutes).day;
}

const DAY_FORMAT = { day: "2-digit", month: "2-digit", year: "numeric" };
const OTHER_DAY_MOMENT = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };

// День строки для таблицы. Он уже в поясе курса: печатается как UTC.
export function historyDayLabel(day, lang) {
  if (typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return "";
  return formatInCourseTz(`${day}T00:00:00Z`, 0, lang, DAY_FORMAT);
}

/**
 * Момент записи, вызова или окончания в строке истории: только время, а
 * если это другой день, чем день строки, - дата и время. Забытый исход
 * отмечают и на следующий день, одно время читалось бы как время того же дня.
 */
export function historyMoment(iso, day, offsetMinutes, lang) {
  const parts = courseParts(iso, offsetMinutes);
  if (!parts.day) return "";
  return formatInCourseTz(iso, offsetMinutes, lang, parts.day === day ? undefined : OTHER_DAY_MOMENT);
}

// То же для CSV: "HH:MM" или "YYYY-MM-DD HH:MM".
function csvMoment(iso, day, offsetMinutes) {
  const parts = courseParts(iso, offsetMinutes);
  if (!parts.day || parts.day === day) return parts.time;
  return `${parts.day} ${parts.time}`;
}

// История в CSV: заголовки и статусы на языке интерфейса, время в поясе курса.
export function historyToCsv(entries, { t, offsetMinutes }) {
  const header = [
    t("adminQueue.columns.day"),
    t("adminQueue.columns.createdAt"),
    t("adminQueue.columns.name"),
    t("adminQueue.columns.group"),
    t("adminQueue.columns.github"),
    t("adminQueue.columns.lab"),
    t("adminQueue.columns.status"),
    t("adminQueue.columns.calledAt"),
    t("adminQueue.columns.finishedAt"),
  ];
  const rows = (entries || []).map(normalizeEntry).map((e) => {
    const day = entryDay(e, offsetMinutes);
    return [
      day,
      csvMoment(e.createdAt, day, offsetMinutes),
      e.name,
      e.group,
      e.github,
      e.lab,
      t(statusKey(e.status)),
      csvMoment(e.calledAt, day, offsetMinutes),
      csvMoment(e.finishedAt, day, offsetMinutes),
    ];
  });
  // Точка с запятой - разделитель, который русский Excel открывает без мастера импорта.
  return [header, ...rows].map((row) => row.map(csvCell).join(";")).join("\r\n");
}

// Строк истории на странице: до 2000 строк MUI сразу перерисовываются
// секундами. CSV выгружает все.
export const HISTORY_PAGE_SIZE = 100;

/**
 * Страница таблицы истории. Номер с нуля; после того как строк стало
 * меньше (исход перечитал таблицу), он не выходит за последнюю страницу.
 */
export function historyPage(rows, page, size = HISTORY_PAGE_SIZE) {
  const list = rows || [];
  const last = Math.max(0, Math.ceil(list.length / size) - 1);
  const current = Math.min(Math.max(0, Number.isInteger(page) ? page : 0), last);
  return { page: current, rows: list.slice(current * size, (current + 1) * size) };
}

// Имя CSV-файла истории: курс и период, без символов, опасных в имени файла.
export function historyFileName(courseId, from, to) {
  const safe = (text) => String(text || "").replace(/[^A-Za-z0-9._-]+/g, "_");
  return `queue-${safe(courseId)}-${safe(from)}-${safe(to)}.csv`;
}


// --- Ошибки и состояние курса ---

/**
 * Коды, у которых для преподавателя свой текст adminQueue.errors.<CODE>:
 * студенческий написан во втором лице («Вы уже стоите в очереди») или
 * про другое. Остальные коды переводятся как у студента.
 */
export const ADMIN_ERROR_CODES = [
  "ALREADY_IN_QUEUE",
  "ENTRY_NOT_CALLED",
  "DAY_OVER",
  "SIGNUP_CLOSED",
  "QUEUE_CHANGED",
  "QUEUE_EMPTY",
  "QUEUE_DISABLED",
  "INVALID_PERIOD",
];

const ADMIN_ERRORS = new Set(ADMIN_ERROR_CODES);

/**
 * Ключи перевода для t(): i18next берёт первый найденный, так что
 * текст для преподавателя главнее студенческого queue.errors.<CODE>.
 */
export function adminErrorKeys(code) {
  const fallback = errorKey(code);
  return ADMIN_ERRORS.has(code) ? [`adminQueue.errors.${code}`, fallback] : [fallback];
}

/**
 * Почему очередь не принимает новых записей: "archived" - курс не active
 * в courses/index.yaml (в конфиге queue.enabled при этом может быть true),
 * "disabled" - очередь выключена в конфигурации курса, null - работает или
 * в секции queue ошибка (её показывает config_error: это не «выключена»).
 */
export function queueNotice(state) {
  if (!state || state.queue_enabled) return null;
  if (state.course_status && state.course_status !== "active") return "archived";
  return state.config_error ? null : "disabled";
}

// Ручное открытие записи - только у работающей очереди с верным конфигом.
export function canOpenManual(state) {
  return Boolean(state && state.queue_enabled && !state.config_error);
}


// --- Действие преподавателя ---

/**
 * Одно действие: запрос, затем экран перечитывается сразу, не дожидаясь
 * таймера опроса. Возвращает ответ ({} для пустого) или null при ошибке.
 *
 * После 401 экран не перечитывается: страница уходит на вход, а опрос
 * получил бы тот же 401 и второй раз ушёл бы на вход уже со страницы входа.
 */
export async function runAdminAction(action, { onSuccess, onError, onUnauthorized, refresh }) {
  let unauthorized = false;
  try {
    const result = await action();
    onSuccess(result);
    return result ?? {};
  } catch (error) {
    if (error && error.status === 401) {
      unauthorized = true;
      onUnauthorized();
    } else {
      onError(error || {});
    }
    return null;
  } finally {
    if (!unauthorized) refresh();
  }
}


// --- Вход преподавателя ---

// Страница очереди курса: сюда возвращаются после входа.
export function adminQueuePath(courseId) {
  return `/admin/courses/${encodeURIComponent(courseId)}/queue`;
}

/**
 * Куда вернуться после входа. Только внутренние страницы админки: иначе
 * ссылка вида /admin?next=https://... уводила бы после входа на чужой сайт.
 */
export function safeAdminNext(next) {
  if (typeof next !== "string") return "/admin/courses";
  if (!next.startsWith("/admin/") || next.includes("\\")) return "/admin/courses";
  return next;
}

// Страница входа, после которой вернёт на next.
export function adminLoginPath(next) {
  return `/admin?next=${encodeURIComponent(next)}`;
}
