import TopBar from "./components/TopBar.jsx";
import GraphWorkspace from "./components/GraphWorkspace.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import { useGraphExplorer } from "./hooks/useGraphExplorer.js";
import { useChatSession } from "./hooks/useChatSession.js";
import "./styles/app.css";

export default function App() {
  useGraphExplorer();
  const chat = useChatSession();

  return (
    <div className="page-shell">
      <TopBar />
      <main className="workspace">
        <GraphWorkspace />
        <ChatPanel
          messages={chat.messages}
          input={chat.input}
          pending={chat.pending}
          onInputChange={chat.setInput}
          onSubmit={chat.handleSubmit}
        />
      </main>
    </div>
  );
}
