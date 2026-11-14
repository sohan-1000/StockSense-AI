import { useEffect, useMemo, useState } from "react";
import { getExpiry } from "../api.js";

const WINDOWS = [
  { label: "3 days", days: 3 },
  { label: "7 days", days: 7 },
  { label: "14 days", days: 14 },
  { label: "30 days", days: 30 },
  { label: "All batches", days: null },
];

const STATUS = {
  expired: { label: "Expired", cls: "expired" },
  critical: { label: "Critical (≤3 days)", cls: "critical" },
  warning: { label: "Soon (≤7 days)", cls: "warning" },
  ok: { label: "OK", cls: "ok" },
};

const money = (v) => `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const daysText = (d) => (d < 0 ? `${-d} day${d === -1 ? "" : "s"} ago` : d === 0 ? "today" : `${d} day${d === 1 ? "" : "s"}`);

const COLUMNS = [
  { key: "product_name", label: "Product" },
  { key: "category", label: "Category" },
  { key: "batch_id", label: "Batch" },
  { key: "quantity_remaining", label: "Qty", num: true },
  { key: "expiry_date", label: "Expiry date" },
  { key: "days_to_expiry", label: "Time left", num: true },
  { key: "value_at_risk", label: "Value at risk", num: true },
];

export default function ExpiryTab({ onAsk }) {
  const [days, setDays] = useState(30);
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState({ key: "days_to_expiry", dir: 1 });

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getExpiry({ days, category, search: debounced })
      .then((d) => { if (!cancelled) { setData(d); setError(""); } })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [days, category, debounced]);

  const items = useMemo(() => {
    if (!data) return [];
    const { key, dir } = sort;
    return [...data.items].sort((a, b) => {
      const x = a[key], y = b[key];
      const c = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return c * dir || a.days_to_expiry - b.days_to_expiry;
    });
  }, [data, sort]);

  const s = data?.summary;
  const clickSort = (key) => setSort((p) => (p.key === key ? { key, dir: -p.dir } : { key, dir: 1 }));

  return (
    <div className="expiry">
      <div className="expiry-head">
        <div>
          <h2>Expiring stock</h2>
          {data && <span className="hint">Days counted from the stock snapshot of {data.as_of}</span>}
        </div>
      </div>

      {s && (
        <div className="cards">
          <div className="card danger"><b>{s.within_3_days}</b><span>batches within 3 days</span></div>
          <div className="card warn"><b>{s.within_7_days}</b><span>batches within 7 days</span></div>
          <div className="card"><b>{money(s.value_at_risk_7_days)}</b><span>stock value expiring in 7 days (at cost)</span></div>
          <div className="card"><b>{s.expired}</b><span>already expired</span></div>
        </div>
      )}

      <div className="filters">
        <div className="chips-row">
          {WINDOWS.map((w) => (
            <button key={w.label} className={`pill ${days === w.days ? "active" : ""}`} onClick={() => setDays(w.days)}>{w.label}</button>
          ))}
        </div>
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {data?.categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <input type="search" placeholder="Search product…" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={60} />
      </div>

      {error && <div className="error-text">{error}</div>}
      {loading && !data && <p className="hint">Loading…</p>}

      {data && (
        <div className="table-wrap tall">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className={`sortable ${c.num ? "num" : ""}`} onClick={() => clickSort(c.key)}>
                    {c.label}{sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.batch_id} className={`row-${STATUS[i.status].cls}`}>
                  <td><span className={`pill-badge ${STATUS[i.status].cls}`}>{STATUS[i.status].label}</span></td>
                  <td>{i.product_name}</td>
                  <td>{i.category}</td>
                  <td>{i.batch_id}</td>
                  <td className="num">{i.quantity_remaining}</td>
                  <td>{i.expiry_date}</td>
                  <td className="num">{daysText(i.days_to_expiry)}</td>
                  <td className="num">{money(i.value_at_risk)}</td>
                  <td><button className="link" onClick={() => onAsk(`What is the stock and reorder point of ${i.product_name}?`)}>Ask in chat</button></td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr><td colSpan={COLUMNS.length + 2} className="empty-cell">No batches match these filters.</td></tr>
              )}
            </tbody>
          </table>
          <div className="table-foot">{data.count} of {s.total_batches} batches{loading ? " · updating…" : ""}</div>
        </div>
      )}
    </div>
  );
}
