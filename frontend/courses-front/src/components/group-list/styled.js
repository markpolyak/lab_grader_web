import styled from "styled-components";
import { colors, sizes, textStyles, breakpoints } from "../../../theme";

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
