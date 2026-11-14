export const identityKey = (course, group) => `queue.identity.${JSON.stringify([course, group])}`;
export function cleanIdentity(value = {}) {
  return Object.fromEntries(["surname", "name", "patronymic", "github"].map(key => [key, typeof value?.[key] === "string" ? value[key] : ""]));
}
export const canIdentify = identity => Boolean(identity?.surname?.trim() && identity?.name?.trim() && identity?.github?.trim());
export const ownEntry = (entries, github) => github?.trim() ? entries?.find(row => row.github?.toLowerCase() === github?.trim().toLowerCase()) : undefined;
export function entryLabel(row, { current, next } = {}) {
  if (current != null && (Array.isArray(current) ? current.includes(row.attempt?.entry_id) : row.attempt?.entry_id === current)) return "Сдаёт сейчас";
  if (next != null && row.attempt?.entry_id === next) return row.attempt.is_repeat ? "Следующий · повторно" : "Следующий";
  const states = { waiting: "Ожидает", called: "Вызван", defended: "Защитил", failed: "Не защитил", no_show: "Не явился", withdrawn: "Снят с очереди", expired: "Занятие завершено" };
  if (row.attempt) return row.attempt.status === "waiting" && row.attempt.is_repeat ? "Ожидает повторного вызова" : states[row.attempt.status] || row.attempt.status;
  return ({ attending: "Придёт", declined: "Не придёт", unconfirmed: "Не придёт" })[row.state] || "Не придёт";
}
export function displayDate(timestamp, zone = "Europe/Moscow", locale = "ru-RU") {
  if (!timestamp) return "Дата не назначена";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: zone }).format(new Date(timestamp * 1000));
}

export const ALL_GROUPS = "__all_groups__";
export function filterSessions(sessions, course, group) {
  return (sessions || []).filter(session => session.course_id === course && (group === ALL_GROUPS || session.group_id === group))
    .sort((a, b) => a.starts_at - b.starts_at || a.session_id.localeCompare(b.session_id));
}
export function localDateTime(timestamp) {
  const date = new Date(timestamp * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
