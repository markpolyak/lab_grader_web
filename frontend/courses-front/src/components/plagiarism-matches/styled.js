import styled from "styled-components";
import { colors, sizes, textStyles, breakpoints } from "../../../theme";

export const Page = styled.div`
  max-width: 900px;
  margin: 0 auto;
  padding: 24px 16px 48px;
`;

export const Header = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 24px;
`;

export const Title = styled.h1`
  ${textStyles}
  font-size: ${sizes.fontSizeLarge};
  margin: 0;
  color: ${colors.textPrimary};
`;

export const Subtitle = styled.p`
  ${textStyles}
  font-size: ${sizes.fontSizeMedium};
  color: ${colors.textSecondary};
  margin: 4px 0 0;
`;

export const BackLink = styled.button`
  ${textStyles}
  background: none;
  border: 1px solid ${colors.buttonBorder};
  border-radius: 8px;
  padding: 8px 14px;
  cursor: pointer;
  color: ${colors.buttonBorder};

  &:hover {
    background: ${colors.buttonHover};
  }
`;

export const LabGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
`;

export const LabCard = styled.button`
  ${textStyles}
  text-align: left;
  padding: 14px 16px;
  border: 1px solid ${colors.buttonBorder};
  border-radius: 10px;
  background: #fff;
  cursor: pointer;

  &:hover {
    background: ${colors.buttonHover};
  }
`;

export const LabName = styled.div`
  font-size: ${sizes.fontSizeMedium};
  color: ${colors.textPrimary};
`;

export const LabMeta = styled.div`
  font-size: ${sizes.fontSizeSmall};
  color: ${colors.textSecondary};
  margin-top: 4px;
`;

export const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  ${textStyles}
  font-size: ${sizes.fontSizeMedium};

  th,
  td {
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  }

  th {
    color: ${colors.textSecondary};
    font-weight: 600;
  }

  @media (max-width: ${breakpoints.tablet}) {
    font-size: ${sizes.fontSizeSmall};

    th,
    td {
      padding: 8px 6px;
    }
  }
`;

export const Empty = styled.p`
  ${textStyles}
  color: ${colors.textSecondary};
  margin-top: 24px;
`;

export const ErrorText = styled.p`
  ${textStyles}
  color: ${colors.error};
  margin-top: 16px;
`;

export const ActionButton = styled.button`
  ${textStyles}
  font-size: ${sizes.fontSizeSmall};
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid ${colors.buttonBorder};
  background: #fff;
  cursor: pointer;

  &:hover {
    background: ${colors.buttonHover};
  }

  &:disabled {
    opacity: 0.5;
    cursor: default;
  }
`;

export const Filters = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  ${textStyles}
  font-size: ${sizes.fontSizeMedium};
`;
