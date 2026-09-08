import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams, useNavigate } from "react-router-dom";

import {
  createJoinTeam,
  fetchJoinLab,
  fetchJoinTeams,
  getJoinStartUrl,
  joinJoinTeam,
} from "../../api";
import { SUPPORTED_LANGUAGES } from "../../language";
import { ButtonBack } from "../course-list/styled";
import { CreateTeamForm } from "./CreateTeamForm";
import { MyTeamCard } from "./MyTeamCard";
import { TeamList } from "./TeamList";
import {
  ActionButton,
  Description,
  Details,
  ErrorPanel,
  JoinCard,
  JoinPage,
  Label,
  LanguageControl,
  LanguageSelect,
  RepositoryLink,
  SectionTitle,
  Spinner,
  SuccessPanel,
  TeamBadge,
  Title,
  Value,
} from "./styled";
import {
  ERROR_TRANSLATION_KEYS,
  findMyTeam,
  getSafeRepositoryUrl,
  resolveJoinView,
  shouldShowJoinAction,
} from "./state";


export function JoinLab() {
  const { courseId, labId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const [lab, setLab] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRedirecting, setIsRedirecting] = useState(false);
  const [teamsData, setTeamsData] = useState(null);
  const [teamsError, setTeamsError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busySlug, setBusySlug] = useState(null);
  const [isCreating, setIsCreating] = useState(false);

  const callbackStatus = searchParams.get("status");
  // main.py передаёт код ошибки в query-параметре `reason` (не `error` - тот
  // зарезервирован под сырой код ошибки GitHub OAuth, см. join_callback).
  const callbackReason = searchParams.get("reason");
  const username = searchParams.get("username");
  const hasLabContext = Boolean(courseId && labId);
  const isStandaloneError = !hasLabContext && callbackStatus === "error";
  const repositoryUrl = useMemo(
    () => getSafeRepositoryUrl(searchParams.get("repo_url")),
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

  const teamEnabled = Boolean(lab && lab.team && lab.team.enabled);

  const reloadTeams = useCallback(() => {
    // Признак «студент авторизован» - успешный ответ этого запроса: cookie
    // join_session помечена HttpOnly и странице не видна, зато переживает
    // перезагрузку, в отличие от query-параметра status=authenticated.
    return fetchJoinTeams(courseId, labId)
      .then((data) => {
        setTeamsData(data);
        setTeamsError(null);
        return data;
      })
      .catch((error) => {
        setTeamsData(null);
        setTeamsError(error.code || "unknown");
        return null;
      });
  }, [courseId, labId]);

  useEffect(() => {
    if (!teamEnabled) return;
    let isCurrentRequest = true;
    fetchJoinTeams(courseId, labId)
      .then((data) => {
        if (isCurrentRequest) {
          setTeamsData(data);
          setTeamsError(null);
        }
      })
      .catch((error) => {
        if (isCurrentRequest) {
          setTeamsData(null);
          setTeamsError(error.code || "unknown");
        }
      });
    return () => {
      isCurrentRequest = false;
    };
  }, [teamEnabled, courseId, labId]);

  const beginOAuth = () => {
    setIsRedirecting(true);
    window.location.assign(getJoinStartUrl(courseId, labId));
  };

  const translatedError = (code) =>
    t(ERROR_TRANSLATION_KEYS[code] || "join.errors.unknown");

  const view = resolveJoinView({ teamEnabled, teamsData, teamsError });
  const myTeam = findMyTeam(teamsData);

  const handleCreate = ({ title, description }) => {
    setActionError(null);
    setIsCreating(true);
    createJoinTeam(courseId, labId, { title, description })
      .then(() => reloadTeams())
      .catch((error) => {
        setActionError(error.code || "unknown");
        // ALREADY_IN_TEAM и TITLE_TAKEN означают, что список устарел
        reloadTeams();
      })
      .finally(() => setIsCreating(false));
  };

  const handleJoin = (slug) => {
    setActionError(null);
    setBusySlug(slug);
    joinJoinTeam(courseId, labId, slug)
      .then(() => reloadTeams())
      .catch((error) => {
        setActionError(error.code || "unknown");
        reloadTeams();
      })
      .finally(() => setBusySlug(null));
  };

  return (
    <JoinPage>
      <ButtonBack onClick={() => navigate("/")}>{t("join.back")}</ButtonBack>
      <JoinCard>
        <LanguageControl>
          <Label as="label" htmlFor="join-language">
            {t("join.language")}
          </Label>
          <LanguageSelect
            id="join-language"
            value={i18n.language.split("-")[0]}
            onChange={(event) => i18n.changeLanguage(event.target.value)}
          >
            {SUPPORTED_LANGUAGES.map(({ code, label }) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </LanguageSelect>
        </LanguageControl>

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
            <span>{translatedError(callbackReason)}</span>
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
                <Value>{lab.lab_short_name}</Value>
              </div>
            </Details>

            {teamEnabled && (
              <>
                <TeamBadge>{t("join.team.badge")}</TeamBadge>
                {(lab.team.size_max || lab.team.count_max) && (
                  <Description>
                    {t("join.team.limits", {
                      size: lab.team.size_max || t("join.team.noLimit"),
                      count: lab.team.count_max || t("join.team.noLimit"),
                    })}
                  </Description>
                )}
              </>
            )}

            {callbackStatus === "error" && (
              <ErrorPanel role="alert">
                <strong>{t("join.errorTitle")}</strong>
                <span>{translatedError(callbackReason)}</span>
              </ErrorPanel>
            )}

            {actionError && (
              <ErrorPanel role="alert">
                <strong>{t("join.errorTitle")}</strong>
                <span>{translatedError(actionError)}</span>
              </ErrorPanel>
            )}

            {view === "loading" && (
              <Description role="status">
                <Spinner aria-hidden="true" />
                {t("join.team.loading")}
              </Description>
            )}

            {view === "landing" && (
              <>
                <Description>{t("join.team.landing")}</Description>
                <ActionButton type="button" onClick={beginOAuth} disabled={isRedirecting}>
                  {isRedirecting ? t("join.redirecting") : t("join.signIn")}
                </ActionButton>
              </>
            )}

            {view === "error" && (
              <>
                <ErrorPanel role="alert">
                  <strong>{t("join.errorTitle")}</strong>
                  <span>{translatedError(teamsError)}</span>
                </ErrorPanel>
                <ActionButton type="button" onClick={() => reloadTeams()}>
                  {t("join.team.retry")}
                </ActionButton>
              </>
            )}

            {view === "picker" && (
              <>
                <SectionTitle>{t("join.team.pickTitle")}</SectionTitle>
                <Description>{t("join.team.pickDescription")}</Description>
                <TeamList
                  teams={teamsData.teams}
                  sizeMax={teamsData.size_max}
                  myTeam={teamsData.my_team}
                  busySlug={busySlug}
                  onJoin={handleJoin}
                />
                <CreateTeamForm
                  canCreate={teamsData.can_create}
                  countMax={teamsData.count_max}
                  isBusy={isCreating}
                  onCreate={handleCreate}
                />
              </>
            )}

            {view === "member" && myTeam && (
              <>
                <SectionTitle>{t("join.team.myTeamTitle")}</SectionTitle>
                <MyTeamCard
                  team={myTeam}
                  sizeMax={teamsData.size_max}
                  isBusy={busySlug === myTeam.slug}
                  onRepairAccess={() => handleJoin(myTeam.slug)}
                />
              </>
            )}

            {view === "individual" && (
              <>
                {callbackStatus === "success" && repositoryUrl ? (
                  <SuccessPanel role="status">
                    <strong>{t("join.successTitle")}</strong>
                    <span>{t("join.successDescription")}</span>
                    {username && (
                      <span>
                        {t("join.usernameLabel")}: <strong>{username}</strong>
                      </span>
                    )}
                    <RepositoryLink href={repositoryUrl} target="_blank" rel="noreferrer">
                      {t("join.openRepository")}
                    </RepositoryLink>
                  </SuccessPanel>
                ) : callbackStatus === "success" ? (
                  <ErrorPanel role="alert">
                    <strong>{t("join.errorTitle")}</strong>
                    <span>{t("join.errors.invalidRepositoryLink")}</span>
                  </ErrorPanel>
                ) : callbackStatus === "error" ? null : (
                  <Description>{t("join.description")}</Description>
                )}

                {shouldShowJoinAction(callbackStatus, repositoryUrl) && (
                  <ActionButton type="button" onClick={beginOAuth} disabled={isRedirecting}>
                    {isRedirecting ? t("join.redirecting") : t("join.signIn")}
                  </ActionButton>
                )}
              </>
            )}
          </>
        )}
      </JoinCard>
    </JoinPage>
  );
}
