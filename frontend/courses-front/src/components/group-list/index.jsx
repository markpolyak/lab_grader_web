import { useState, useEffect } from "react";
import { fetchGroups } from "../../api";
import { Spin } from "antd";
import { CardContainer, DescriptionContainer, Number, Title } from "./styled";
import { CardTitle, MainContainer } from "../../../theme";
import { ButtonBack } from "../course-list/styled";

export const GroupList = ({ courseId, onSelectGroup, onBack }) => {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchGroups(courseId)
      .then((data) => {
        setGroups(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [courseId]);

  return (
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
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
    </MainContainer>
  );
};
