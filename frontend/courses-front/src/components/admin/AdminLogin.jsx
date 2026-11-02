import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, StyledInput, Text, TextError } from "./styled";
import { CourseCardContainer } from "../course-list/styled";

// Вход через GitHub - переход всей страницей, не fetch: браузер уходит на
// github.com и возвращается через callback backend (docs/MULTI_TEACHER_PLAN.md §4).
const GITHUB_LOGIN_URL = "/api/v1/admin/login/github";

// Коды ?error=..., которые backend передаёт после неудачного входа через GitHub.
const KNOWN_ERRORS = ["oauth_failed", "not_a_teacher"];

export const AdminLogin = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [showEmergency, setShowEmergency] = useState(false);

  const oauthError = searchParams.get("error");
  const oauthErrorText = oauthError
    ? t(`adminAuth.errors.${KNOWN_ERRORS.includes(oauthError) ? oauthError : "oauth_failed"}`)
    : "";

  const handleGithubLogin = () => {
    window.location.href = GITHUB_LOGIN_URL;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    try {
      const response = await fetch("/api/v1/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ login, password }),
      });

      if (response.ok) {
        navigate("/admin/courses");
      } else {
        setError(t("adminAuth.wrongCredentials"));
      }
    } catch {
      setError(t("adminAuth.connectionError"));
    }
  };

  return (
    <CourseCardContainer style={{ padding: "16px" }}>
      <Text style={{ textAlign: "center" }}>{t("adminAuth.title")}</Text>

      <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
        {oauthErrorText && (
          <TextError style={{ textAlign: "center", maxWidth: "430px" }}>{oauthErrorText}</TextError>
        )}
        <Button type="button" onClick={handleGithubLogin} style={{ marginTop: "16px" }}>
          {t("adminAuth.signInWithGithub")}
        </Button>

        <button
          type="button"
          onClick={() => setShowEmergency((prev) => !prev)}
          aria-expanded={showEmergency}
          style={{
            marginTop: "16px",
            background: "none",
            border: "none",
            color: "#666",
            cursor: "pointer",
            textDecoration: "underline",
            fontSize: "13px",
          }}
        >
          {t("adminAuth.emergencyLogin")}
        </button>
      </div>

      {showEmergency && (
        <form
          onSubmit={handleSubmit}
          style={{ display: "flex", flexDirection: "column", width: "300px" }}
        >
          <StyledInput
            type="text"
            placeholder={t("adminAuth.login")}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            status={error ? "error" : ""}
          />
          <StyledInput
            type="password"
            placeholder={t("adminAuth.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            status={error ? "error" : ""}
          />
          {error && <TextError>{error}</TextError>}
          <Button type="submit" style={{ marginTop: error ? "0" : "16px" }}>
            {t("adminAuth.submit")}
          </Button>
        </form>
      )}
    </CourseCardContainer>
  );
};
