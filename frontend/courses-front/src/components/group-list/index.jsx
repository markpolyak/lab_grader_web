import { useState, useEffect } from "react";
import { fetchGroups } from "../../api";
import { Spin } from "antd";
import { CardContainer, DescriptionContainer, Number, Title } from "./styled";
import { CardTitle, MainContainer } from "../../../theme";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { Snackbar, Alert } from "@mui/material";

export const GroupList = ({ courseId, onSelectGroup, onBack }) => {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} />
      <CardTitle>Выберите группу</CardTitle>
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
    </MainContainer>
  );
};
