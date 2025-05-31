import { useParams, useNavigate } from "react-router-dom";
import { LabList } from ".";

export function LabListWrapper() {
  const { courseId, groupId } = useParams();
  const navigate = useNavigate();
  return (
    <LabList
      courseId={courseId}
      groupId={groupId}
      onSelectLab={(labId) =>
        navigate(`/course/${courseId}/group/${groupId}/lab/${labId}`)
      }
      onBack={() => navigate(`/course/${courseId}`)}
    />
  );
}
