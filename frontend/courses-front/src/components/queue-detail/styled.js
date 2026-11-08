import styled from "styled-components";
import { buttonStyles, colors, sizes, textStyles } from "../../../theme";
import { Input as ExistingInput } from "../registration-form/styled";

export const Content = styled.div`
  ${textStyles}
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 0 16px 24px;
`;
export const Panel = styled.section`
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);
`;
export const Heading = styled.h2`
  margin: 0;
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  line-height: 1.5;
`;
export const Muted = styled.p`
  margin: 0;
  font-size: ${sizes.fontSizeMedium};
  color: ${colors.textSecondary};
  line-height: 1.6;
`;
export const Badge = styled.span`
  padding: 4px 8px;
  border-radius: ${sizes.borderRadius};
  background: ${colors.buttonHover};
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeSmall};
`;
export const Form = styled.form`
  display: flex;
  flex-direction: column;
  gap: 8px;
`;
export const Label = styled.label`
  font-size: ${sizes.fontSizeMedium};
  color: ${colors.textPrimary};
`;
export const Input = styled(ExistingInput)`
  box-sizing: border-box;
  width: 100%;
  height: auto;
  min-height: 42px;
  cursor: text;
`;
export const Select = styled.select`
  ${textStyles}
  min-height: 42px;
  padding: 8px;
  margin-bottom: 12px;
  background: #fff;
  color: ${colors.textPrimary};
  border: 1px solid ${colors.buttonHover};
  border-radius: 8px;
  font-size: ${sizes.fontSizeMedium};
`;
export const Actions = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
`;
export const PrimaryButton = styled.button`
  ${buttonStyles}
  width: auto;
  align-items: center;
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};
  cursor: pointer;
  &:disabled { opacity: 0.55; cursor: default; }
  &:focus-visible { outline: 2px solid ${colors.buttonBorder}; outline-offset: 3px; }
`;
export const SecondaryButton = styled(PrimaryButton)`
  background: #fff;
  color: ${colors.textPrimary};
`;
