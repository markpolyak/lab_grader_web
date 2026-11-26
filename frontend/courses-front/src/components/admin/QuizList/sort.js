// Sorting of the course's quiz list, remembered in a cookie between visits.

export const SORT_COOKIE = "quizListSort";
const COOKIE_MAX_AGE = 365 * 24 * 60 * 60;

// Column -> value of a quiz summary (GET .../quizzes). A quiz that failed to
// parse has only id and error: it sorts by id, its other values are empty.
export const SORT_COLUMNS = {
  title: (quiz) => quiz.title || quiz.id,
  short_name: (quiz) => quiz.short_name,
  questions: (quiz) => quiz.questions,
  max_points: (quiz) => quiz.max_points,
  seconds_per_question: (quiz) => quiz.seconds_per_question,
};

const DIRECTIONS = ["asc", "desc"];

// "Квиз 2" before "Квиз 10", case-insensitive.
const collator = new Intl.Collator("ru", { numeric: true, sensitivity: "base" });

const isEmpty = (value) => value === undefined || value === null || value === "";

// "10,5" from a Russian-locale course is a number too.
const asNumber = (value) => {
  if (typeof value === "number") return value;
  const n = Number(String(value).replace(",", "."));
  return Number.isFinite(n) ? n : null;
};

function compareValues(a, b) {
  const na = asNumber(a);
  const nb = asNumber(b);
  if (na !== null && nb !== null) return na - nb;
  return collator.compare(String(a), String(b));
}

// A new array; empty values go last in either direction. Without a valid
// sort the server's order is kept.
export function sortQuizzes(quizzes, sort) {
  const value = sort && SORT_COLUMNS[sort.column];
  if (!value) return quizzes;
  const sign = sort.direction === "desc" ? -1 : 1;
  return [...quizzes].sort((qa, qb) => {
    const a = value(qa);
    const b = value(qb);
    if (isEmpty(a) || isEmpty(b)) return isEmpty(a) - isEmpty(b);
    return sign * compareValues(a, b);
  });
}

// A click on a column header: the same column flips the direction, another
// one starts ascending.
export function nextSort(sort, column) {
  if (sort && sort.column === column) {
    return { column, direction: sort.direction === "asc" ? "desc" : "asc" };
  }
  return { column, direction: "asc" };
}

// From document.cookie; anything unknown (an old column, a hand-edited value)
// is ignored rather than trusted.
export function readSortCookie(cookieString) {
  for (const part of (cookieString || "").split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name !== SORT_COOKIE) continue;
    const [column, direction] = decodeURIComponent(rest.join("=")).split(".");
    if (SORT_COLUMNS[column] && DIRECTIONS.includes(direction)) return { column, direction };
    return null;
  }
  return null;
}

export function sortCookie(sort) {
  const value = encodeURIComponent(`${sort.column}.${sort.direction}`);
  return `${SORT_COOKIE}=${value}; Path=/; Max-Age=${COOKIE_MAX_AGE}; SameSite=Lax`;
}
