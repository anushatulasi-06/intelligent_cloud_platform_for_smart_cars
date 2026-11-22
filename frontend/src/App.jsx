import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { RoleProvider, useRole } from './config/RoleContext.jsx';
import { ROLE_HOME } from './config/access.js';
import { ToastProvider } from './components/Toast.jsx';
import AppLayout from './layout/AppLayout.jsx';
import RequireAccess from './layout/RequireAccess.jsx';
import PlaceholderPage from './pages/PlaceholderPage.jsx';
import LoginPage from './pages/login/LoginPage.jsx';
import DashboardPage from './pages/dashboard/DashboardPage.jsx';
import MapViewPage from './pages/map/MapViewPage.jsx';
import TrackingPage from './pages/tracking/TrackingPage.jsx';
import AlertsPage from './pages/alerts/AlertsPage.jsx';
import AlertDetailPage from './pages/alerts/AlertDetailPage.jsx';
import ConfigPage from './pages/config/ConfigPage.jsx';
import DeviceManagerPage from './pages/devices/DeviceManagerPage.jsx';
import SimulatorPage from './pages/simulator/SimulatorPage.jsx';

// Everything below the login page needs a token; a 401 clears the session and lands here too.
function RequireSignIn() {
  const { signedIn } = useRole();
  return signedIn ? <Outlet /> : <Navigate to="/login" replace />;
}

// The role router of the design document: owner -> F2, iot_staff -> F4, cloud_staff -> F6.
function RoleHome() {
  const { role } = useRole();
  return <Navigate to={ROLE_HOME[role]} replace />;
}

const guard = (page, el) => <RequireAccess page={page}>{el}</RequireAccess>;

export default function App() {
  return (
    <RoleProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireSignIn />}>
            <Route element={<AppLayout />}>
              <Route index element={<RoleHome />} />
              <Route path="/dashboard" element={guard('dashboard', <DashboardPage />)} />
              <Route path="/map" element={guard('map', <MapViewPage />)} />
              <Route path="/tracking" element={guard('tracking', <TrackingPage />)} />
              <Route path="/alerts" element={guard('alerts', <AlertsPage />)} />
              <Route path="/alerts/:id" element={guard('alerts', <AlertDetailPage />)} />
              <Route path="/simulator" element={guard('simulator', <SimulatorPage />)} />
              <Route path="/devices" element={guard('devices', <DeviceManagerPage />)} />
              <Route path="/config" element={guard('config', <ConfigPage />)} />
              <Route path="*" element={<div className="placeholder"><h1>Not found</h1></div>} />
            </Route>
          </Route>
        </Routes>
      </ToastProvider>
    </RoleProvider>
  );
}
