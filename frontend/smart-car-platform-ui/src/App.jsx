import { Navigate, Route, Routes } from 'react-router-dom';
import { RoleProvider } from './config/RoleContext.jsx';
import { ToastProvider } from './components/Toast.jsx';
import AppLayout from './layout/AppLayout.jsx';
import RequireAccess from './layout/RequireAccess.jsx';
import PlaceholderPage from './pages/PlaceholderPage.jsx';
import AlertsPage from './pages/AlertsPage.jsx';
import AlertDetailPage from './pages/AlertDetailPage.jsx';
import ConfigPage from './pages/ConfigPage.jsx';

const guard = (page, el) => <RequireAccess page={page}>{el}</RequireAccess>;

export default function App() {
  return (
    <RoleProvider>
      <ToastProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={guard('dashboard', <PlaceholderPage pageKey="dashboard" />)} />
            <Route path="/map" element={guard('map', <PlaceholderPage pageKey="map" />)} />
            <Route path="/tracking" element={guard('tracking', <PlaceholderPage pageKey="tracking" />)} />
            <Route path="/alerts" element={guard('alerts', <AlertsPage />)} />
            <Route path="/alerts/:id" element={guard('alerts', <AlertDetailPage />)} />
            <Route path="/simulator" element={guard('simulator', <PlaceholderPage pageKey="simulator" />)} />
            <Route path="/devices" element={guard('devices', <PlaceholderPage pageKey="devices" />)} />
            <Route path="/config" element={guard('config', <ConfigPage />)} />
            <Route path="*" element={<div className="placeholder"><h1>Not found</h1></div>} />
          </Route>
        </Routes>
      </ToastProvider>
    </RoleProvider>
  );
}
