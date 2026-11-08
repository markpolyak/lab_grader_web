import { useNavigate, useParams } from "react-router-dom";
import { QueueDetail } from ".";

export function QueueDetailWrapper() {
  const { courseId, lessonId } = useParams();
  const navigate = useNavigate();
  return (
    <QueueDetail
      key={`${courseId}/${lessonId}`}
      courseId={courseId}
      lessonId={lessonId}
      onBack={() => navigate(`/course/${courseId}/queue`)}
    />
  );
}
