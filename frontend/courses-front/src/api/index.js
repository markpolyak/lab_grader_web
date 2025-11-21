const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

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

export const fetchCourses = async () => {
  const response = await fetch(`${API_BASE_URL}/courses`);
  return response.json();
};

export const fetchCourseDetails = async (courseId) => {
  const response = await fetch(`${API_BASE_URL}/courses/${courseId}`);
  return response.json();
};

export const fetchGroups = async (courseId) => {
  const response = await fetch(`${API_BASE_URL}/courses/${courseId}/groups`);
  return response.json();
};

export const fetchLabs = async (courseId, groupId) => {
  const response = await fetch(
    `${API_BASE_URL}/courses/${courseId}/groups/${groupId}/labs`
  );
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

