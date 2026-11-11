import styled from "styled-components";
import { CardTitle, MainContainer, colors, sizes, textStyles, breakpoints } from "../../../theme";

// На телефоне карточка в 430px шире экрана: body центрирует её, и левый край
// уходит туда, куда прокрутка не достаёт. Ширина считается от окна, а не в
// процентах: #root - flex-элемент body и сам растягивается по карточке.
// 48px - поля body (8px + 1em) с двух сторон. Включается ($fitPhone) только
// там, где на странице есть блок очереди: страницы курсов без очереди как были.
export const ListContainer = styled(MainContainer)`
  ${({ $fitPhone }) =>
    $fitPhone
      ? `@media (max-width: ${breakpoints.tablet}) {
          box-sizing: border-box;
          max-width: calc(100vw - 48px);
        }`
      : ""}
`;

// Заголовок списка. Если у курса есть очередь, под ним пояснение, что список -
// сдача работы на проверку, и отступ до списка переходит под пояснение.
export const ListTitle = styled(CardTitle)`
  ${({ $withHint }) => ($withHint ? "margin-bottom: 6px;" : "")}
`;

export const ListHint = styled.p`
  ${textStyles}
  margin: 0 0 18px;
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
  line-height: 1.5;
  text-align: center;
`;

export const CardContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-self: stretch;
  padding: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    padding: 8px;
    gap: 8px;
  }
`;

export const DescriptionContainer = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 0.5px solid ${colors.buttonBorder};

  @media (max-width: ${breakpoints.tablet}) {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
`;

export const Title = styled.h3`
  ${textStyles}
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeMedium};
  font-weight: 400;
  margin: 0;
  margin-bottom: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    font-size: ${sizes.fontSizeSmall};
    margin-bottom: 8px;
  }
`;

export const Number = styled.p`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  margin: 0;
  cursor: pointer;
  margin-bottom: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    font-size: ${sizes.fontSizeMedium};
    margin-bottom: 8px;
  }
`;
