import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { fetchCourseDetails } from "../../api";
import {
  BackLink,
  Empty,
  ErrorText,
  Header,
  LabCard,
  LabGrid,
  LabMeta,
  LabName,
  Page,
  Subtitle,
  Title,
} from "./styled";

export const AdminLabList = () => {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const [course, setCourse] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchCourseDetails(courseId)
      .then((details) => {
        setCourse(details);
        setError("");
      })
      .catch((err) => {
        setError(err.message || t("errorLoadingCourseDetails"));
        setCourse(null);
      })
      .finally(() => setLoading(false));
  }, [courseId, t]);

  const labs = course?.labs || [];

  return (
    <Page>
      <Header>
        <div>
          <Title>{t("plagiarismLabs")}</Title>
          <Subtitle>{course?.name || courseId}</Subtitle>
        </div>
        <BackLink type="button" onClick={() => navigate("/admin/courses")}>
          {t("backToCourses")}
        </BackLink>
      </Header>

      {loading && <Empty>{t("loading")}</Empty>}
      {error && <ErrorText>{error}</ErrorText>}
      {!loading && !error && labs.length === 0 && (
        <Empty>{t("noLabs")}</Empty>
      )}

      <LabGrid>
        {labs.map((lab) => (
          <LabCard
            key={lab.id}
            type="button"
            onClick={() =>
              navigate(`/admin/courses/${courseId}/labs/${lab.id}/plagiarism`)
            }
          >
            <LabName>{lab["short-name"] || lab.id}</LabName>
            <LabMeta>
              id: {lab.id}
              {lab.has_plagiarism ? ` · ${t("plagiarismConfigured")}` : ""}
            </LabMeta>
          </LabCard>
        ))}
      </LabGrid>
    </Page>
  );
};
