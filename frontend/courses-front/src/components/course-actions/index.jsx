import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Breadcrumb } from "../breadcrumb";
import { LanguageSwitcher } from "../language-switcher";
import { FixedAdminButton } from "../course-list/styled";
import {
  Page,
  Navigation,
  BackButton,
  Title,
  ActionGrid,
  ActionCard,
  ActionTitle,
} from "./styled";

export function CourseActions({ courseId, onSelectGrading, onSelectQueue, onBack }) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <Page>
      <FixedAdminButton onClick={() => navigate("/admin")}>
        {t("courseActions.forTeachers")}
      </FixedAdminButton>
      <LanguageSwitcher />
      <Navigation>
        <BackButton onClick={onBack}>{t("courseActions.back")}</BackButton>
        <Breadcrumb courseId={courseId} />
      </Navigation>
      <Title>{t("courseActions.selectAction")}</Title>
      <ActionGrid>
        <ActionCard type="button" onClick={onSelectGrading}>
          <ActionTitle>{t("courseActions.grading")}</ActionTitle>
        </ActionCard>
        <ActionCard type="button" onClick={onSelectQueue}>
          <ActionTitle>{t("courseActions.queue")}</ActionTitle>
        </ActionCard>
      </ActionGrid>
    </Page>
  );
}
