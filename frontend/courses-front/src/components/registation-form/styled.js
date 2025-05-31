import styled from "styled-components";
import { buttonStyles, colors, textStyles } from "../../../theme";

export const InputContainer = styled.div`
  display: flex;
  flex-direction: column;
  align-items: normal;
`;

export const Input = styled.input`
  ${textStyles}
  height: 28px;
  border: none;
  border-bottom: 1px solid ${colors.buttonHover};
  margin-bottom: 12px;
  padding: 8px;
  cursor: pointer;

  &::placeholder {
    color: ${colors.textSecondary};
    transition: color 0.3s ease;
  }

  &:hover {
    border: none;
    border-bottom: 1px solid ${colors.textSecondary};
    &::placeholder {
      color: ${colors.buttonBackground};
    }
  }
`;

export const RegistrationButton = styled.button`
  ${buttonStyles}
  width: auto;
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};

  &:hover {
    border: 1px solid ${colors.buttonBorder};
    cursor: pointer;
  }
`;
