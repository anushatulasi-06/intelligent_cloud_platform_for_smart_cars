// Metric card: 6px coloured bar on the left and a tinted icon square.
//
// Usage:
//   <div className="kpi-row">
//     <KpiCard label="Alerts today" value={12} color="danger" icon={<Icon name="alerts" />} />
//     <KpiCard label="PostgreSQL" value={<StatusBadge value="healthy" />} hint="4 ms" color="success" />
//   </div>
// color: primary | success | danger | warning | sky  (default primary)
export default function KpiCard({ label, value, hint, color = 'primary', icon }) {
  return (
    <div className={`kpi-card kpi-${color}`}>
      {icon && <span className="kpi-icon">{icon}</span>}
      <div className="kpi-text">
        <div className="kpi-label">{label}</div>
        <div className="kpi-value">{value ?? '-'}</div>
        {hint && <div className="kpi-hint">{hint}</div>}
      </div>
    </div>
  );
}
