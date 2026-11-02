import styled, { css } from "styled-components";
import { colors } from "../../../theme";

export const Page = styled.div`
  min-height: 100vh;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 16px;
  box-sizing: border-box;
  background: #f5f5f7;
`;

export const Card = styled.div`
  width: 100%;
  max-width: 520px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);
  padding: 20px 16px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
`;

export const Title = styled.h2`
  margin: 0;
  font-size: 22px;
  text-align: center;
`;

export const Hint = styled.p`
  margin: 0;
  text-align: center;
  color: ${(p) => (p.$error ? colors.error : "#666")};
  font-size: 14px;
`;

export const Status = styled.p`
  margin: 8px 0;
  text-align: center;
  font-size: 20px;
  font-weight: 600;
`;

export const Countdown = styled.div`
  text-align: center;
  font-size: 40px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
`;

export const QuestionText = styled.div`
  font-size: 18px;
  line-height: 1.4;
  overflow-x: auto;

  pre {
    background: #f3f3f3;
    padding: 8px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 14px;
  }
`;

export const AnswerButton = styled.button`
  min-height: 56px;
  width: 100%;
  padding: 12px;
  border-radius: 10px;
  border: 2px solid #ccc;
  background: #fff;
  font-size: 17px;
  text-align: left;
  cursor: pointer;
  color: #222;
  overflow-x: auto;

  &:disabled {
    cursor: default;
    color: #222;
  }

  ${(p) =>
    p.$mine &&
    css`
      border-color: #1976d2;
      background: #e3f0fd;
    `}
  ${(p) =>
    p.$correct &&
    css`
      border-color: #2e7d32;
      background: #e3f6e5;
    `}
  ${(p) =>
    p.$wrong &&
    css`
      border-color: ${colors.error};
      background: #fdeaea;
    `}
`;

export const PrimaryButton = styled.button`
  display: block;
  text-align: center;
  text-decoration: none;
  padding: 14px;
  border-radius: 10px;
  border: none;
  background: #24292f;
  color: #fff;
  font-size: 17px;
  cursor: pointer;
`;

export const OfflineBadge = styled.div`
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 12px;
  color: #fff;
  background: ${colors.error};
  padding: 2px 8px;
  border-radius: 10px;
`;
