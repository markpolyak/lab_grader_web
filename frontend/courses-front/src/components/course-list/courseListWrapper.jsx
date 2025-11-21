import { useNavigate } from "react-router-dom";
import { CourseList } from ".";
import { FixedAdminButton, PageTitle } from "./styled";

export function CourseListWrapper() {
  const navigate = useNavigate();

  return (
    <>
      <FixedAdminButton onClick={() => navigate("/admin")}>
        Для преподавателей
      </FixedAdminButton>
      <PageTitle>Проверка лабораторных работ</PageTitle>
      <CourseList
        onSelectCourse={(courseId) => navigate(`/course/${courseId}`)}
      />
    </>
  );
}
