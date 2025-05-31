import { useNavigate } from "react-router-dom";
import { CourseList } from ".";
import { FixedAdminButton } from "./styled";

export function CourseListWrapper() {
  const navigate = useNavigate();

  return (
    <>
      <FixedAdminButton onClick={() => navigate("/admin")}>
        Для преподавателей
      </FixedAdminButton>
      <CourseList
        onSelectCourse={(courseId) => navigate(`/course/${courseId}`)}
      />
    </>
  );
}
