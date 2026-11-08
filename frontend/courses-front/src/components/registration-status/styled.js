import styled from "styled-components";
import { sizes } from "../../../theme";

export const Badge = styled.span`
  display: inline-block;
  align-self: flex-start;
  padding: 4px 8px;
  border-radius: ${sizes.borderRadius};
  background: ${({ $closed }) => $closed ? "#ffebee" : "#edf7ed"};
  color: ${({ $closed }) => $closed ? "#b71c1c" : "#1b5e20"};
  font-size: ${sizes.fontSizeSmall};
`;
