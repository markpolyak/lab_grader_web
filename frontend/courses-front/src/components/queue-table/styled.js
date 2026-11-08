import styled from "styled-components";
import { colors, sizes } from "../../../theme";

export const ScrollArea = styled.div`
  width: 100%;
  overflow-x: auto;
  &:focus-visible { outline: 2px solid ${colors.buttonBorder}; outline-offset: 3px; }
`;
export const Table = styled.table`
  width: 100%;
  min-width: 440px;
  border-collapse: collapse;
  text-align: left;
  font-size: ${sizes.fontSizeMedium};
  th, td {
    padding: 14px 8px;
    border-bottom: 1px solid ${colors.buttonHover};
    vertical-align: middle;
    overflow-wrap: anywhere;
  }
  th { font-weight: 600; color: ${colors.textSecondary}; white-space: nowrap; }
  th:first-child, td:first-child { width: 56px; min-width: 56px; text-align: center; }
  td:nth-child(2) { min-width: 130px; }
  td:nth-child(3) { min-width: 100px; }
  td:nth-child(4) { min-width: 42px; }
  th:nth-child(5), td:nth-child(5) { min-width: 180px; }
  button { white-space: nowrap; }
`;
export const Row = styled.tr`
  background: ${({ $status }) => $status === "called" ? "#edf7ed" : ["finished", "withdrawn", "expired"].includes($status) ? "#f3f3f3" : "#fff"};
  color: ${({ $status }) => $status === "called" ? "#1b5e20" : ["finished", "withdrawn", "expired"].includes($status) ? "#757575" : colors.textPrimary};
  ${({ $own }) => $own && `td:first-child { box-shadow: inset 3px 0 ${colors.buttonBorder}; }`}
`;
export const Name = styled.div`
  line-height: 1.5;
`;
export const EntryStatus = styled.div`
  font-size: ${sizes.fontSizeSmall};
  line-height: 1.6;
`;
