import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./context/ToastContext";
import { ThemeProvider } from "./context/ThemeContext";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Conversations } from "./pages/Conversations";
import { Contacts } from "./pages/Contacts";
import { Approvals } from "./pages/Approvals";
import { AIControl } from "./pages/AIControl";
import { HumanOverride } from "./pages/HumanOverride";
import { SmartMemory } from "./pages/SmartMemory";
import { WorkingHours } from "./pages/WorkingHours";
import { SystemHealth } from "./pages/SystemHealth";
import { Logs } from "./pages/Logs";
import { Settings } from "./pages/Settings";
import { ScheduledMessages } from "./pages/ScheduledMessages";

function RequireAuth({ children }: { children: JSX.Element }) {
  const { isAuthed } = useAuth();
  if (!isAuthed) return <Navigate to="/login" replace />;
  return children;
}

function Routed() {
  const { isAuthed } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={isAuthed ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/" element={<RequireAuth><Overview /></RequireAuth>} />
      <Route path="/conversations" element={<RequireAuth><Conversations /></RequireAuth>} />
      <Route path="/contacts" element={<RequireAuth><Contacts /></RequireAuth>} />
      <Route path="/approvals" element={<RequireAuth><Approvals /></RequireAuth>} />
      <Route path="/ai-control" element={<RequireAuth><AIControl /></RequireAuth>} />
      <Route path="/human-override" element={<RequireAuth><HumanOverride /></RequireAuth>} />
      <Route path="/smart-memory" element={<RequireAuth><SmartMemory /></RequireAuth>} />
      <Route path="/working-hours" element={<RequireAuth><WorkingHours /></RequireAuth>} />
      <Route path="/scheduled-messages" element={<RequireAuth><ScheduledMessages /></RequireAuth>} />
      <Route path="/system-health" element={<RequireAuth><SystemHealth /></RequireAuth>} />
      <Route path="/logs" element={<RequireAuth><Logs /></RequireAuth>} />
      <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <Routed />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
