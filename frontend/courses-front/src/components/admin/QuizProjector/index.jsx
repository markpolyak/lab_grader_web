import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { QRCodeSVG } from "qrcode.react";
import { QuizMarkdown } from "../../quiz-markdown";

// Экран проектора (docs/QUIZ_PLAN.md §10.2): QR-код и подключившиеся, вопрос с
// отсчётом, правильный ответ и распределение, итог и запись в таблицу.
// «Завершить» и «Отменить» доступны всё время, пока квиз идёт.

const BAR_COLORS = ["#1976d2", "#9c27b0", "#ef6c00", "#00897b"];

const styles = {
  page: { minHeight: "100vh", padding: "72px 32px 32px", boxSizing: "border-box", background: "#fff" },
  top: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" },
  title: { fontSize: 32, margin: 0 },
  muted: { color: "#666", fontSize: 22 },
  big: { fontSize: 30, lineHeight: 1.35 },
  option: (highlight) => ({
    fontSize: 26,
    padding: "12px 16px",
    margin: "8px 0",
    borderRadius: 10,
    border: `3px solid ${highlight ? "#2e7d32" : "#ddd"}`,
    background: highlight ? "#e3f6e5" : "#fafafa",
    display: "flex",
    gap: 12,
    alignItems: "center",
  }),
  button: (color) => ({
    fontSize: 20,
    padding: "10px 20px",
    borderRadius: 8,
    border: "none",
    background: color,
    color: "#fff",
    cursor: "pointer",
  }),
  countdown: { fontSize: 64, fontWeight: 700, fontVariantNumeric: "tabular-nums" },
};

export const QuizProjector = () => {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const base = `/api/v1/admin/quiz-runs/${encodeURIComponent(runId)}`;

  const [snap, setSnap] = useState(null);
  const [link, setLink] = useState("");
  const [error, setError] = useState(null);
  const [offline, setOffline] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const deadlineRef = useRef(null);

  const versionRef = useRef(-1);

  // The answer to a button press and SSE events race each other: keep only
  // snapshots newer than the one on screen.
  const apply = (data) => {
    if (!data || data.version < versionRef.current) return;
    versionRef.current = data.version;
    setSnap((prev) => ({ ...data, link: data.link ?? prev?.link }));
    // One countdown: to the end of the question, then to the next question.
    const left = data.state === "question" ? data.remaining_ms : data.state === "reveal" ? data.next_in_ms : null;
    deadlineRef.current = left !== null && left !== undefined ? Date.now() + left : null;
    setRemaining(left || 0);
  };

  useEffect(() => {
    fetch(base, { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error(response.status === 404 ? t("quizAdmin.runNotFound") : `HTTP ${response.status}`);
        const data = await response.json();
        setLink(data.link);
        apply(data);
      })
      .catch((err) => setError(err.message));

    const source = new EventSource(`${base}/events`, { withCredentials: true });
    source.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setOffline(false);
      apply(data);
      if (data.state === "cancelled" || (data.state === "finished" && !data.writing)) source.close();
    };
    source.onerror = () => setOffline(true);
    return () => source.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (deadlineRef.current) setRemaining(Math.max(0, deadlineRef.current - Date.now()));
    }, 200);
    return () => clearInterval(timer);
  }, []);

  // In the room only the QR code carries a code that changes every 15 s:
  // refresh it while joining is open.
  const joinOpen = snap && (snap.state === "lobby" || (snap.state === "question" && snap.question === 0));
  const rotating = !!(snap && snap.in_room && joinOpen);
  useEffect(() => {
    if (!rotating) return undefined;
    const timer = setInterval(() => {
      fetch(base, { credentials: "include" })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => data && setLink(data.link))
        .catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [rotating, base]);

  const act = async (action) => {
    const response = await fetch(`${base}/${action}`, { method: "POST", credentials: "include" });
    const data = await response.json().catch(() => null);
    if (response.ok) apply(data);
    else setError((data && (data.message || data.detail)) || `HTTP ${response.status}`);
  };

  // Under the question: end it early; at the answer: the auto-advance countdown,
  // "Дальше" right now, "Пауза" to stay on the answer.
  const questionControls = snap && (
    <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginTop: 16 }}>
      {snap.state === "question" && (
        <button style={styles.button("#555")} onClick={() => act("end-question")}>
          {t("quizAdmin.projector.endQuestion")}
        </button>
      )}
      {snap.state === "reveal" && (
        <>
          <button style={styles.button("#1976d2")} onClick={() => act("next")}>
            {snap.question + 1 < snap.total ? t("quizAdmin.projector.next") : t("quizAdmin.projector.toResults")}
          </button>
          {snap.next_in_ms !== null && snap.next_in_ms !== undefined && (
            <>
              <button style={styles.button("#777")} onClick={() => act("hold")}>
                {t("quizAdmin.projector.hold")}
              </button>
              <span style={styles.muted}>
                {t(snap.question + 1 < snap.total ? "quizAdmin.projector.nextIn" : "quizAdmin.projector.resultsIn", {
                  seconds: Math.ceil(remaining / 1000),
                })}
              </span>
            </>
          )}
        </>
      )}
    </div>
  );

  if (error && !snap) return <div style={styles.page}><p style={styles.big}>{error}</p></div>;
  if (!snap) return <div style={styles.page}>{t("quiz.loading")}</div>;

  const ended = snap.state === "finished" || snap.state === "cancelled";
  const maxCount = snap.distribution ? Math.max(1, ...snap.distribution) : 1;

  return (
    <div style={styles.page}>
      <div style={styles.top}>
        <div>
          <h1 style={styles.title}>{snap.title}</h1>
          <div style={styles.muted}>
            {snap.course} · {t("quizAdmin.projector.joined", { count: snap.joined })}
            {offline && ` · ${t("quiz.reconnecting")}`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!ended && snap.state !== "lobby" && (
            <button style={styles.button("#555")} onClick={() => act("finish")}>
              {t("quizAdmin.projector.finish")}
            </button>
          )}
          {!ended && (
            <button style={styles.button("#c62828")} onClick={() => act("cancel")}>
              {t("quizAdmin.projector.cancel")}
            </button>
          )}
          {ended && (
            <button style={styles.button("#555")} onClick={() => navigate(-1)}>
              {t("quizAdmin.back")}
            </button>
          )}
        </div>
      </div>

      {error && <p style={{ color: "#c62828", fontSize: 18 }}>{error}</p>}
      {snap.warnings && snap.warnings.map((w) => <p key={w} style={{ color: "#ef6c00", fontSize: 18 }}>{w}</p>)}

      {snap.state === "lobby" && (
        <div style={{ display: "flex", gap: 48, alignItems: "center", flexWrap: "wrap", marginTop: 24 }}>
          {link && <QRCodeSVG value={link} size={320} />}
          <div>
            <p style={styles.big}>{t("quizAdmin.projector.scan")}</p>
            <p style={{ fontSize: 28, fontFamily: "monospace" }}>{link}</p>
            <p style={styles.big}>{t("quizAdmin.projector.joined", { count: snap.joined })}</p>
            {snap.in_room && <p style={styles.muted}>{t("quizAdmin.projector.inRoomLobby")}</p>}
            <button style={styles.button("#2e7d32")} onClick={() => act("start")}>
              {t("quizAdmin.projector.start")}
            </button>
          </div>
        </div>
      )}

      {(snap.state === "question" || snap.state === "reveal") && snap.label !== undefined && (
        // Контрольная: у каждого студента свой вариант - общего текста нет
        <div style={{ marginTop: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={styles.muted}>
              {t("quiz.questionOf", { n: snap.question + 1, total: snap.total })} ·{" "}
              {t("quizAdmin.projector.answered", { answered: snap.answered, joined: snap.joined })}
            </span>
            {snap.state === "question" && <span style={styles.countdown}>{Math.ceil(remaining / 1000)}</span>}
          </div>
          <p style={styles.big}>{snap.label}</p>
          <p style={styles.muted}>{t("quizAdmin.projector.ownVariants")}</p>
          {snap.state === "reveal" && snap.by_variant && (
            <table style={{ fontSize: 24, borderCollapse: "collapse" }}>
              <tbody>
                {snap.by_variant.map((v) => (
                  <tr key={v.label}>
                    <td style={{ padding: "4px 24px 4px 0" }}>{v.label}</td>
                    <td>{t("quizAdmin.projector.correctOf", { correct: v.correct, answered: v.answered })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {questionControls}
        </div>
      )}

      {(snap.state === "question" || snap.state === "reveal") && snap.label === undefined && (
        <div style={{ marginTop: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={styles.muted}>
              {t("quiz.questionOf", { n: snap.question + 1, total: snap.total })} ·{" "}
              {t("quizAdmin.projector.answered", { answered: snap.answered, joined: snap.joined })}
            </span>
            {snap.state === "question" && <span style={styles.countdown}>{Math.ceil(remaining / 1000)}</span>}
          </div>
          <div style={styles.big}>
            <QuizMarkdown>{snap.text}</QuizMarkdown>
          </div>
          {snap.in_room && snap.state === "question" && (
            <p style={styles.muted}>{t("quizAdmin.projector.answersOnPhones")}</p>
          )}
          {(snap.options || []).map((option) => {
            const correct = snap.state === "reveal" && option.id === snap.correct;
            const count = snap.distribution ? snap.distribution[option.id] : null;
            return (
              <div key={option.id} style={styles.option(correct)}>
                <strong>{option.letter}</strong>
                <div style={{ flex: 1 }}>
                  <QuizMarkdown inline>{option.text}</QuizMarkdown>
                </div>
                {count !== null && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 220 }}>
                    <div
                      style={{
                        height: 18,
                        width: `${(180 * count) / maxCount}px`,
                        background: BAR_COLORS[option.id % BAR_COLORS.length],
                        borderRadius: 4,
                      }}
                    />
                    <span>{count}</span>
                  </div>
                )}
              </div>
            );
          })}
          {questionControls}
        </div>
      )}

      {snap.state === "finished" && snap.summary && (
        <div style={{ marginTop: 24, fontSize: 22 }}>
          <p style={styles.big}>
            {t("quizAdmin.projector.summary", {
              participants: snap.summary.participants,
              average: snap.summary.average ?? "—",
              max: snap.summary.max_points,
            })}
          </p>
          {snap.writing && <p>{t("quizAdmin.projector.writing")}</p>}
          {snap.write_report && (
            <table style={{ borderCollapse: "collapse", fontSize: 18 }}>
              <tbody>
                {Object.entries(snap.write_report).map(([group, r]) => (
                  <tr key={group}>
                    <td style={{ padding: "4px 16px 4px 0", fontWeight: 600 }}>{group}</td>
                    <td style={{ padding: 4 }}>
                      {t("quizAdmin.projector.written", { count: r.written.length })}
                      {r.skipped.length > 0 &&
                        ` · ${t("quizAdmin.projector.skipped", {
                          list: r.skipped.map((s) => `${s.name || s.login} (${s.value})`).join(", "),
                        })}`}
                      {r.missing_column && ` · ${t("quizAdmin.projector.missingColumn", { column: snap.short_name })}`}
                      {r.error && <span style={{ color: "#c62828" }}> · {r.error}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!snap.writing &&
            snap.write_report &&
            Object.values(snap.write_report).some((r) => r.error || r.missing_column) && (
              <button style={{ ...styles.button("#1976d2"), marginTop: 16 }} onClick={() => act("write-results")}>
                {t("quizAdmin.projector.retryWrite")}
              </button>
            )}
        </div>
      )}

      {snap.state === "cancelled" && <p style={styles.big}>{t("quiz.cancelled")}</p>}
    </div>
  );
};
