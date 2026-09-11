const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const JOIN_REQUEST_TIMEOUT_MS = 10000;

// Публичные данные для страницы создания репозитория. Сам OAuth намеренно не
// выполняется через fetch: браузер должен перейти на github.com и вернуться
// через callback backend.
export const fetchJoinLab = async (courseId, labId) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), JOIN_REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(
      `${API_BASE_URL}/join/${encodeURIComponent(courseId)}/${encodeURIComponent(labId)}`,
      { signal: controller.signal }
    );
  } catch (cause) {
    const error = new Error("Unable to load repository-generation settings", {
      cause,
    });
    error.code = cause?.name === "AbortError" ? "request_timeout" : "unknown";
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const error = new Error("Unable to load repository-generation settings");
    // Стабильные коды позволяют компоненту переводить ожидаемые ошибки, не
    // показывая русскоязычный detail backend во всех поддерживаемых языках UI.
    if (response.status === 404) error.code = "join_not_found";
    else if (response.status === 400) error.code = "join_not_configured";
    else if (response.status === 429) error.code = "rate_limit";
    else error.code = "unknown";
    throw error;
  }

  return response.json();
};

// GitHub OAuth flow is a full-page redirect, not a fetch - the caller navigates
// the browser to this URL (window.location.href = getJoinStartUrl(...)).
export const getJoinStartUrl = (courseId, labId) =>
  `${API_BASE_URL}/join/${encodeURIComponent(courseId)}/${encodeURIComponent(labId)}/start`;


// --- Командные лабораторные работы (docs/TEAM_ASSIGNMENTS_PLAN.md §8.2) ---
//
// Все три запроса идут с credentials: "include" - личность студента backend
// берёт из подписанной cookie join_session, и только из неё. В dev-режиме
// фронтенд на :8080 и backend на :8000 - это разные источники, поэтому без
// этой опции cookie не уедет (так же сделано в админке, LabList/index.jsx).

const joinTeamsUrl = (courseId, labId) =>
  `${API_BASE_URL}/join/${encodeURIComponent(courseId)}/${encodeURIComponent(labId)}/teams`;

// Backend отдаёт в `detail` стабильные коды (SESSION_REQUIRED, TEAM_FULL, ...),
// которые компонент переводит сам. Сюда попадают только те случаи, когда кода
// нет: сеть, таймаут, ответ прокси.
const teamErrorCode = (status, detail) => {
  if (typeof detail === "string" && /^[A-Z][A-Z_]*$/.test(detail)) return detail;
  if (status === 401) return "SESSION_REQUIRED";
  if (status === 404) return "join_not_found";
  if (status === 429) return "rate_limit";
  return "unknown";
};

const requestJoinTeams = async (url, options = {}) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), JOIN_REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(url, {
      credentials: "include",
      signal: controller.signal,
      ...options,
    });
  } catch (cause) {
    const error = new Error("Team request failed", { cause });
    error.code = cause?.name === "AbortError" ? "request_timeout" : "unknown";
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    // тело может быть пустым - код ошибки тогда выводится из статуса
  }

  if (!response.ok) {
    const error = new Error("Team request failed");
    error.code = teamErrorCode(response.status, data && data.detail);
    error.status = response.status;
    // ALREADY_IN_TEAM несёт с собой slug и ссылку на команду студента
    error.payload = data || {};
    throw error;
  }

  return data;
};

export const fetchJoinTeams = (courseId, labId) =>
  requestJoinTeams(joinTeamsUrl(courseId, labId));

export const createJoinTeam = (courseId, labId, { title, description }) =>
  requestJoinTeams(joinTeamsUrl(courseId, labId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, description }),
  });

// Имя репозитория собирает backend из префикса лабы и slug'а - отсюда
// уезжает только slug.
export const joinJoinTeam = (courseId, labId, slug) =>
  requestJoinTeams(
    `${joinTeamsUrl(courseId, labId)}/${encodeURIComponent(slug)}/join`,
    { method: "POST" }
  );

// Маппинг полей на русские названия для сообщений об ошибках
const fieldLabels = {
  name: "Имя",
  surname: "Фамилия",
  patronymic: "Отчество",
  github: "GitHub аккаунт",
};

// Функция для форматирования ошибок валидации
function formatValidationError(err) {
  // Получаем имя поля из loc (обычно последний элемент)
  const fieldName = err.loc && err.loc.length > 0 ? err.loc[err.loc.length - 1] : null;
  const fieldLabel = fieldLabels[fieldName] || fieldName;

  // Переводим типичные сообщения Pydantic
  if (err.type === "string_too_short" || err.msg?.includes("at least 1 character")) {
    return `${fieldLabel}: поле обязательно для заполнения`;
  }
  if (err.type === "missing") {
    return `${fieldLabel}: поле обязательно`;
  }

  return fieldLabel ? `${fieldLabel}: ${err.msg}` : err.msg;
}

export const fetchCourses = async (status = "active") => {
  const response = await fetch(`${API_BASE_URL}/courses?status=${status}`);
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }
  return response.json();
};

export const fetchCourseDetails = async (courseId) => {
  const response = await fetch(`${API_BASE_URL}/courses/${courseId}`);
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }
  return response.json();
};

export const fetchGroups = async (courseId) => {
  const response = await fetch(`${API_BASE_URL}/courses/${courseId}/groups`);
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }
  return response.json();
};

export const fetchLabs = async (courseId, groupId) => {
  const response = await fetch(
    `${API_BASE_URL}/courses/${courseId}/groups/${groupId}/labs`
  );
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }
  return response.json();
};

export const registerAndCheck = async (courseId, groupId, formData) => {
  const response = await fetch(
    `${API_BASE_URL}/courses/${courseId}/groups/${groupId}/register`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(formData),
    }
  );

  // Обрабатываем 429 Rate Limit как специальный случай (до парсинга JSON)
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }

  const data = await response.json();

  // Обрабатываем 409 Conflict как специальный случай
  if (response.status === 409) {
    return { status: "conflict", message: data.detail };
  }

  // Если ответ не успешный, выбрасываем ошибку с сообщением от сервера
  if (!response.ok) {
    // Обработка ошибок валидации FastAPI (422) - detail может быть массивом объектов
    let errorMessage = "Ошибка при регистрации";
    if (data.detail) {
      if (typeof data.detail === "string") {
        errorMessage = data.detail;
      } else if (Array.isArray(data.detail)) {
        // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
        errorMessage = data.detail.map(formatValidationError).join("\n");
      }
    }
    throw new Error(errorMessage);
  }

  return data;
};


export async function gradeLab(courseId, groupId, labId, github) {
  const encodedLabId = encodeURIComponent(labId);

  const response = await fetch(
    `${API_BASE_URL}/courses/${courseId}/groups/${groupId}/labs/${encodedLabId}/grade`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ github }),
    }
  );

  // Обрабатываем 429 Rate Limit как специальный случай (до парсинга JSON)
  if (response.status === 429) {
    let errorMessage = "Превышен лимит запросов. Пожалуйста, подождите немного и попробуйте снова.";
    try {
      const data = await response.json();
      errorMessage = data.detail || data.message || errorMessage;
    } catch (e) {
      // Если не удалось распарсить JSON, используем сообщение по умолчанию
    }
    throw new Error(errorMessage);
  }

  const data = await response.json();

  // Если ответ не успешный, выбрасываем ошибку с сообщением от сервера
  if (!response.ok) {
    // Обработка ошибок валидации FastAPI (422) - detail может быть массивом объектов
    let errorMessage = "Ошибка при проверке";
    if (data.detail) {
      if (typeof data.detail === "string") {
        errorMessage = data.detail;
      } else if (Array.isArray(data.detail)) {
        // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
        errorMessage = data.detail.map(formatValidationError).join("\n");
      }
    }
    throw new Error(errorMessage);
  }

  return data;
}


