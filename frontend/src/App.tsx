import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import MainLayout from "./components/layout/MainLayout";
import DashboardPage from "./pages/DashboardPage";
import SuccessionPage from "./pages/SuccessionPage";
import PersonToPositionPage from "./pages/PersonToPositionPage";
import DataManagementPage from "./pages/DataManagementPage";
import LoginPage from "./pages/LoginPage";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        {/* Protected Routes */}
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Navigate to="/analytics" replace />} />
          <Route path="analytics" element={<DashboardPage />} />
          <Route path="succession" element={<SuccessionPage />} />
          <Route path="person-to-position" element={<PersonToPositionPage />} />
          <Route path="data-management" element={<DataManagementPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App;
