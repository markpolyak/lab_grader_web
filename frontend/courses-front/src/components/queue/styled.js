import styled from "styled-components";
import { Link } from "react-router-dom";

import { breakpoints, buttonStyles, colors, textStyles } from "../../../theme";
import { ButtonBack } from "../course-list/styled";
import {
  JoinCard,
  JoinPage,
  LanguageSelect as JoinLanguageSelect,
  SecondaryButton as JoinSecondaryButton,
} from "../JoinLab/styled";

// Страница очереди собрана из тех же панелей, что и /join: студент видит
// одинаковые кнопки, ошибки и переключатель языка.
export {
  ActionButton,
  Description,
  Details,
  ErrorPanel,
  Label,
  LanguageControl,
  SectionTitle,
  Spinner,
  Title,
  Value,
} from "../JoinLab/styled";

// Очередью пользуются с телефона. Палец промахивается мимо кнопок ниже
// 40px, поэтому на узком или сенсорном экране все кнопки не ниже этого.
const TOUCH_SCREEN = `(max-width: ${breakpoints.tablet}), (pointer: coarse)`;
const PHONE = `(max-width: ${breakpoints.tablet})`;

// Столбец сетки не шире страницы (minmax(0, 1fr)): иначе карточка с длинным
// ФИО растягивает его, и страница прокручивается вбок. Поля на телефоне
// уменьшены ради ширины карточки.
export const QueuePage = styled(JoinPage)`
  grid-template-columns: minmax(0, 1fr);

  @media ${PHONE} {
    padding: 12px 0;
  }
`;

// У общей кнопки «Назад» ширина 89px: «← К курсу» на ней переносится.
// На телефоне кнопка не ниже 40px: по меньшей промахиваются пальцем.
export const BackButton = styled(ButtonBack)`
  width: auto;
  white-space: nowrap;

  @media ${TOUCH_SCREEN} {
    min-height: 40px;
    align-items: center;
  }
`;

export const QueueCard = styled(JoinCard)`
  width: min(680px, 100%);
  text-align: left;
  overflow-wrap: break-word;

  @media ${PHONE} {
    gap: 16px;
    padding: 20px 16px;
  }
`;

export const LanguageSelect = styled(JoinLanguageSelect)`
  @media ${TOUCH_SCREEN} {
    min-height: 40px;
  }
`;

export const SecondaryButton = styled(JoinSecondaryButton)`
  min-height: 40px;
`;

export const Subtitle = styled.div`
  margin-top: 6px;
  color: ${colors.textSecondary};
  font-size: 14px;
`;

export const Hint = styled.p`
  margin: 0;
  color: ${colors.textSecondary};
  font-size: 12px;
  line-height: 1.5;
`;

export const NoticePanel = styled.div`
  padding: 12px 16px;
  border: 1px solid ${colors.edit};
  border-radius: 12px;
  background: rgba(246, 201, 79, 0.1);
  color: ${colors.textPrimary};
  font-size: 13px;
  line-height: 1.5;
`;

// Своя запись: место в очереди должно читаться с расстояния вытянутой руки.
export const MyEntryPanel = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border: 2px solid ${({ $called }) => ($called ? colors.save : colors.buttonBorder)};
  border-radius: 14px;
  background: ${({ $called }) => ($called ? "rgba(34, 195, 142, 0.12)" : "rgba(60, 60, 67, 0.04)")};
`;

export const PlaceRow = styled.div`
  display: flex;
  align-items: baseline;
  gap: 12px;
`;

export const PlaceNumber = styled.span`
  color: ${colors.textPrimary};
  font-size: 44px;
  font-weight: 700;
  line-height: 1;
`;

export const CalledTitle = styled.div`
  color: ${colors.textPrimary};
  font-size: 22px;
  font-weight: 700;
`;

export const ButtonRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
`;

export const DangerButton = styled.button`
  ${buttonStyles}
  width: auto;
  min-height: 40px;
  align-items: center;
  padding: 10px 16px;
  background: transparent;
  color: ${colors.error};
  border-color: ${colors.error};
  font-size: 13px;
  cursor: pointer;

  &:disabled {
    cursor: wait;
    opacity: 0.55;
  }
`;

export const LinkButton = styled.button`
  ${textStyles}
  align-self: flex-start;
  justify-self: start;
  padding: 0;
  border: none;
  background: none;
  color: ${colors.textSecondary};
  font-size: 12px;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;

  &:disabled {
    cursor: wait;
    opacity: 0.55;
  }

  @media ${TOUCH_SCREEN} {
    display: inline-flex;
    min-height: 40px;
    align-items: center;
  }
`;

export const Form = styled.form`
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border: 1px solid ${colors.buttonHover};
  border-radius: 12px;
`;

export const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: ${colors.textSecondary};
  font-size: 12px;
`;

export const Select = styled.select`
  ${textStyles}
  width: 100%;
  min-height: 40px;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid ${colors.buttonHover};
  border-radius: 8px;
  background: #fff;
  color: ${colors.textPrimary};
  font-size: 14px;

  &:focus-visible {
    outline: 2px solid ${colors.buttonBackground};
    outline-offset: 1px;
  }
`;

export const SessionList = styled.ul`
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
`;

export const SessionCard = styled.li`
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border: 1px solid ${({ $mine }) => ($mine ? colors.buttonBorder : colors.buttonHover)};
  border-radius: 12px;
  opacity: ${({ $mine }) => ($mine ? 1 : 0.75)};
`;

export const SessionHeader = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
`;

export const SessionName = styled.div`
  color: ${colors.textPrimary};
  font-size: 15px;
  font-weight: 600;
`;

export const Meta = styled.div`
  color: ${colors.textSecondary};
  font-size: 12px;
  line-height: 1.5;
`;

const BADGE_COLORS = {
  open: { border: colors.save, background: "rgba(34, 195, 142, 0.12)" },
  upcoming: { border: colors.edit, background: "rgba(246, 201, 79, 0.14)" },
  closed: { border: colors.cancel, background: "rgba(129, 153, 167, 0.12)" },
  called: { border: colors.save, background: "rgba(34, 195, 142, 0.18)" },
};

export const Badge = styled.span`
  flex: 0 0 auto;
  padding: 3px 10px;
  border-radius: 100px;
  border: 1px solid ${({ $tone }) => (BADGE_COLORS[$tone] || BADGE_COLORS.closed).border};
  background: ${({ $tone }) => (BADGE_COLORS[$tone] || BADGE_COLORS.closed).background};
  color: ${colors.textPrimary};
  font-size: 12px;
`;

export const EntryList = styled.ol`
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
`;

// minmax(0, 1fr): длинное ФИО переносится, а не раздвигает строку.
export const EntryRow = styled.li`
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: 8px;
  background: ${({ $me }) => ($me ? "rgba(34, 195, 142, 0.12)" : "transparent")};
  font-size: 13px;
  color: ${colors.textPrimary};
  overflow-wrap: anywhere;
`;

export const EntryPlace = styled.span`
  color: ${colors.textSecondary};
  font-weight: 600;
  text-align: right;
`;

// Блок «Очередь на защиту» над списком групп и списком работ (QueueEntryCard).
// Светлый фон и рамка отделяют его от списка: это не шаг сдачи работы.
export const EntryCard = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
  padding: 14px 16px;
  border: 1px solid ${colors.save};
  border-radius: 12px;
  background: rgba(34, 195, 142, 0.08);
  text-align: left;
`;

export const EntryCardTitle = styled.h2`
  ${textStyles}
  margin: 0;
  color: ${colors.textPrimary};
  font-size: 16px;
  font-weight: 600;
`;

export const EntryCardText = styled.p`
  ${textStyles}
  margin: 0;
  color: ${colors.textSecondary};
  font-size: 13px;
  line-height: 1.5;
`;

// Зелёная, а не тёмная, как кнопки списка. Текст тёмный: белый на этом
// зелёном (контраст 2.3:1) на телефоне при солнце не читается. На телефоне
// кнопка во всю ширину и не ниже 44px, чтобы попасть пальцем.
export const EntryCardButton = styled(Link)`
  ${buttonStyles}
  width: auto;
  min-height: 44px;
  box-sizing: border-box;
  align-items: center;
  align-self: flex-start;
  margin-top: 4px;
  padding: 10px 20px;
  border-color: ${colors.save};
  background: ${colors.save};
  color: ${colors.textPrimary};
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;

  &:hover {
    opacity: 0.85;
  }

  &:focus-visible {
    outline: 2px solid ${colors.buttonBackground};
    outline-offset: 2px;
  }

  @media ${PHONE} {
    align-self: stretch;
  }
`;

// Кнопка-ссылка на страницу очереди в автопредложении после проверки.
export const QueueLink = styled(Link)`
  ${buttonStyles}
  width: auto;
  min-height: 40px;
  box-sizing: border-box;
  align-items: center;
  align-self: center;
  padding: 10px 18px;
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};
  text-decoration: none;

  &:hover {
    opacity: 0.85;
  }
`;
