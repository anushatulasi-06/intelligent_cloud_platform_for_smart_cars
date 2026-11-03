// Pill badge with a text label, coloured by value (colours in theme.css).
//
// Usage:
//   <StatusBadge value="critical" />            // severity: critical | high | medium | low
//   <StatusBadge value="acknowledged" />        // alert status: new | acknowledged | resolved
//   <StatusBadge value="online" />              // device / store / job status
//   <StatusBadge value="review" />              // anomaly tag
//   <StatusBadge value="x" tone="indigo" />     // force a colour: any badge-* name below
const TONES = {
  // severity
  critical: 'critical', high: 'high', medium: 'medium', low: 'low',
  // alert status
  new: 'new', acknowledged: 'acknowledged', resolved: 'resolved',
  // devices, cars, stores, jobs, records
  online: 'online', moving: 'online', healthy: 'online', succeeded: 'online', processed: 'online', enabled: 'online',
  offline: 'offline', parked: 'offline', registered: 'offline', inactive: 'offline', disabled: 'offline', pending: 'offline',
  failed: 'failed', down: 'failed',
  degraded: 'medium',
  queued: 'new', running: 'new', processing: 'new', configuring: 'new',
  active: 'indigo', review: 'indigo',
};

export default function StatusBadge({ value, tone, label }) {
  if (value === null || value === undefined || value === '') return <span>-</span>;
  const t = tone || TONES[value] || 'offline';
  return <span className={`badge badge-${t}`}>{label ?? String(value).replace(/_/g, ' ')}</span>;
}
