// Sound category with its coloured dot (colours: --sound-<type> in theme.css).
//
// Usage:
//   <SoundType value="in_vehicle" />     // ● in vehicle
//   <SoundType value="human" dotOnly />  // ● only (label kept for screen readers)
export const SOUND_TYPES = ['emergency', 'in_vehicle', 'human', 'traffic', 'environmental'];

export default function SoundType({ value, dotOnly = false }) {
  const label = String(value ?? '-').replace(/_/g, ' ');
  return (
    <span className="sound" title={dotOnly ? label : undefined}>
      <span className={`sound-dot sound-${value}`} aria-hidden="true" />
      {dotOnly ? <span className="visually-hidden">{label}</span> : label}
    </span>
  );
}
