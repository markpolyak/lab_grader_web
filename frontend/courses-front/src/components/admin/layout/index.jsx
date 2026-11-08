import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert } from "@mui/material";
import { adminRequest } from "../../../api/adminQueues";
import { LanguageSwitcher } from "../../language-switcher";
import { Toolbar, NavButton, Frame } from "./styled";

export function AdminLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const loginPage = pathname === "/admin" || pathname === "/admin/";

  async function logout() {
    setPending(true);
    setError(false);
    try {
      await adminRequest("/logout", { method: "POST" });
      navigate("/", { replace: true });
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <Toolbar aria-label={t("adminQueues.navigation")}>
        <NavButton onClick={() => navigate("/")}>{t("adminQueues.home")}</NavButton>
        {!loginPage && <NavButton onClick={() => navigate("/admin/courses")}>{t("adminQueues.courses")}</NavButton>}
        {!loginPage && <NavButton disabled={pending} onClick={logout}>{t("logout")}</NavButton>}
      </Toolbar>
      {pathname !== "/admin/courses" && <LanguageSwitcher />}
      <Frame>
        {error && <Alert severity="error">{t("adminQueues.logoutFailed")}</Alert>}
        <Outlet />
      </Frame>
    </>
  );
}
