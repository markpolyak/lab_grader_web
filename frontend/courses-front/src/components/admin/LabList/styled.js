import styled from "styled-components";
import { colors, sizes, textStyles, breakpoints } from "../../../../theme";

export const Container = styled.div`
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 15px;
  padding: 16px;
`;

export const Panel = styled.div`
  display: flex;
  width: 100%;
  max-width: 960px;
  flex-direction: column;
  border-radius: 12px;
  background: #fff;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);
  padding: 24px;
  gap: 16px;

  @media (max-width: ${breakpoints.tablet}) {
    padding: 16px;
  }
`;

export const PageTitle = styled.h1`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  margin: 0;
`;

export const BackButton = styled.button`
  align-self: flex-start;
  ${textStyles}
  color: ${colors.buttonBorder};
  border: none;
  background: none;
  font-size: ${sizes.fontSizeMedium};
  padding: 4px 0;

  &:hover {
    color: ${colors.cancel};
    cursor: pointer;
  }
`;

export const TableWrapper = styled.div`
  width: 100%;
  overflow-x: auto;
`;

// Таблица предпросмотра прокручивается сама, а не вместе с содержимым диалога:
// sticky-шапка держится за ближайшего прокручиваемого предка, и без этого
// строка "Выбрать все" уезжает вверх на курсе в две сотни студентов.
export const SelectableTableWrapper = styled(TableWrapper)`
  max-height: 50vh;
  overflow-y: auto;
`;

export const StatusChipRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
`;

export const HintText = styled.p`
  ${textStyles}
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
  margin: 0;
`;
