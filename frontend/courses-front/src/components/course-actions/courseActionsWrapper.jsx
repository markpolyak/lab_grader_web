import { useNavigate, useParams } from "react-router-dom";
import { CourseActions } from ".";

export function CourseActionsWrapper() {
  const { courseId } = useParams();
  const navigate = useNavigate();

  return (
    <CourseActions
      courseId={courseId}
      onSelectGrading={() => navigate(`/course/${courseId}/groups`)}
      onBack={() => navigate("/")}
    />
  );
}
