import styled from "styled-components";
import { buttonStyles, colors } from "../../../../theme";

export const Toolbar = styled.nav`
  position: fixed;
  top: 16px;
  left: 16px;
  z-index: 3100;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: calc(100vw - 170px);
`;
export const NavButton = styled.button`
  ${buttonStyles}
  width: auto;
  background: #fff;
  color: ${colors.textPrimary};
  align-items: center;
  cursor: pointer;
  &:disabled { opacity: 0.5; cursor: default; }
`;
export const Frame = styled.div`
  padding-top: 80px;
  padding-bottom: 24px;
`;
