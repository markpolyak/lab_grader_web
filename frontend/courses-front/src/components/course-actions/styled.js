import styled from "styled-components";
import { breakpoints, colors, sizes, textStyles } from "../../../theme";
import { ButtonBack, PageTitle } from "../course-list/styled";

export const Page = styled.main`
  width: min(920px, calc(100vw - 64px));
  padding: 24px 0;
`;

export const Navigation = styled.div`
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
  padding: 0 16px;
`;

export const BackButton = styled(ButtonBack)`
  width: auto;
  align-items: center;
`;

export const Title = styled(PageTitle)`
  width: auto;
`;

export const ActionGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 16px;

  @media (max-width: ${breakpoints.tablet}) {
    grid-template-columns: 1fr;
  }
`;

export const ActionCard = styled.button`
  ${textStyles}
  display: flex;
  min-height: 180px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 16px;
  padding: ${sizes.padding};
  border: 1px solid transparent;
  border-radius: 12px;
  background: #fff;
  color: ${colors.textPrimary};
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);
  text-align: center;
  cursor: pointer;
  transition: background-color 0.2s, border-color 0.2s;

  &:hover:not(:disabled) {
    background: ${colors.buttonHover};
    border-color: ${colors.buttonBorder};
  }

  &:focus-visible {
    outline: 2px solid ${colors.buttonBorder};
    outline-offset: 4px;
  }

  &:disabled {
    color: ${colors.textSecondary};
    cursor: default;
  }
`;

export const ActionTitle = styled.span`
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  line-height: 1.5;
`;

export const ComingSoon = styled.span`
  padding: 6px 12px;
  border-radius: ${sizes.borderRadius};
  background: ${colors.buttonHover};
  color: ${colors.textSecondary};
  font-size: ${sizes.fontSizeSmall};
`;
