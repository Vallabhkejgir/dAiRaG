export default function ChatComposer({ input, pending, onInputChange, onSubmit }) {
  const statusText = pending ? "dAi is thinking" : "dAi is awaiting instructions";

  function handleSubmit(event) {
    event.preventDefault();
    onSubmit();
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <div className={`chat-composer__status${pending ? " chat-composer__status--thinking" : ""}`}>
        <span className="chat-composer__dot"></span>
        <span>{statusText}</span>
      </div>
      <label className="chat-composer__label" htmlFor="chatInput">Analyze anything</label>
      <textarea
        id="chatInput"
        className="chat-composer__input"
        placeholder="Ask about an order, invoice, payment, journal entry, customer, or product"
        rows="4"
        value={input}
        onChange={(event) => onInputChange(event.target.value)}
        onKeyDown={handleKeyDown}
      ></textarea>
      <div className="chat-composer__actions">
        <button className="chat-composer__submit" type="submit" aria-label="Send message" disabled={pending || !input.trim()}>
          <span className="chat-composer__submit-icon" aria-hidden="true">&rarr;</span>
        </button>
      </div>
    </form>
  );
}
