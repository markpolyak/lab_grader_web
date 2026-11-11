import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { fetchLabs } from "../../api";
import { Spin } from "antd";
import {
  CardContainer,
  DescriptionContainer,
  ListContainer,
  ListHint,
  ListTitle,
  Number,
  Title,
} from "../group-list/styled";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { Snackbar, Alert } from "@mui/material";
import { QueueEntryCard } from "../queue/QueueEntryCard";
import { useCourseQueue } from "../queue/useCourseQueue";

export const LabList = ({ courseId, groupId, onSelectLab, onBack }) => {
  const [labs, setLabs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Очередь на защиту - там же, где студент сдаёт свои работы.
  const queueEnabled = useCourseQueue(courseId);
  const { t } = useTranslation();

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchLabs(courseId, groupId)
      .then((data) => {
        setLabs(data);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        setError(err.message || "Ошибка при загрузке лабораторных работ");
      });
  }, [courseId, groupId]);

  return (
    <ListContainer $fitPhone={queueEnabled}>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} groupId={groupId} />
      {queueEnabled && <QueueEntryCard courseId={courseId} />}
      <ListTitle $withHint={queueEnabled}>Лабораторные работы</ListTitle>
      {queueEnabled && <ListHint>{t("queue.entry.labsHint")}</ListHint>}
      {loading ? (
        <Spin size="default" />
      ) : (
        <CardContainer>
          {labs.map((lab, index) => (
            <DescriptionContainer key={index}>
              <Title>№ лабораторной работы</Title>
              <Number onClick={() => onSelectLab(lab)}>{lab}</Number>
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
