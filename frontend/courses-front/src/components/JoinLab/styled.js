import styled from "styled-components";
import { buttonStyles, colors, textStyles } from "../../../theme";

export const JoinDescription = styled.div`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 20px;
`;

export const JoinButton = styled.button`
  ${buttonStyles}
  width: auto;
  padding: 10px 20px;
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid ${colors.buttonBorder};

  &:hover {
    cursor: pointer;
    opacity: 0.85;
  }
`;

export const JoinResultBox = styled.div`
  ${textStyles}
  margin-top: 8px;
  padding: 16px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  color: ${colors.textPrimary};
  background-color: ${(props) =>
    props.$type === "success" ? "#e8f5e9" : props.$type === "error" ? "#ffebee" : "#e3f2fd"};
  border: 1px solid ${(props) =>
    props.$type === "success" ? colors.save : props.$type === "error" ? colors.error : colors.cancel};
`;
