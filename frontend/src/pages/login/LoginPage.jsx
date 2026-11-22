// F1 Login. The only page you can open without a token. On success the role router sends
// owners to F2, IoT staff to F4 and cloud staff to F6.
import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { DEV_LOGINS, USE_MOCK } from '../../api/endpoints.js';
import { ROLE_HOME } from '../../config/access.js';
import { useRole } from '../../config/RoleContext.jsx';

// BR-1.2: never say which half was wrong, or an attacker learns which addresses exist.
const BAD_CREDENTIALS = 'Invalid email or password';

export default function LoginPage() {
  const { signedIn, signIn } = useRole();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  if (signedIn) return <Navigate to="/" replace />;

  async function submit(e) {
    e.preventDefault();
    const found = {};
    if (!email.trim()) found.email = 'Enter your email address';
    else if (!email.includes('@')) found.email = 'That does not look like an email address';
    if (!password) found.password = 'Enter your password';
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setBusy(true);
    try {
      const role = await signIn(email.trim(), password);
      navigate(ROLE_HOME[role] ?? '/', { replace: true });
    } catch (err) {
      setErrors({ form: err.status === 401 || err.status === 422 ? BAD_CREDENTIALS : err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">Smart Car Platform</div>
        <h1>Welcome back</h1>
        <p className="muted small">Sign in to access your dashboard.</p>

        <label className="field grow">
          <span>Email</span>
          <input
            type="text"
            autoComplete="username"
            placeholder="name@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        {errors.email && <p className="field-error">{errors.email}</p>}

        <label className="field grow">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {errors.password && <p className="field-error">{errors.password}</p>}

        {errors.form && <p className="field-error login-form-error">{errors.form}</p>}

        <button type="submit" className="btn btn-primary login-submit" disabled={busy}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>

        <p className="muted small">
          Your role is read from your token and sets the menu: owners, IoT staff or cloud staff.
        </p>

        {import.meta.env.DEV && (
          <div className="login-hint">
            <strong>Development accounts</strong>
            {DEV_LOGINS.map((a) => (
              <button
                key={a.email}
                type="button"
                className="login-hint-row"
                onClick={() => { setEmail(a.email); setPassword(a.password); }}
              >
                {a.email} · {a.role}
              </button>
            ))}
            <span className="small">{USE_MOCK ? 'Mock mode: any password works.' : 'Passwords come from the backend seed.'}</span>
          </div>
        )}
      </form>
    </div>
  );
}
