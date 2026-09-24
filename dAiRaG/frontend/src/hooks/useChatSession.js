import { startTransition, useState } from "react";
import { postChatMessage, INITIAL_CHAT_MESSAGES } from "../graph/chatApi.js";
import { applyChatResponse } from "../graph/explorerEngine.js";

let nextMessageId = 0;

function createMessage(role, content) {
  nextMessageId += 1;
  return {
    id: `chat-message-${nextMessageId}`,
    role,
    content,
  };
}

export function useChatSession() {
  const [messages, setMessages] = useState(() =>
    INITIAL_CHAT_MESSAGES.map((message) => createMessage(message.role, message.content))
  );
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit() {
    const message = input.trim();
    if (!message || pending) {
      return;
    }

    setInput("");
    setPending(true);
    startTransition(() => {
      setMessages((currentMessages) => [...currentMessages, createMessage("user", message)]);
    });

    try {
      const response = await postChatMessage(message);
      applyChatResponse(response);
      startTransition(() => {
        setMessages((currentMessages) => [
          ...currentMessages,
          createMessage("assistant", response.reply),
        ]);
      });
      return response;
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Unknown error";
      startTransition(() => {
        setMessages((currentMessages) => [
          ...currentMessages,
          createMessage("assistant", `I ran into an issue while reading the graph: ${detail}`),
        ]);
      });
      return null;
    } finally {
      setPending(false);
    }
  }

  return {
    messages,
    input,
    pending,
    setInput,
    handleSubmit,
  };
}
