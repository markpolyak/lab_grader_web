const errorKeys = {
  QUEUE_CLOSED: "closed",
  STUDENT_NOT_FOUND: "studentNotFound",
  STUDENT_NAME_MISMATCH: "nameMismatch",
  LAB_NOT_PASSED: "labNotPassed",
  LAB_NOT_FOUND: "labNotFound",
  GROUP_NOT_ALLOWED: "groupNotAllowed",
  ALREADY_QUEUED: "alreadyQueued",
  SHEETS_UNAVAILABLE: "sheetsUnavailable",
  LESSON_NOT_FOUND: "notFound",
};

export function normalizeStudentName(value) {
  return value.trim().replace(/\s+/g, " ").toLocaleLowerCase("ru").replaceAll("ё", "е");
}

export function queueErrorKey(error) {
  if (errorKeys[error?.code]) return errorKeys[error.code];
  if (error?.status === 429) return "rateLimit";
  if (error?.status === 404) return "notFound";
  return "requestFailed";
}
