import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CourseList } from "../../course-list";
import { PageTitle } from "../../course-list/styled";

export function AdminCourses() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  return (
    <>
      <PageTitle>{t("adminQueues.coursesTitle")}</PageTitle>
      <CourseList isAdmin onSelectCourse={(courseId) => navigate(`/admin/courses/${courseId}/queues`)} />
    </>
  );
}
