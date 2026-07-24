import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "react-router-dom";

import { fetchJoinLab, getJoinStartUrl } from "../../api";
import {
  ActionButton,
  Description,
  Details,
  ErrorPanel,
  JoinCard,
  JoinPage,
  Label,
  RepositoryLink,
  Spinner,
  SuccessPanel,
  Title,
  Value,
} from "./styled";
import { ERROR_TRANSLATION_KEYS, getSafeRepositoryUrl } from "./state";


export function JoinLab() {
  const { courseId, labId } = useParams();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const [lab, setLab] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRedirecting, setIsRedirecting] = useState(false);

  const callbackStatus = searchParams.get("status");
  const callbackError = searchParams.get("error");
  const hasLabContext = Boolean(courseId && labId);
  const isStandaloneError = !hasLabContext && callbackStatus === "error";
  const repositoryUrl = useMemo(
    () => getSafeRepositoryUrl(searchParams.get("repository")),
    [searchParams]
  );

  useEffect(() => {
    let isCurrentRequest = true;
    setIsLoading(true);
    setLoadError(null);

    if (!hasLabContext) {
      setLab(null);
      setLoadError(isStandaloneError ? null : "join_not_found");
      setIsLoading(false);
      return () => {
        isCurrentRequest = false;
      };
    }

    fetchJoinLab(courseId, labId)
      .then((data) => {
        if (isCurrentRequest) setLab(data);
      })
      .catch((error) => {
        if (isCurrentRequest) setLoadError(error.code || "unknown");
      })
      .finally(() => {
        if (isCurrentRequest) setIsLoading(false);
      });

    // React может размонтировать route до завершения запроса. Флаг исключает
    // обновление state, относящегося к предыдущей странице курса или лабы.
    return () => {
      isCurrentRequest = false;
    };
  }, [courseId, labId, hasLabContext, isStandaloneError]);

  const beginOAuth = () => {
    setIsRedirecting(true);
    window.location.assign(getJoinStartUrl(courseId, labId));
  };

  const translatedError = (code) =>
    t(ERROR_TRANSLATION_KEYS[code] || "join.errors.unknown");

  return (
    <JoinPage>
      <JoinCard>
        <Title>{t("join.title")}</Title>

        {isLoading && (
          <Description role="status">
            <Spinner aria-hidden="true" />
            {t("join.loading")}
          </Description>
        )}

        {!isLoading && loadError && (
          <ErrorPanel role="alert">
            <strong>{t("join.errorTitle")}</strong>
            <span>{translatedError(loadError)}</span>
          </ErrorPanel>
        )}

        {!isLoading && isStandaloneError && (
          <ErrorPanel role="alert">
            <strong>{t("join.errorTitle")}</strong>
            <span>{translatedError(callbackError)}</span>
          </ErrorPanel>
        )}

        {!isLoading && lab && (
          <>
            <Details>
              <div>
                <Label>{t("join.course")}</Label>
                <Value>{lab.course_name}</Value>
              </div>
              <div>
                <Label>{t("join.lab")}</Label>
                <Value>{lab.lab_name}</Value>
              </div>
            </Details>

            {callbackStatus === "success" && repositoryUrl ? (
              <SuccessPanel role="status">
                <strong>{t("join.successTitle")}</strong>
                <span>{t("join.successDescription")}</span>
                <RepositoryLink href={repositoryUrl} target="_blank" rel="noreferrer">
                  {t("join.openRepository")}
                </RepositoryLink>
              </SuccessPanel>
            ) : callbackStatus === "success" ? (
              <ErrorPanel role="alert">
                <strong>{t("join.errorTitle")}</strong>
                <span>{t("join.errors.invalidRepositoryLink")}</span>
              </ErrorPanel>
            ) : callbackStatus === "error" ? (
              <ErrorPanel role="alert">
                <strong>{t("join.errorTitle")}</strong>
                <span>{translatedError(callbackError)}</span>
              </ErrorPanel>
            ) : (
              <Description>{t("join.description")}</Description>
            )}

            {callbackStatus !== "success" && (
              <ActionButton type="button" onClick={beginOAuth} disabled={isRedirecting}>
                {isRedirecting ? t("join.redirecting") : t("join.signIn")}
              </ActionButton>
            )}
          </>
        )}
      </JoinCard>
    </JoinPage>
  );
}
