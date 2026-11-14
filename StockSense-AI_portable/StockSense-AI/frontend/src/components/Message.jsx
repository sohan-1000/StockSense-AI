import { useState } from "react";
import { sendFeedback } from "../api.js";
import DataTable from "./DataTable.jsx";
import { DownIcon, UpIcon } from "./Icons.jsx";
import ResultChart from "./ResultChart.jsx";

const ROUTE_LABEL = { sql: "Answered from your store data (SQL)", rag: "Answered from shopkeeper documents (RAG)", unsupported: "Not available in data", none: "" };

export default function Message({ msg }) {
  const [showSql, setShowSql] = useState(false);
  const [rated, setRated] = useState(null);

  if (msg.role === "user") return <div className="msg user"><div className="bubble">{msg.text}</div></div>;

  const rate = async (rating) => {
    setRated(rating);
    try {
      await sendFeedback(msg.message_id, rating);
    } catch {
      setRated(null);
    }
  };

  return (
    <div className="msg bot">
      <div className={`bubble ${msg.route || ""}`}>
        {msg.route && ROUTE_LABEL[msg.route] && <span className={`badge ${msg.route}`}>{ROUTE_LABEL[msg.route]}</span>}
        <p className={msg.status === "error" ? "error-text" : ""}>{msg.answer}</p>

        <ResultChart chart={msg.chart} columns={msg.columns} rows={msg.data} />
        <DataTable columns={msg.columns} rows={msg.data} truncated={msg.truncated} />

        {msg.sources?.length > 0 && (
          <div className="sources">
            <strong>Sources</strong>
            {msg.sources.map((s, i) => (
              <details key={i}>
                <summary>{s.document}{s.page ? `, page ${s.page}` : ""}</summary>
                <p>{s.snippet}{s.snippet.length >= 300 ? "…" : ""}</p>
              </details>
            ))}
          </div>
        )}

        {msg.sql && (
          <div className="sql">
            <button className="link" onClick={() => setShowSql(!showSql)}>{showSql ? "Hide" : "Show"} generated SQL</button>
            {showSql && <pre>{msg.sql}</pre>}
          </div>
        )}

        {msg.meta?.notes && <div className="note">{msg.meta.notes}</div>}

        {msg.message_id && (
          <div className="feedback">
            <button className={rated === 1 ? "on" : ""} disabled={rated !== null} onClick={() => rate(1)} aria-label="Helpful"><UpIcon /></button>
            <button className={rated === -1 ? "on" : ""} disabled={rated !== null} onClick={() => rate(-1)} aria-label="Not helpful"><DownIcon /></button>
          </div>
        )}
      </div>
    </div>
  );
}
