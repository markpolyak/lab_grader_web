import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { JoinLab } from ".";

export function JoinLabWrapper() {
  const { courseId, labId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  return (
    <JoinLab
      courseId={courseId}
      labId={labId}
      status={searchParams.get("status")}
      reason={searchParams.get("reason")}
      repoUrl={searchParams.get("repo_url")}
      username={searchParams.get("username")}
      onBack={() => navigate("/")}
    />
  );
}
