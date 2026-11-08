import { useNavigate, useParams } from "react-router-dom";
import { QueueLessons } from ".";

export function QueueLessonsWrapper() {
  const { courseId } = useParams();
  const navigate = useNavigate();

  return (
    <QueueLessons
      courseId={courseId}
      onBack={() => navigate(`/course/${courseId}`)}
    />
  );
}
