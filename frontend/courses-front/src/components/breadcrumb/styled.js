import styled from "styled-components";
import { textStyles, colors, sizes } from "../../../theme";

export const BreadcrumbContainer = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  ${textStyles}
  font-size: ${sizes.fontSizeSmall};
  color: ${colors.textSecondary};
`;

export const BreadcrumbItem = styled.span`
  color: ${props => props.$active ? colors.textPrimary : colors.textSecondary};
  font-weight: ${props => props.$active ? '500' : '400'};
`;

export const BreadcrumbSeparator = styled.span`
  color: ${colors.textSecondary};
  user-select: none;
`;
