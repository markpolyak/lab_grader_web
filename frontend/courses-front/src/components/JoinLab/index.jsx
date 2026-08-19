import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Spin } from "antd";
import { fetchJoinLabInfo, getJoinStartUrl } from "../../api";
import { CardTitle, MainContainer } from "../../../theme";
import { ButtonBack } from "../course-list/styled";
import { Breadcrumb } from "../breadcrumb";
import { JoinDescription, JoinButton, JoinResultBox } from "./styled";

// Maps the `reason` query param set by GET /join/callback (see docs/REPO_GENERATION_PLAN.md §7)
// to a translation key. Anything not listed here falls back to a generic message -
// still human-readable, never a raw error code shown to the student.
const ERROR_REASON_KEYS = {
  access_denied: "joinErrorAccessDenied",
  config: "joinErrorConfig",
  oauth_not_configured: "joinErrorConfig",
  oauth_exchange_failed: "joinErrorOauthFailed",
  INVALID_TEMPLATE_CONFIG: "joinErrorConfig",
  TEMPLATE_NOT_FOUND: "joinErrorTemplateNotFound",
  CREATE_FORBIDDEN: "joinErrorForbidden",
};

export const JoinLab = ({ courseId, labId, status, reason, repoUrl, username, onBack }) => {
  const { t } = useTranslation();

  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    fetchJoinLabInfo(courseId, labId)
      .then((data) => {
        setInfo(data);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        setLoadError(err.message || t("joinLoadError"));
      });
  }, [courseId, labId]);

  const handleSignIn = () => {
    window.location.href = getJoinStartUrl(courseId, labId);
  };

  return (
    <MainContainer>
      <ButtonBack onClick={onBack}>← Назад</ButtonBack>
      <Breadcrumb courseId={courseId} labId={labId} />
      <CardTitle>{t("joinPageTitle")}</CardTitle>

      {loading && <Spin size="default" />}

      {!loading && loadError && <JoinResultBox $type="error">{loadError}</JoinResultBox>}

      {!loading && !loadError && info && (
        <>
          <JoinDescription>
            <div>
              {t("joinCourseLabel")}: <strong>{info.course_name}</strong>
            </div>
            <div>
              {t("joinLabLabel")}: <strong>{info.lab_short_name}</strong>
            </div>
          </JoinDescription>

          {status === "success" ? (
            <JoinResultBox $type="success">
              <p>{t("joinSuccessMessage")}</p>
              {username && (
                <p>
                  GitHub: <strong>{username}</strong>
                </p>
              )}
              <JoinButton as="a" href={repoUrl} target="_blank" rel="noreferrer">
                {t("joinOpenRepoButton")}
              </JoinButton>
            </JoinResultBox>
          ) : status === "error" ? (
            <JoinResultBox $type="error">
              <p>{t(ERROR_REASON_KEYS[reason] || "joinErrorGeneric")}</p>
              <JoinButton onClick={handleSignIn}>{t("joinTryAgain")}</JoinButton>
            </JoinResultBox>
          ) : (
            <JoinButton onClick={handleSignIn}>{t("joinSignInButton")}</JoinButton>
          )}
        </>
      )}
    </MainContainer>
  );
};
