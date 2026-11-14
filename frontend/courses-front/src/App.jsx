import { BrowserRouter as Router, Routes, Route, useParams } from "react-router-dom";
import { CourseList } from "./components/course-list";
import { AdminLogin } from "./components/admin/AdminLogin";
import { ProtectedRoute } from "./components/admin/ProtectedRoute";
import { CourseListWrapper } from "./components/course-list/courseListWrapper";
import { GroupListWrapper } from "./components/group-list/groupListWrapper";
import { LabListWrapper } from "./components/lab-list/labListWrapper";
import { RegistrationFormWrapper } from "./components/registration-form/registrationFormWrapper";
import { JoinLab } from "./components/JoinLab";
import { LabListWrapper as AdminLabListWrapper } from "./components/admin/LabList/labListWrapper";

import { TeacherQueue } from "./components/DefenseQueue/TeacherQueue";

function TeacherQueueRoute() {
  const { courseId } = useParams();
  return <TeacherQueue initialCourse={courseId} />;
}

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
        <Route path="/admin/queue" element={<ProtectedRoute><TeacherQueue /></ProtectedRoute>} />
        <Route path="/admin/courses/:courseId/queue" element={<ProtectedRoute><TeacherQueueRoute /></ProtectedRoute>} />
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
