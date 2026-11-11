import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { fetchGroups } from "../../api";
import { Spin } from "antd";
import { CardContainer, DescriptionContainer, ListContainer, ListHint, ListTitle, Number, Title } from "./styled";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { Snackbar, Alert } from "@mui/material";
import { QueueEntryCard } from "../queue/QueueEntryCard";
import { useCourseQueue } from "../queue/useCourseQueue";

export const GroupList = ({ courseId, onSelectGroup, onBack }) => {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const queueEnabled = useCourseQueue(courseId);
  const { t } = useTranslation();

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchGroups(courseId)
      .then((data) => {
        setGroups(data);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        setError(err.message || "Ошибка при загрузке групп");
      });
  }, [courseId]);

  return (
    <ListContainer $fitPhone={queueEnabled}>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} />
      {queueEnabled && <QueueEntryCard courseId={courseId} />}
      <ListTitle $withHint={queueEnabled}>Выберите группу</ListTitle>
      {queueEnabled && <ListHint>{t("queue.entry.groupsHint")}</ListHint>}
      {loading ? (
        <Spin size="default" />
      ) : (
        <CardContainer>
          {groups.map((group) => (
            <DescriptionContainer key={group}>
              <Title>№ группы</Title>
              <Number onClick={() => onSelectGroup(group)}>{group}</Number>
            </DescriptionContainer>
          ))}
        </CardContainer>
      )}
      <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert onClose={() => setError(null)} severity="error" sx={{ width: "100%" }}>
          {error}
        </Alert>
      </Snackbar>
    </ListContainer>
  );
};
