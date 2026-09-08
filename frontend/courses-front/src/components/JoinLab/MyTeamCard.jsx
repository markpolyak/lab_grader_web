import { useTranslation } from "react-i18next";

import {
  FieldHint,
  MemberChip,
  RepositoryLink,
  SecondaryButton,
  SuccessPanel,
  TeamCount,
  TeamHeader,
  TeamMembers,
  TeamName,
} from "./styled";
import { getSafeRepositoryUrl } from "./state";


export function MyTeamCard({ team, sizeMax, isBusy, onRepairAccess }) {
  const { t } = useTranslation();
  const repositoryUrl = getSafeRepositoryUrl(team.repo_url);

  return (
    <SuccessPanel role="status">
      <TeamHeader>
        <TeamName>{team.title || team.slug}</TeamName>
        <TeamCount>
          {sizeMax
            ? t("join.team.sizeOf", { size: team.size, max: sizeMax })
            : t("join.team.size", { count: team.size })}
        </TeamCount>
      </TeamHeader>

      {team.description && <FieldHint>{team.description}</FieldHint>}

      <TeamMembers>
        {team.members.map((login) => (
          <MemberChip key={login}>{login}</MemberChip>
        ))}
        {team.pending.map((login) => (
          <MemberChip key={login} $pending>
            {login} · {t("join.team.pending")}
          </MemberChip>
        ))}
      </TeamMembers>

      {repositoryUrl ? (
        <RepositoryLink href={repositoryUrl} target="_blank" rel="noreferrer">
          {t("join.team.openRepository")}
        </RepositoryLink>
      ) : (
        <FieldHint>{t("join.errors.invalidRepositoryLink")}</FieldHint>
      )}

      <FieldHint>{t("join.team.memberHint")}</FieldHint>

      {/* Замена github-reinvite: пересоздаёт протухшее приглашение,
          повторно проходя §4 плана #46 для репозитория команды. */}
      <SecondaryButton type="button" disabled={isBusy} onClick={onRepairAccess}>
        {isBusy ? t("join.team.repairing") : t("join.team.repairAccess")}
      </SecondaryButton>
    </SuccessPanel>
  );
}
