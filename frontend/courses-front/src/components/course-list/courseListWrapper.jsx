import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CourseList } from ".";
import { FixedAdminButton, PageTitle } from "./styled";

export function CourseListWrapper() {
  const navigate = useNavigate();
  const { t } = useTranslation();

  return (
    <>
      <FixedAdminButton onClick={() => navigate("/admin")}>
        {t("courseActions.forTeachers")}
      </FixedAdminButton>
      <PageTitle>{t("courseActions.selectCourse")}</PageTitle>
      <CourseList
        onSelectCourse={(courseId) => navigate(`/course/${courseId}`)}
      />
    </>
  );
}
