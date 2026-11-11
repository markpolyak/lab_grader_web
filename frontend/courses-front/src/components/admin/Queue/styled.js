import styled from "styled-components";

import { breakpoints, colors, sizes, textStyles } from "../../../../theme";

import {
  BackButton as LabListBackButton,
  Container as LabListContainer,
  PageTitle as LabListPageTitle,
  Panel as LabListPanel,
} from "../LabList/styled";

// Каркас страницы - как у списка лабораторных в админке.
export { HintText, TableWrapper } from "../LabList/styled";

// Преподаватель ведёт очередь и с телефона: на узком или сенсорном экране
// у каждой кнопки высота не меньше 40px, иначе по ней промахиваются пальцем.
const TOUCH_SCREEN = `(max-width: ${breakpoints.tablet}), (pointer: coarse)`;

export const BackButton = styled(LabListBackButton)`
  @media ${TOUCH_SCREEN} {
    min-height: 40px;
  }
`;

// sx для столбца с кнопкой исхода (история и завершённые записи занятия):
// он прижат к правому краю, иначе в широкой таблице уходит за край прокрутки.
export const ACTION_CELL = {
  position: "sticky",
  right: 0,
  zIndex: 1,
  backgroundColor: "#fff",
  boxShadow: "inset 1px 0 0 rgba(224, 224, 224, 1)",
};

// То же для диалогов: MUI рисует их в портале, вне Container.
export const touchDialogSx = {
  [`@media ${TOUCH_SCREEN}`]: { "& .MuiButton-root": { minHeight: 40 } },
};

// Ширина страницы задана от окна, а не от содержимого. body - flex с
// align-items: center, и на телефоне широкая таблица растянула бы страницу
// и отодвинула её левый край за экран, куда прокрутка не достаёт. Таблицы
// прокручиваются внутри TableWrapper. 48px - поля body (8px + 1em) с двух сторон.
export const Container = styled(LabListContainer)`
  box-sizing: border-box;
  width: min(992px, calc(100vw - 48px));
  align-items: stretch;

  @media (max-width: ${breakpoints.tablet}) {
    padding: 0;
  }

  @media ${TOUCH_SCREEN} {
    .MuiButton-root {
      min-height: 40px;
    }
  }
`;

// На телефоне промежутки меньше: «Вызвать следующего» и исходы ближе к
// верху экрана.
export const Panel = styled(LabListPanel)`
  box-sizing: border-box;
  align-self: center;
  min-width: 0;

  @media (max-width: ${breakpoints.tablet}) {
    gap: 12px;
  }
`;

// Название курса бывает длиннее строки телефона.
export const PageTitle = styled(LabListPageTitle)`
  min-width: 0;
  overflow-wrap: anywhere;
`;


export const HeaderRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
`;

export const SessionTiles = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
`;

export const SessionTile = styled.button`
  ${textStyles}
  display: flex;
  flex: 1 1 220px;
  max-width: 320px;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 12px 14px;
  border: 2px solid ${({ $selected }) => ($selected ? colors.buttonBorder : colors.buttonHover)};
  border-radius: 12px;
  background: ${({ $selected }) => ($selected ? "rgba(60, 60, 67, 0.05)" : "#fff")};
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeMedium};
  text-align: left;
  cursor: pointer;

  &:hover {
    border-color: ${colors.buttonBorder};
  }

  @media (max-width: ${breakpoints.tablet}) {
    max-width: none;
  }
`;

export const TileTitle = styled.span`
  font-weight: 600;
`;

export const TileMeta = styled.span`
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
`;

// На телефоне рамка сессии внутри панели отнимала бы ещё 32px ширины.
export const SessionBox = styled.section`
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  border: 1px solid ${colors.buttonHover};
  border-radius: 12px;

  @media (max-width: ${breakpoints.tablet}) {
    gap: 12px;
    padding: 0;
    border: none;
  }
`;

export const SectionHeading = styled.h2`
  ${textStyles}
  margin: 0;
  color: ${colors.textPrimary};
  font-size: 16px;
  font-weight: 600;
`;

export const ActionsRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
`;

// «Вызвать следующего» - главное действие пары, его видно издалека.
export const CallNextRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border-radius: 12px;
  background: rgba(60, 60, 67, 0.04);

  @media (max-width: ${breakpoints.tablet}) {
    gap: 12px;
    padding: 12px;
  }
`;

export const NextUp = styled.div`
  ${textStyles}
  min-width: 0;
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeMedium};
  line-height: 1.5;
  overflow-wrap: anywhere;
`;

export const CalledGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));
  gap: 12px;
`;

export const CalledCard = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 2px solid ${colors.save};
  border-radius: 12px;
  background: rgba(34, 195, 142, 0.06);
`;

// Длинные ФИО и логины переносятся внутри карточки, а не растягивают её.
export const CalledName = styled.div`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: 16px;
  font-weight: 600;
  overflow-wrap: anywhere;
`;

export const CalledMeta = styled.p`
  ${textStyles}
  margin: 0;
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
  overflow-wrap: anywhere;
`;

// Исходы - равными столбцами: на телефоне по кнопке на полстроки и больше.
export const OutcomeButtons = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(120px, 100%), 1fr));
  gap: 8px;
`;

// Итоги занятия: число крупно, подпись под ним; цвет полосы - как у статуса.
const SUMMARY_TONE = {
  defended: colors.save,
  not_defended: colors.error,
  no_show: colors.edit,
  left: colors.cancel,
  waiting: colors.buttonBorder,
  called: colors.save,
  expired: colors.cancel,
};

export const SummaryList = styled.dl`
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
  gap: 8px;
  margin: 0;
`;

export const SummaryItem = styled.div`
  display: flex;
  flex-direction: column-reverse;
  justify-content: flex-end;
  gap: 2px;
  padding: 8px 10px;
  border-left: 3px solid ${({ $status }) => SUMMARY_TONE[$status] || colors.cancel};
  border-radius: 6px;
  background: rgba(60, 60, 67, 0.04);
`;

export const SummaryLabel = styled.dt`
  ${textStyles}
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
`;

export const SummaryCount = styled.dd`
  ${textStyles}
  margin: 0;
  color: ${colors.textPrimary};
  font-size: 20px;
  font-weight: 600;
`;

export const FilterRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
`;
