// F3 Subscriptions: which alert categories the owner wants to hear about, per car.
// A category with no row means "do not notify" (BR-1.4), so an off toggle with no row
// is left alone instead of writing a row that says nothing.
import { useEffect, useState } from 'react';
import { getOwnerDashboard, listSubscriptions, upsertSubscription } from '../../api/endpoints.js';
import { useAsync } from '../../utils/useAsync.js';
import { humanize } from '../../utils/format.js';
import SoundType, { SOUND_TYPES } from '../../components/SoundType.jsx';
import { useToast } from '../../components/Toast.jsx';

const NOTIFY_METHODS = [
  { value: 'email', label: 'Email' },
  { value: 'in_app', label: 'In app' },
];

const EXAMPLES = {
  emergency: 'siren, horn',
  in_vehicle: 'crash, glass',
  human: 'scream, voices',
  traffic: 'engine, tyres',
  environmental: 'rain, wind',
};

export default function SubscriptionsTab() {
  const toast = useToast();
  const cars = useAsync(getOwnerDashboard, []);
  const [carId, setCarId] = useState('');
  const [enabled, setEnabled] = useState({});
  const [method, setMethod] = useState('email');
  const [saving, setSaving] = useState(false);

  const carList = cars.data?.cars ?? [];
  useEffect(() => {
    if (!carId && carList.length > 0) setCarId(String(carList[0].id));
  }, [carList, carId]);

  const rows = useAsync(() => (carId ? listSubscriptions({ car_id: carId }) : Promise.resolve([])), [carId]);

  // Load the saved state into the form whenever the car changes.
  useEffect(() => {
    if (!rows.data) return;
    const byType = Object.fromEntries(rows.data.map((r) => [r.alert_type, r]));
    setEnabled(Object.fromEntries(SOUND_TYPES.map((t) => [t, !!byType[t]?.enabled])));
    setMethod(rows.data[0]?.notify_method ?? 'email');
  }, [rows.data]);

  const saved = Object.fromEntries((rows.data ?? []).map((r) => [r.alert_type, r]));
  const methodChanged = (rows.data ?? []).some((r) => r.notify_method !== method);
  const changedTypes = SOUND_TYPES.filter((t) => {
    const was = !!saved[t]?.enabled;
    if (enabled[t] !== was) return true;
    return methodChanged && saved[t] !== undefined;     // only rewrite rows that exist
  });

  async function save() {
    setSaving(true);
    try {
      for (const type of changedTypes) {
        await upsertSubscription({ car_id: Number(carId), alert_type: type,
                                   enabled: enabled[type], notify_method: method });
      }
      toast('Subscriptions saved');
      rows.reload();
    } catch (e) {
      toast(`Could not save (${e.status ?? 'error'}): ${e.message}`, 'error');
    } finally {
      setSaving(false);
    }
  }

  if (cars.loading && !cars.data) return <p className="muted">Loading...</p>;
  if (cars.error) return <p className="state-error">Could not load your cars: {cars.error.message}</p>;
  if (carList.length === 0) return <p className="muted">No cars are registered to your account.</p>;

  return (
    <div className="stack">
      <section className="panel">
        <div className="filter-bar">
          <label className="field">
            <span>Car</span>
            <select value={carId} onChange={(e) => setCarId(e.target.value)}>
              {carList.map((c) => (
                <option key={c.id} value={c.id}>{c.make} {c.model}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Notify by</span>
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              {NOTIFY_METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </label>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <h2>Alert categories for this car</h2>
        </div>
        {rows.error && <p className="state-error">{rows.error.message}</p>}
        <div className="toggle-grid">
          {SOUND_TYPES.map((type) => (
            <label key={type} className="toggle">
              <input
                type="checkbox"
                role="switch"
                checked={!!enabled[type]}
                disabled={rows.loading}
                onChange={(e) => setEnabled({ ...enabled, [type]: e.target.checked })}
              />
              <span className="toggle-track" aria-hidden="true" />
              <SoundType value={type} />
              <span className="muted small">({EXAMPLES[type]})</span>
            </label>
          ))}
        </div>
        <p className="muted small">
          A notification is only created for categories that are on. Email goes through AWS SES;
          in-app notices appear in the Alerts drawer.
        </p>
        <div className="action-row">
          <button className="btn btn-primary" onClick={save} disabled={saving || changedTypes.length === 0}>
            {saving ? 'Saving...' : 'Save'}
          </button>
          <span className="muted small">
            {changedTypes.length === 0
              ? 'No changes'
              : `${changedTypes.length} change${changedTypes.length > 1 ? 's' : ''}: ${changedTypes.map(humanize).join(', ')}`}
          </span>
        </div>
      </section>
    </div>
  );
}
