import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ActionButton,
  BackLink,
  Empty,
  ErrorText,
  Filters,
  Header,
  Page,
  Subtitle,
  Table,
  Title,
} from "./styled";

export const PlagiarismMatches = () => {
  const { courseId, labId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [includeReviewed, setIncludeReviewed] = useState(true);
  const [busyPair, setBusyPair] = useState(null);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        include_reviewed: String(includeReviewed),
      });
      const response = await fetch(
        `/api/v1/courses/${courseId}/labs/${labId}/plagiarism?${params}`,
        { credentials: "include" }
      );
      if (response.status === 401) {
        navigate("/admin");
        return;
      }
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || t("errorLoadingPlagiarism"));
      }
      setData(await response.json());
    } catch (err) {
      setError(err.message || t("errorLoadingPlagiarism"));
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, labId, includeReviewed]);

  const markReviewed = async (studentA, studentB, reviewed) => {
    const key = `${studentA}:${studentB}`;
    setBusyPair(key);
    try {
      const response = await fetch(
        `/api/v1/courses/${courseId}/labs/${labId}/plagiarism/review`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            student_a: studentA,
            student_b: studentB,
            reviewed,
          }),
        }
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || t("errorReviewingPlagiarism"));
      }
      await load();
    } catch (err) {
      setError(err.message || t("errorReviewingPlagiarism"));
    } finally {
      setBusyPair(null);
    }
  };

  const matches = data?.matches || [];

  return (
    <Page>
      <Header>
        <div>
          <Title>{t("plagiarismMatches")}</Title>
          <Subtitle>
            {courseId} · {t("lab")} {labId}
            {data?.threshold != null
              ? ` · ${t("threshold")}: ${Math.round(data.threshold * 100)}%`
              : ""}
          </Subtitle>
        </div>
        <BackLink
          type="button"
          onClick={() => navigate(`/admin/courses/${courseId}/labs`)}
        >
          {t("backToLabs")}
        </BackLink>
      </Header>

      <Filters>
        <label>
          <input
            type="checkbox"
            checked={includeReviewed}
            onChange={(e) => setIncludeReviewed(e.target.checked)}
          />{" "}
          {t("showReviewed")}
        </label>
      </Filters>

      {loading && <Empty>{t("loading")}</Empty>}
      {error && <ErrorText>{error}</ErrorText>}

      {!loading && !error && matches.length === 0 && (
        <Empty>{t("noPlagiarismMatches")}</Empty>
      )}

      {!loading && matches.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>{t("studentA")}</th>
              <th>{t("studentB")}</th>
              <th>{t("similarity")}</th>
              <th>{t("checkedAt")}</th>
              <th>{t("reviewed")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {matches.map((m) => {
              const key = `${m.student_a}:${m.student_b}`;
              return (
                <tr key={key}>
                  <td>{m.student_a}</td>
                  <td>{m.student_b}</td>
                  <td>{Math.round(m.similarity * 100)}%</td>
                  <td>
                    {m.checked_at
                      ? new Date(m.checked_at).toLocaleString()
                      : "—"}
                  </td>
                  <td>
                    {m.reviewed_by_teacher ? t("yes") : t("no")}
                  </td>
                  <td>
                    <ActionButton
                      type="button"
                      disabled={busyPair === key}
                      onClick={() =>
                        markReviewed(
                          m.student_a,
                          m.student_b,
                          !m.reviewed_by_teacher
                        )
                      }
                    >
                      {m.reviewed_by_teacher
                        ? t("unmarkReviewed")
                        : t("markReviewed")}
                    </ActionButton>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Page>
  );
};
