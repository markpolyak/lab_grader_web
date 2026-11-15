import { Select } from "@mui/material";
import styled from "styled-components";

export const LanguageSelect = styled(Select)`
  && {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 3200;
    min-width: 120px;
    background-color: #555;
    color: #fff;
  }

  .MuiOutlinedInput-notchedOutline {
    border-color: #555;
  }

  &.Mui-focused .MuiOutlinedInput-notchedOutline {
    border-color: #888;
  }

  .MuiSvgIcon-root {
    color: #fff;
  }
`;
