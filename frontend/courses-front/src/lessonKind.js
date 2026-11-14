// Keep aliases consistent with the queue backend's accepted lesson kinds.
const laboratory = new Set(["лабораторное занятие", "лабораторная работа", "лр", "лб"]);
const practical = new Set(["практическое занятие", "практическая работа", "практика", "пр", "пз"]);

export function translateLessonKind(kind, t) {
  const normalized = (kind ?? "").trim().toLowerCase().replace(/\.+$/, "");
  if (laboratory.has(normalized)) return t("lessonKinds.laboratory");
  if (practical.has(normalized)) return t("lessonKinds.practical");
  return kind ?? "";
}
