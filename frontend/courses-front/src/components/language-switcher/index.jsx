import { MenuItem } from "@mui/material";
import { useTranslation } from "react-i18next";
import { LanguageSelect } from "./styled";
import { SUPPORTED_LANGUAGES } from "../../language";

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();

  return (
    <LanguageSelect
      value={i18n.language}
      onChange={(event) => i18n.changeLanguage(event.target.value)}
      variant="outlined"
      size="small"
      inputProps={{ "aria-label": t("join.language") }}
      MenuProps={{
        disablePortal: true,
        sx: { zIndex: 3200 },
        PaperProps: { sx: { zIndex: 3200 } },
      }}
    >
      {SUPPORTED_LANGUAGES.map(({ code, label }) => (
        <MenuItem key={code} value={code}>
          {label}
        </MenuItem>
      ))}
    </LanguageSelect>
  );
}
