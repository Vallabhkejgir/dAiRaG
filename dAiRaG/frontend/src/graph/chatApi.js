export const INITIAL_CHAT_MESSAGES = [
  {
    role: "assistant",
    content:
      "Hi! I can help you analyze the Order to Cash process. Ask about an order, invoice, payment, customer, product, or overall flow.",
  },
];

export async function postChatMessage(message) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(payload.detail || `Chat request failed with ${response.status}`);
  }

  return response.json();
}
