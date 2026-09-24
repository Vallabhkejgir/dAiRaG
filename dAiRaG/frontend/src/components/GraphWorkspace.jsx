import MetadataCard from "./MetadataCard.jsx";

export default function GraphWorkspace() {
  return (
    <section className="graph-shell">
      <div id="graphStage" className="graph-stage">
        <div className="graph-toolbar">
          <button id="resetViewButton" className="toolbar-button toolbar-button--ghost" type="button">
            Reset View
          </button>
        </div>

        <div className="graph-zoom-controls">
          <button id="zoomInButton" className="zoom-button" type="button" aria-label="Zoom in">+</button>
          <button id="zoomOutButton" className="zoom-button" type="button" aria-label="Zoom out">-</button>
        </div>

        <MetadataCard />
        <canvas id="graphCanvas"></canvas>
      </div>
    </section>
  );
}
