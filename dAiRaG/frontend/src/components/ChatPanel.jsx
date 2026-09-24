import ChatMessageList from "./ChatMessageList.jsx";
import ChatComposer from "./ChatComposer.jsx";

export default function ChatPanel({ messages, input, pending, onInputChange, onSubmit }) {
  return (
    <aside className="chat-shell">
      <div className="chat-shell__header">
        <div>
          <h2>Chat with dAi</h2>
        </div>
      </div>

      <div className="chat-shell__body">
        <ChatMessageList messages={messages} pending={pending} />
        <ChatComposer
          input={input}
          pending={pending}
          onInputChange={onInputChange}
          onSubmit={onSubmit}
        />
      </div>
    </aside>
  );
}
