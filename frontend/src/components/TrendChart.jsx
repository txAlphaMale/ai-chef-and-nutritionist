/** Minimal inline-SVG line chart -- no charting library dependency.
 * `points`: array of { date: "YYYY-MM-DD", value: number }, any order.
 * Renders nothing but a hint if there are fewer than 2 points to draw a
 * line between. Colors come from the app's CSS variables so it stays
 * themed with everything else (see styles/theme.css). */
export default function TrendChart({ points, label, unit }) {
  const clean = (points || []).filter((p) => p.value != null).slice().sort((a, b) => (a.date < b.date ? -1 : 1));

  if (clean.length < 2) {
    return (
      <div className="trend-chart-empty">
        <span className="hint">{label}: not enough data yet for a trend line</span>
      </div>
    );
  }

  const width = 480;
  const height = 120;
  const padding = 24;
  const values = clean.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const xStep = (width - padding * 2) / (clean.length - 1);
  const coords = clean.map((p, i) => {
    const x = padding + i * xStep;
    const y = height - padding - ((p.value - min) / range) * (height - padding * 2);
    return { x, y, ...p };
  });

  const first = clean[0];
  const last = clean[clean.length - 1];
  const direction = last.value === first.value ? "steady" : last.value < first.value ? "down" : "up";

  return (
    <div className="trend-chart">
      <div className="trend-chart-header">
        <strong>{label}</strong>
        <span className={`tag trend-${direction}`}>
          {first.value}
          {unit} &rarr; {last.value}
          {unit}
        </span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="trend-chart-svg">
        <polyline points={coords.map((c) => `${c.x},${c.y}`).join(" ")} fill="none" className="trend-line" />
        {coords.map((c, i) => (
          <circle key={i} cx={c.x} cy={c.y} r={3} className="trend-point" />
        ))}
      </svg>
      <div className="trend-chart-axis">
        <span>{first.date}</span>
        <span>{last.date}</span>
      </div>
    </div>
  );
}
