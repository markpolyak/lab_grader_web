import { useTranslation } from "react-i18next";
import { PrimaryButton } from "../queue-detail/styled";
import { ScrollArea, Table, Row, Name, EntryStatus } from "./styled";

export function QueueTable({ entries, ownEntryId, onFinish, pending = false }) {
  const { t } = useTranslation();
  return <ScrollArea tabIndex={0}>
    <Table aria-label={t("queueDetail.participants")}>
      <thead><tr>
        <th scope="col">{t("queueTable.position")}</th>
        <th scope="col">{t("queueDetail.fullName")}</th>
        <th scope="col">GitHub</th>
        <th scope="col">{t("queueTable.lab")}</th>
        {onFinish && <th scope="col">{t("queueTable.actions")}</th>}
      </tr></thead>
      <tbody>
        {entries.length === 0 && <tr><td colSpan={onFinish ? 5 : 4}>{t("queueTable.empty")}</td></tr>}
        {entries.map((entry) => <Row key={entry.id} $status={entry.status} $own={entry.id === ownEntryId}>
          <td>{entry.position}</td>
          <td>
            <Name>{entry.full_name}{entry.id === ownEntryId && ` · ${t("queueDetail.you")}`}</Name>
            <EntryStatus>{t(`queueTable.status.${entry.status}`)}</EntryStatus>
          </td>
          <td>{entry.github}</td>
          <td>{entry.lab_name || entry.lab_id}</td>
          {onFinish && <td>{entry.status === "called" && <PrimaryButton disabled={pending}
            aria-label={`${t("queueTable.finish")} — ${entry.full_name}`}
            onClick={() => onFinish(entry.id)}>{t("queueTable.finish")}</PrimaryButton>}</td>}
        </Row>)}
      </tbody>
    </Table>
  </ScrollArea>;
}
