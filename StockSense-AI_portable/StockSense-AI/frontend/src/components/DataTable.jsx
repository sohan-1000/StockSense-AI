const fmt = (v) => {
  if (v === null || v === undefined) return "–";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  return String(v);
};

const label = (c) => c.replace(/_/g, " ");

export default function DataTable({ columns, rows, truncated }) {
  if (!rows?.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c}>{label(c)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : ""}>{fmt(v)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-foot">{rows.length} row{rows.length === 1 ? "" : "s"}{truncated ? " (result limited)" : ""}</div>
    </div>
  );
}
