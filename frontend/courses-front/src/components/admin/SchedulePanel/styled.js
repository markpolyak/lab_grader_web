import { styled } from "@mui/material/styles";
import { Box } from "@mui/material";

export const Panel = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  marginBottom: theme.spacing(1),
  display: "flex",
  flexDirection: "column",
  gap: theme.spacing(1),
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius,
  background: theme.palette.background.paper,
}));

export const Header = styled(Box)({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "16px",
  flexWrap: "wrap",
});

export const Actions = styled(Box)({
  display: "flex",
  alignItems: "center",
  gap: "8px",
  flexWrap: "wrap",
});
