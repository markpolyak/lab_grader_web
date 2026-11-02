import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { CourseList } from "./components/course-list";
import { AdminLogin } from "./components/admin/AdminLogin";
import { ProtectedRoute } from "./components/admin/ProtectedRoute";
import { CourseListWrapper } from "./components/course-list/courseListWrapper";
import { GroupListWrapper } from "./components/group-list/groupListWrapper";
import { LabListWrapper } from "./components/lab-list/labListWrapper";
import { RegistrationFormWrapper } from "./components/registration-form/registrationFormWrapper";
import { JoinLab } from "./components/JoinLab";
import { LabListWrapper as AdminLabListWrapper } from "./components/admin/LabList/labListWrapper";
import { QuizList } from "./components/admin/QuizList";
import { QuizProjector } from "./components/admin/QuizProjector";
import { QuizStudent } from "./components/quiz-student";

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/admin" element={<AdminLogin />} />
        <Route
          path="/admin/courses"
          element={
            <ProtectedRoute>
              <CourseList onSelectCourse={() => {}} isAdmin={true} />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/courses/:courseId/labs"
          element={
            <ProtectedRoute>
              <AdminLabListWrapper />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/courses/:courseId/quizzes"
          element={
            <ProtectedRoute>
              <QuizList />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/quiz-runs/:runId"
          element={
            <ProtectedRoute>
              <QuizProjector />
            </ProtectedRoute>
          }
        />
        {/* Квиз на лекции: студент открывает по QR-коду (docs/QUIZ_PLAN.md §10.1) */}
        <Route path="/q/:code" element={<QuizStudent />} />
        <Route path="/" element={<CourseListWrapper />} />
        <Route path="/course/:courseId" element={<GroupListWrapper />} />
        <Route
          path="/course/:courseId/group/:groupId"
          element={<LabListWrapper />}
        />
        <Route
          path="/course/:courseId/group/:groupId/lab/:labId"
          element={<RegistrationFormWrapper />}
        />
        <Route path="/join/error" element={<JoinLab />} />
        {/* Секретная ссылка на получение репозитория - тот же экран,
            данные берутся по токену (docs/SECRET_JOIN_LINKS_PLAN.md §10) */}
        <Route path="/j/:token" element={<JoinLab />} />
        <Route path="/join/:courseId/:labId" element={<JoinLab />} />
      </Routes>
    </Router>
  );
}

export default App;
