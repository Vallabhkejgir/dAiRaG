import { useEffect, useRef } from "react";

function avatarText(role) {
  return role === "assistant" ? "dAi" : "yOu";
}

function displayName(role) {
  return role === "assistant" ? "dAi" : "You";
}

function displayRole(role) {
  return role === "assistant" ? "Graph Agent" : "";
}

export default function ChatMessageList({ messages, pending }) {
  const scrollBodyRef = useRef(null);

  useEffect(() => {
    const element = scrollBodyRef.current;
    if (!element) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [messages, pending]);

  return (
    <div className="chat-scroll-body" ref={scrollBodyRef}>
      <div className="chat-messages">
        {messages.map((message) => (
          <div key={message.id} className={`message message--${message.role}`}>
            <div className="message__avatar">{avatarText(message.role)}</div>
            <div className="message__body">
              <div className="message__meta">
                <span className="message__name">{displayName(message.role)}</span>
                <span className="message__role">{displayRole(message.role)}</span>
              </div>
              <div className="message__bubble">{message.content}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
