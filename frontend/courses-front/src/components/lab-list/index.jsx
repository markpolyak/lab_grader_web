import { useState, useEffect } from "react";
import { fetchLabs } from "../../api";
import { Spin } from "antd";
import {
  CardContainer,
  DescriptionContainer,
  Number,
  Title,
} from "../group-list/styled";
import { CardTitle, MainContainer } from "../../../theme";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { Snackbar, Alert } from "@mui/material";

export const LabList = ({ courseId, groupId, onSelectLab, onBack }) => {
  const [labs, setLabs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} groupId={groupId} />
      <CardTitle>Лабораторные работы</CardTitle>
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
    </MainContainer>
  );
};
