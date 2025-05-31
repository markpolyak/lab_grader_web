import styled from "styled-components";
import {
  buttonStyles,
  colors,
  sizes,
  textStyles,
  breakpoints,
} from "../../../theme";
import { Input } from "antd";

export const Button = styled.button`
  ${buttonStyles}
  font-size: ${sizes.fontSizeMedium};
  width: 430px;
  height: 34.5px;
  color: ${colors.buttonBorder};

  &:hover {
    background: none;
    border-color: ${colors.buttonBorder};
    cursor: pointer;
  }

  @media (max-width: ${breakpoints.tablet}) {
    width: 100%;
  }
`;

export const StyledInput = styled(Input)`
  width: 430px;
  height: 34.5px;
  margin-top: 8px;
  ${textStyles}
  font-size: ${sizes.fontSizeMedium};
  color: ${colors.buttonBorder};
  cursor: pointer;

  &:hover {
    border-color: ${colors.buttonBorder};
  }

  @media (max-width: ${breakpoints.tablet}) {
    width: 100%;
  }
`;

export const Text = styled.h2`
  ${textStyles}
  cursor: default;
  font-size: ${sizes.fontSizeLarge};

  @media (max-width: ${breakpoints.tablet}) {
    font-size: ${sizes.fontSizeMedium};
    text-align: center;
  }
`;

export const TextError = styled.p`
  text-align: center;
  margin-top: 8px;
  color: ${colors.error};
  font-size: ${sizes.fontSizeSmall};

  @media (max-width: ${breakpoints.tablet}) {
    font-size: 12px;
  }
`;
