// API очереди на защиту: эндпоинты /courses/{id}/queue и /admin/courses/{id}/queue в main.py.
//
// Модуль не читает import.meta.env и не импортирует ./index.js, чтобы его
// можно было загрузить в node --test. Базовый адрес передаёт компонент:
// студенческая страница - API_BASE_URL, админка - "/api/v1" (как LabList).
//
// Backend отдаёт в `detail` стабильные коды (SIGNUP_CLOSED, QUEUE_CHANGED, ...),
// их переводит компонент. Без кода (сеть, таймаут, ответ прокси, лимит
// запросов) код выводится здесь из статуса.

const QUEUE_REQUEST_TIMEOUT_MS = 10000;

const CODE_PATTERN = /^[A-Z][A-Z_]*$/;

// Код ошибки ответа: из detail, а без него - по HTTP-статусу.
export function queueErrorCode(status, detail) {
  if (typeof detail === "string" && CODE_PATTERN.test(detail)) return detail;
  if (status === 401) return "SESSION_REQUIRED";
  if (status === 404) return "not_found";
  if (status === 429) return "rate_limit";
  return "unknown";
}

function buildQuery(params) {
  const query = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

const course = (courseId) => encodeURIComponent(courseId);

// Клиент API очереди: по методу на эндпоинт. fetchImpl и timeoutMs подменяют тесты.
export function createQueueApi({ baseUrl, fetchImpl, timeoutMs = QUEUE_REQUEST_TIMEOUT_MS }) {
  // window.fetch нельзя вызывать как метод чужого объекта (Illegal invocation),
  // поэтому по умолчанию он вызывается через обёртку.
  const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));
  const root = String(baseUrl || "").replace(/\/+$/, "");

  const request = async (path, { method = "GET", body, query } = {}) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const options = { method, credentials: "include", signal: controller.signal };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }

    // fetch() завершается уже по заголовкам, поэтому таймер снимается только
    // после чтения тела: застрявший JSON иначе держал бы опрос и кнопки преподавателя.
    let response;
    let data = null;
    try {
      response = await doFetch(`${root}${path}${buildQuery(query)}`, options);
      try {
        data = await response.json();
      } catch (cause) {
        if (controller.signal.aborted) throw cause;
        // тело может быть пустым - код ошибки тогда выводится из статуса
      }
    } catch (cause) {
      const error = new Error("Queue request failed", { cause });
      error.code =
        cause?.name === "AbortError" || controller.signal.aborted ? "request_timeout" : "network_error";
      error.status = 0;
      error.payload = {};
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }

    if (!response.ok) {
      const error = new Error("Queue request failed");
      error.code = queueErrorCode(response.status, data && data.detail);
      error.status = response.status;
      error.payload = data || {};
      throw error;
    }
    return data;
  };

  const studentPath = (courseId, tail = "") => `/courses/${course(courseId)}/queue${tail}`;
  const adminPath = (courseId, tail = "") => `/admin/courses/${course(courseId)}/queue${tail}`;

  return {
    // --- студент (логин backend берёт только из подписанной cookie) ---
    getState: (courseId) => request(studentPath(courseId)),
    getLabs: (courseId) => request(studentPath(courseId, "/labs")),
    signUp: (courseId, { sessionId, labId }) =>
      request(studentPath(courseId, "/entries"), {
        method: "POST",
        body: { session_id: sessionId, lab_id: labId },
      }),
    leave: (courseId) => request(studentPath(courseId, "/leave"), { method: "POST" }),
    skip: (courseId) => request(studentPath(courseId, "/skip"), { method: "POST" }),
    logout: (courseId) => request(studentPath(courseId, "/logout"), { method: "POST" }),
    // Вход через GitHub - полный переход браузера, а не fetch.
    loginUrl: (courseId, lab) => `${root}${studentPath(courseId, "/login")}${buildQuery({ lab })}`,

    // --- преподаватель (Depends(require_admin), cookie admin_session) ---
    getDay: (courseId) => request(adminPath(courseId)),
    openManual: (courseId, { groups, closesAt }) =>
      request(adminPath(courseId, "/sessions"), {
        method: "POST",
        body: { groups, closes_at: closesAt || null },
      }),
    closeSignup: (courseId, sessionId) =>
      request(adminPath(courseId, `/sessions/${encodeURIComponent(sessionId)}/close`), { method: "POST" }),
    reopenSignup: (courseId, sessionId) =>
      request(adminPath(courseId, `/sessions/${encodeURIComponent(sessionId)}/reopen`), { method: "POST" }),
    finishSession: (courseId, sessionId) =>
      request(adminPath(courseId, `/sessions/${encodeURIComponent(sessionId)}/finish`), { method: "POST" }),
    callNext: (courseId, sessionId, expectedEntryId) =>
      request(adminPath(courseId, `/sessions/${encodeURIComponent(sessionId)}/call-next`), {
        method: "POST",
        body: { expected_entry_id: expectedEntryId ?? null },
      }),
    setOutcome: (courseId, entryId, outcome) =>
      request(adminPath(courseId, `/entries/${encodeURIComponent(entryId)}/outcome`), {
        method: "POST",
        body: { outcome },
      }),
    returnToQueue: (courseId, entryId) =>
      request(adminPath(courseId, `/entries/${encodeURIComponent(entryId)}/return`), { method: "POST" }),
    getHistory: (courseId, { from, to, group, q, lab } = {}) =>
      request(adminPath(courseId, "/history"), { query: { from, to, group, q, lab } }),
    // Существующие маршруты: список групп (листы таблицы) и лаб курса.
    getGroups: (courseId) => request(`/courses/${course(courseId)}/groups`),
    getAdminLabs: (courseId) => request(`/admin/courses/${course(courseId)}/labs`),
  };
}
