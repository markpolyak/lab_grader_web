import { useId } from "react";
import { useTranslation } from "react-i18next";

import { EntryCard, EntryCardButton, EntryCardText, EntryCardTitle } from "./styled";
import { queuePagePath } from "./state";


// Вход в очередь на страницах курса и работ группы. Отдельный блок, а не
// кнопка среди прочих: очередь - другая функция, не сдача работы на
// проверку, и студент должен заметить её сразу.
export function QueueEntryCard({ courseId }) {
  const { t } = useTranslation();
  const titleId = useId();

  return (
    <EntryCard aria-labelledby={titleId}>
      <EntryCardTitle id={titleId}>{t("queue.entry.title")}</EntryCardTitle>
      <EntryCardText>{t("queue.entry.text")}</EntryCardText>
      <EntryCardButton to={queuePagePath(courseId)}>{t("queue.entry.button")}</EntryCardButton>
    </EntryCard>
  );
}
