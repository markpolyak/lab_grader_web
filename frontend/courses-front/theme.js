import styled, { css } from "styled-components";

export const breakpoints = {
  tablet: "600px",
  desktop: "1024px",
};

export const colors = {
  textPrimary: "rgba(24, 24, 24, 1)",
  textSecondary: "rgba(102, 102, 102, 1)",
  buttonBorder: "rgba(60, 60, 67, 1)",
  buttonHover: "rgba(60, 60, 67, 0.1)",
  buttonBackground: "rgba(60, 60, 67, 1)",
  buttonText: "rgba(255, 255, 255, 1)",
  error: "rgba(235, 87, 87, 1)",
  edit: "rgba(246, 201, 79, 1)",
  save: "rgba(34, 195, 142, 1)",
  cancel: "rgba(129, 153, 167, 1)",
};

export const sizes = {
  fontSizeSmall: "12px",
  fontSizeMedium: "14px",
  fontSizeLarge: "18px",
  borderRadius: "100px",
  cardWidth: "430px",
  cardHeight: "228px",
  padding: "16px 24px",
};

export const textStyles = css`
  font-feature-settings: "liga" off, "clig" off;
  font-family: "Source Code Pro", monospace;
  font-style: normal;
  line-height: normal;
`;

export const buttonStyles = css`
  display: flex;
  padding: 8px 12px;
  width: 89px;
  justify-content: center;
  align-items: flex-end;
  gap: 10px;
  border-radius: ${sizes.borderRadius};
  border: 1px solid ${colors.buttonBorder};
  ${textStyles}
  font-size: ${sizes.fontSizeSmall};
  font-weight: 400;
`;

export const MainContainer = styled.div`
  display: flex;
  width: ${sizes.cardWidth};
  padding: 16px 24px;
  flex-direction: column;
  border-radius: 12px;
  background: #fff;
  box-shadow: 2px 2px 10px rgba(0, 0, 0, 0.1);
  gap: 10px;
  cursor: default;
`;

export const CardTitle = styled.h3`
  ${textStyles}
  color: ${colors.textPrimary};
  font-size: ${sizes.fontSizeLarge};
  font-weight: 500;
  margin: 0 0 24px 0;
  text-align: center;
`;
