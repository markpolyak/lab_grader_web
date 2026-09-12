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


// Ячейка с готовой секретной ссылкой: её показывают целиком, чтобы её можно
// было и скопировать кнопкой, и прочитать глазами при сверке
// (docs/SECRET_JOIN_LINKS_PLAN.md §9.1).
export const JoinLinkCell = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 260px;
`;

export const JoinLinkText = styled.code`
  font-size: ${sizes.fontSizeSmall};
  word-break: break-all;
`;

// Ссылка и кнопка копирования - одной строкой, кнопка сразу за ссылкой.
// Flex, а не обычный инлайн-поток: у инлайна кнопка - неразрывный блок,
// и если текст ссылки занял строку до самого края, кнопка переносится на
// следующую - ровно то, чего здесь быть не должно.
//
// `flex: 0 1 auto` у ссылки означает "своя ширина, но ужимайся при нехватке":
// короткая ссылка не растягивается, и кнопка стоит вплотную к ней, а длинная
// переносится внутри своей колонки, оставляя кнопку на первой строке.
// `min-width: 0` снимает флексбоксовый минимум, иначе перенос по символам
// (word-break: break-all) не сработает.
export const JoinLinkLine = styled.div`
  display: flex;
  align-items: flex-start;
  gap: 2px;

  & > code {
    flex: 0 1 auto;
    min-width: 0;
  }

  & > button {
    flex: 0 0 auto;
  }
`;
