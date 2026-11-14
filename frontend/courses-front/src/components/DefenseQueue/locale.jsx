import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import { displayDate as formatDate } from "./state";

export const q = (text, values = {}) => i18n.t(text, { ns: "queue", keySeparator: false, nsSeparator: false, ...values });

export function useQueueLanguage() {
  const { i18n: language } = useTranslation("queue");
  return { displayDate: (timestamp, zone) => timestamp ? formatDate(timestamp, zone, language.language) : q("Дата не назначена") };
}

export function QueueLanguage() {
  const { i18n: language } = useTranslation("queue");
  return <label className="queue-language">{q("Язык")}
    <select aria-label={q("Язык")} value={language.language.split("-")[0]} onChange={event => language.changeLanguage(event.target.value)}>
      <option value="ru">Русский</option><option value="en">English</option><option value="zh">中文</option>
    </select>
  </label>;
}

export function queueError(error) {
  const message = typeof error === "string" ? error : error?.message;
  if (message && i18n.exists(message, { ns: "queue", keySeparator: false, nsSeparator: false })) return q(message);
  if (i18n.language.startsWith("ru") && message) return message;
  const messages = {
    401: "Сессия входа истекла. Войдите в панель снова.",
    403: "Недостаточно прав. Проверьте данные входа.",
    404: "Запись не найдена. Обновите список.",
    409: "Состояние очереди изменилось. Обновите список и повторите действие.",
    422: "Проверьте заполненные поля и даты, затем повторите действие.",
    503: "Источник данных недоступен. Сохранённый список доступен; повторите позже.",
  };
  return q(messages[error?.status] || "Не удалось связаться с сервером. Проверьте соединение и повторите действие.");
}
