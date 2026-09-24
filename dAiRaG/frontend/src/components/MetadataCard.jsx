export default function MetadataCard() {
  return (
    <div id="metadataCard" className="metadata-card hidden">
      <div id="metadataCardHeader" className="metadata-card__header">
        <div>
          <p id="metadataEyebrow" className="metadata-card__eyebrow"></p>
          <h2 id="metadataTitle" className="metadata-card__title"></h2>
        </div>
        <button id="metadataCloseButton" className="metadata-card__close" type="button" aria-label="Close">
          x
        </button>
      </div>
      <p id="metadataSubtitle" className="metadata-card__subtitle"></p>
      <dl id="metadataFields" className="metadata-card__fields"></dl>
      <div className="metadata-card__footer">
        <div className="metadata-card__actions">
          <button id="overlayToggleButton" className="toolbar-button toolbar-button--dark" type="button" hidden>
            Show Granular View
          </button>
          <button id="focusNodeButton" className="toolbar-button toolbar-button--light" type="button" hidden>
            Focus Neighborhood
          </button>
          <button id="unfocusButton" className="toolbar-button toolbar-button--light" type="button" hidden>
            Unfocus
          </button>
          <button id="showAllButton" className="toolbar-button toolbar-button--ghost" type="button">
            Show Full Graph
          </button>
        </div>
        <div id="metadataConnectionsSection" className="metadata-card__connections-section">
          <div id="metadataConnectionsLabel" className="metadata-card__connections-label">Connections</div>
          <div id="metadataConnections" className="metadata-card__connections"></div>
        </div>
      </div>
    </div>
  );
}
