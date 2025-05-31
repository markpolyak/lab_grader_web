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

export const LabList = ({ courseId, groupId, onSelectLab, onBack }) => {
  const [labs, setLabs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchLabs(courseId, groupId)
      .then((data) => {
        setLabs(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [courseId, groupId]);

  return (
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
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
    </MainContainer>
  );
};
