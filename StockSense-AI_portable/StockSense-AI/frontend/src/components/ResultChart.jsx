import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const COLORS = ["var(--chart-1)", "var(--chart-2)"];

// The chart type/axes come from the backend (chosen deterministically from the result shape).
export default function ResultChart({ chart, columns, rows }) {
  if (!chart) return null;
  const data = rows.map((r) => Object.fromEntries(columns.map((c, i) => [c, r[i]])));
  const Chart = chart.type === "line" ? LineChart : BarChart;
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={260}>
        <Chart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis dataKey={chart.x} tick={{ fontSize: 11, fill: "var(--muted)" }} stroke="var(--line)" interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11, fill: "var(--muted)" }} stroke="var(--line)" width={56} />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, color: "var(--ink)", fontSize: 13 }} cursor={{ fill: "var(--surface-2)" }} />
          {chart.y.length > 1 && <Legend />}
          {chart.y.map((y, i) =>
            chart.type === "line" ? (
              <Line key={y} type="monotone" dataKey={y} stroke={COLORS[i % 2]} dot={false} strokeWidth={2} />
            ) : (
              <Bar key={y} dataKey={y} fill={COLORS[i % 2]} radius={[3, 3, 0, 0]} />
            )
          )}
        </Chart>
      </ResponsiveContainer>
    </div>
  );
}
