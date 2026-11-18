import { Route, Routes } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./components/Layout";
import { AccountPage } from "./pages/Account";
import { DashboardPage } from "./pages/Dashboard";
import { IssueDraftPage } from "./pages/IssueDraft";
import { LoginPage } from "./pages/Login";
import { NotFoundPage } from "./pages/NotFound";
import { RegisterPage } from "./pages/Register";
import { RepoDetailPage } from "./pages/RepoDetail";
import { ScanDetailPage } from "./pages/ScanDetail";
import { UsersPage } from "./pages/Users";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/repos/new" element={<DashboardPage addOpen />} />
        <Route path="/repos/:id" element={<RepoDetailPage />} />
        <Route path="/scans/:id" element={<ScanDetailPage />} />
        <Route path="/issue-drafts/:id" element={<IssueDraftPage />} />
        <Route path="/account" element={<AccountPage />} />
        <Route element={<RequireAdmin />}>
          <Route path="/users" element={<UsersPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
