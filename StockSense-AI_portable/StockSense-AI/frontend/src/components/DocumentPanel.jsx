import { useEffect, useRef, useState } from "react";
import { listDocuments, uploadDocument } from "../api.js";
import { UploadIcon } from "./Icons.jsx";

export default function DocumentPanel() {
  const [docs, setDocs] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef(null);

  const refresh = async () => {
    try {
      setDocs(await listDocuments());
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  // poll while any document is still being processed
  useEffect(() => {
    if (!docs.some((d) => d.status === "queued" || d.status === "processing")) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [docs]);

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError("");
    setBusy(true);
    try {
      await uploadDocument(file);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <aside className="docs">
      <h3>Shopkeeper documents</h3>
      <p className="hint">Upload PDF, DOCX or TXT guides. Document questions are answered only from these files.</p>
      <label className="upload-btn">
        <UploadIcon /> {busy ? "Uploading…" : "Upload a document"}
        <input ref={input} type="file" accept=".pdf,.docx,.txt" onChange={onFile} disabled={busy} />
      </label>
      {error && <div className="error-text">{error}</div>}
      <ul>
        {docs.map((d) => (
          <li key={d.document_id}>
            <span className="docname" title={d.filename}>{d.filename}</span>
            <span className={`status ${d.status}`}>{d.status === "ready" ? `ready · ${d.chunk_count} chunks` : d.status}</span>
            {d.error && <div className="error-text small">{d.error}</div>}
          </li>
        ))}
        {docs.length === 0 && <li className="empty">No documents uploaded yet.</li>}
      </ul>
    </aside>
  );
}
