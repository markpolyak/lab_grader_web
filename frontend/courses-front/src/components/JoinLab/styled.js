import styled, { keyframes } from "styled-components";

import { buttonStyles, colors, textStyles } from "../../../theme";


const rotate = keyframes`
  to { transform: rotate(360deg); }
`;

export const JoinPage = styled.main`
  width: min(1200px, calc(100vw - 2em));
  min-height: calc(100vh - 2em);
  box-sizing: border-box;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #f6f7f9;
`;

export const JoinCard = styled.section`
  ${textStyles}
  width: min(520px, 100%);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 32px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 12px 36px rgba(24, 24, 24, 0.1);
`;

export const Title = styled.h1`
  margin: 0;
  color: ${colors.textPrimary};
  font-size: 24px;
  line-height: 1.35;
`;

export const Description = styled.p`
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: ${colors.textSecondary};
  font-size: 14px;
  line-height: 1.6;
`;

export const Details = styled.div`
  display: grid;
  gap: 14px;
  padding: 18px;
  border: 1px solid ${colors.buttonHover};
  border-radius: 12px;
`;

export const Label = styled.div`
  margin-bottom: 4px;
  color: ${colors.textSecondary};
  font-size: 12px;
`;

export const Value = styled.div`
  color: ${colors.textPrimary};
  font-size: 15px;
  font-weight: 600;
`;

export const ActionButton = styled.button`
  ${buttonStyles}
  width: 100%;
  align-items: center;
  padding: 12px 18px;
  background: ${colors.buttonBackground};
  color: ${colors.buttonText};
  font-size: 14px;
  cursor: pointer;

  &:disabled {
    cursor: wait;
    opacity: 0.65;
  }
`;

const ResultPanel = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.5;
`;

export const SuccessPanel = styled(ResultPanel)`
  border: 1px solid ${colors.save};
  background: rgba(34, 195, 142, 0.08);
`;

export const ErrorPanel = styled(ResultPanel)`
  border: 1px solid ${colors.error};
  background: rgba(235, 87, 87, 0.08);
`;

export const RepositoryLink = styled.a`
  color: ${colors.textPrimary};
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
`;

export const Spinner = styled.span`
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
  border: 2px solid ${colors.buttonHover};
  border-top-color: ${colors.buttonBackground};
  border-radius: 50%;
  animation: ${rotate} 0.8s linear infinite;
`;
