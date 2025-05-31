import { useParams, useNavigate } from "react-router-dom";
import { GroupList } from ".";

export function GroupListWrapper() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  return (
    <GroupList
      courseId={courseId}
      onSelectGroup={(groupId) =>
        navigate(`/course/${courseId}/group/${groupId}`)
      }
      onBack={() => navigate("/")}
    />
  );
}
