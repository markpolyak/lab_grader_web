import styled from "styled-components";
import { breakpoints, colors, sizes, textStyles } from "../../../theme";
import { Button, SelectButton } from "../course-list/styled";

export const Content = styled.section`
  ${textStyles}
  padding: 0 16px 24px;
`;

export const Description = styled.p`
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeMedium};
  line-height: 1.6;
  text-align: center;
  margin: 0 0 12px;
`;

export const LessonList = styled.ul`
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0;
  margin: 24px 0 0;
  list-style: none;
`;

export const LessonCard = styled.li`
  display: grid;
  grid-template-columns: minmax(150px, 1fr) minmax(0, 2fr) auto;
  align-items: center;
  gap: 24px;
  padding: ${sizes.padding};
  border-radius: 12px;
  background: #fff;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);

  @media (max-width: ${breakpoints.tablet}) {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 20px;
  }
`;

export const LessonDate = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeMedium};
  line-height: 1.5;
`;

export const LessonTime = styled.span`
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
`;

export const LessonDetails = styled.div`
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-wrap: anywhere;
`;

export const Subject = styled.h2`
  margin: 0 0 4px;
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  line-height: 1.5;
`;

export const Detail = styled.p`
  margin: 0;
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeMedium};
  line-height: 1.5;
`;

export const SelectLessonButton = styled(SelectButton)`
  &:disabled {
    background: ${colors.buttonHover};
    border-color: transparent;
    color: ${colors.textSecondary};
    cursor: default;
  }
`;

export const Status = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 24px 0;
  text-align: center;
  color: ${colors.textSecondary};
`;

export const RetryButton = styled(Button)`
  width: auto;
  background: #fff;
`;
