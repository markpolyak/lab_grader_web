import { useParams, useNavigate } from "react-router-dom";
import { LabList } from ".";

export function LabListWrapper() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  return <LabList courseId={courseId} onBack={() => navigate("/admin/courses")} />;
}
