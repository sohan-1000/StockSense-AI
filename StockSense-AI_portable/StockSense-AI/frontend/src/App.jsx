import { useEffect, useRef, useState } from "react";
import { getSuggestions, sendChat } from "./api.js";
import DocumentPanel from "./components/DocumentPanel.jsx";
import ExpiryTab from "./components/ExpiryTab.jsx";
import { ChatIcon, ClockIcon, Logo, MoonIcon, SendIcon } from "./components/Icons.jsx";
import Message from "./components/Message.jsx";

const newId = () => `conv_${Math.random().toString(36).slice(2, 14)}`;
const BILLS_URL = import.meta.env.VITE_BILLS_URL || "http://localhost:8000";

function toggleTheme() {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("ss-theme", next); } catch { /* storage may be blocked */ }
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [conversationId] = useState(newId);
  const [tab, setTab] = useState("chat");
  const [online, setOnline] = useState(null); // null = still checking
  const bottom = useRef(null);

  useEffect(() => {
    getSuggestions().then(setSuggestions).catch(() => {});
    fetch("/api/health").then((r) => setOnline(r.ok)).catch(() => setOnline(false));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const ask = async (text) => {
    const question = text.trim();
    if (!question || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: question }]);
    setLoading(true);
    try {
      const res = await sendChat(question, conversationId);
      setMessages((m) => [...m, { role: "bot", ...res }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "bot", status: "error", route: "none", answer: e.message, columns: [], data: [], sources: [] }]);
    } finally {
      setLoading(false);
    }
  };

  const askFromExpiry = (q) => {
    setTab("chat");
    ask(q);
  };

  return (
    <div className="app">
      <header className="appbar">
        <div className="appbar-in">
          <a className="brand" href="/" aria-label="StockSense AI Assistant">
            <Logo size={18} />
            <span>STOCKSENSE</span>
            <span className="product">/ ASSISTANT</span>
          </a>
          <nav className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "chat"} className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}><ChatIcon /> Chat</button>
            <button role="tab" aria-selected={tab === "expiry"} className={tab === "expiry" ? "active" : ""} onClick={() => setTab("expiry")}><ClockIcon /> Expiry</button>
          </nav>
          <div className="nav-right">
            <a className="nav-link" href={BILLS_URL} target="_blank" rel="noopener noreferrer">Bill Intake &#8599;</a>
            <button type="button" className="icon-btn" onClick={toggleTheme} title="Switch light / dark" aria-label="Switch light or dark theme"><MoonIcon /></button>
          </div>
        </div>
      </header>

      {tab === "expiry" ? (
        <div className="layout single"><ExpiryTab onAsk={askFromExpiry} /></div>
      ) : (
        <div className="layout">
          <main className="chat">
            <div className="chat-head">
              <div>
                <h1>Store assistant</h1>
              </div>
              <span className={`live ${online === false ? "off" : online === null ? "wait" : ""}`}>
                {online === null ? "Checking…" : online ? "Connected" : "Backend offline"}
              </span>
            </div>

            <div className="messages">
              {messages.length === 0 && (
                <div className="welcome">
                  <h2>Ask about your store</h2>
                  <p>Sales, stock, expiry and suppliers, in English, हिन्दी or తెలుగు. Answers come from your own data.</p>
                  <div className="suggest-grid">
                    {suggestions.map((s) => (
                      <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m, i) => <Message key={i} msg={m} />)}
              {loading && (
                <div className="msg bot">
                  <div className="bubble"><span className="typing"><i /><i /><i /></span></div>
                </div>
              )}
              <div ref={bottom} />
            </div>

            <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
              <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a question, e.g. Which products are below reorder point?" maxLength={1000} disabled={loading} aria-label="Your question" />
              <button type="submit" disabled={loading || !input.trim()}>Send <SendIcon /></button>
            </form>
          </main>
          <DocumentPanel />
        </div>
      )}
    </div>
  );
}
