const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

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
    throw new Error(data.detail || "Ошибка при регистрации");
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
    throw new Error(data.detail || "Ошибка при проверке");
  }

  return data;
}

