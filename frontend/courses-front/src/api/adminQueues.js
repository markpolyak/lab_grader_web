export async function adminRequest(path, { method = "GET", body, signal } = {}) {
  const response = await fetch(`/api/v1/admin${path}`, {
    method, signal, credentials: "include",
    ...(body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(typeof data?.detail === "string" ? data.detail : "Request failed");
    error.status = response.status;
    error.code = data?.detail;
    throw error;
  }
  return data;
}

const coursePath = (courseId) => `/courses/${encodeURIComponent(courseId)}/queues`;
export const fetchAdminQueues = (courseId, options) => adminRequest(coursePath(courseId), options);
export const fetchAdminQueue = (courseId, queueId, options) => adminRequest(`${coursePath(courseId)}/${encodeURIComponent(queueId)}`, options);
export const setQueueRegistration = (courseId, queueId, closed) => adminRequest(`${coursePath(courseId)}/${encodeURIComponent(queueId)}/registration`, { method: "PUT", body: { closed } });
export const updateAdminQueue = (courseId, queueId, body) => adminRequest(`${coursePath(courseId)}/${encodeURIComponent(queueId)}`, { method: "PATCH", body });
export const callNextStudent = (courseId, queueId) => adminRequest(`${coursePath(courseId)}/${encodeURIComponent(queueId)}/call-next`, { method: "POST" });
export const finishStudent = (courseId, queueId, entryId) => adminRequest(`${coursePath(courseId)}/${encodeURIComponent(queueId)}/entries/${encodeURIComponent(entryId)}/finish`, { method: "POST", body: {} });
