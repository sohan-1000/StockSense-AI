async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new Error("Cannot reach the server. Is the backend running?");
  }
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const sendChat = (question, conversationId) =>
  request("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });

export const sendFeedback = (messageId, rating) =>
  request(`/api/chat/${messageId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating }),
  });

export const getSuggestions = () => request("/api/suggestions");

export const getExpiry = ({ days, category, search }) => {
  const p = new URLSearchParams();
  if (days !== null && days !== undefined) p.set("days", days);
  if (category) p.set("category", category);
  if (search) p.set("search", search);
  return request(`/api/expiry?${p.toString()}`);
};
export const listDocuments = () => request("/api/documents");

export const uploadDocument = (file) => {
  const form = new FormData();
  form.append("file", file);
  return request("/api/documents", { method: "POST", body: form });
};
