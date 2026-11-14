import { useParams, useNavigate } from "react-router-dom";
import { RegistrationForm } from ".";

export function RegistrationFormWrapper() {
  const { courseId, groupId, labId } = useParams();
  const navigate = useNavigate();
  return (
    <RegistrationForm
      key={`${courseId}:${groupId}`}
      courseId={courseId}
      groupId={groupId}
      labId={labId}
      onBack={() => navigate(`/course/${courseId}/group/${groupId}`)}
    />
  );
}
