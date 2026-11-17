"use strict";

const byId = (id) => document.getElementById(id);
const plot = byId("terrain");
const statusLine = byId("status");
const profilePanel = byId("profile-panel");
const profileChart = byId("profile-chart");
const numberFormat = new Intl.NumberFormat("en-GB", {
  maximumFractionDigits: 0,
});

const palette = {
  original: ["#132446", "#214a77", "#267d9c", "#55b8ba", "#c5ecdf"],
  inverted: ["#163d3e", "#386b60", "#88a775", "#cfb17e", "#fff2d1"],
};

const defaultCamera = {
  eye: { x: 1.14, y: -1.40, z: 0.93 },
  up: { x: 0, y: 0, z: 1 },
};

let grid;
let invertedGrid;
let mode = "inverted";
let is3D = true;
let canUse3D = true;
let last3D;
let viewRevision = 0;
let needsRender = false;
let rendering = false;
let fallbackMessage = "";

const profilePoints = [];
let selectingProfile = false;
let selectedProfile = null;

function signed(value) {
  if (value === 0) return "0";
  return `${value < 0 ? "−" : "+"}${numberFormat.format(Math.abs(value))}`;
}

function setStatus(message, isError = false) {
  statusLine.textContent = message;
  statusLine.dataset.error = String(isError);
}

function hasWebGL() {
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    if (!gl) return false;
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}

function useFallback(reason) {
  canUse3D = false;
  is3D = false;
  fallbackMessage = `${reason} Showing the same cells in 2D.`;
  queueDraw();
}

function axis(title) {
  return {
    title: { text: title },
    gridcolor: "#31474e",
    zerolinecolor: "#78918f",
    showbackground: true,
    backgroundcolor: "#101e24",
    nticks: 5,
  };
}

function updateControls(peak, render3D) {
  const inverted = mode === "inverted";
  const factor = byId("exaggeration").value;

  byId("original").setAttribute("aria-pressed", String(!inverted));
  byId("inverted").setAttribute("aria-pressed", String(inverted));

  byId("mode-copy").textContent = inverted
    ? "Flip the sign of each underwater cell. A seabed cell at −6,000 m becomes a peak at +6,000 m."
    : "Seafloor elevation is shown in signed metres. Deeper cells have more negative values.";

  byId("legend-title").textContent = inverted
    ? "Artistic height (m)"
    : "Seafloor elevation (m)";

  byId("legend-ramp").style.background =
    `linear-gradient(90deg, ${palette[mode].join(", ")})`;

  const ticks = inverted ? [0, peak / 2, peak] : [-peak, -peak / 2, 0];
  const labels = ticks.map((value) => {
    const label = document.createElement("span");
    label.textContent = signed(value);
    return label;
  });
  byId("legend-ticks").replaceChildren(...labels);

  byId("factor").value = `${factor}×`;
  byId("exaggeration").disabled = !render3D;
  byId("view-toggle").textContent = render3D ? "Switch to 2D" : "Switch to 3D";
  byId("view-toggle").disabled = !canUse3D;

  byId("interaction").textContent = render3D
    ? "Drag to rotate · Scroll to zoom · Hover for coordinates and metres"
    : "Drag to zoom · Double-click to reset · Hover for coordinates and metres";
}

function sceneLayout(peak) {
  const lon = grid.longitude;
  const lat = grid.latitude;
  const midpointLatitude = (lat[0] + lat.at(-1)) * Math.PI / 360;

  const widthMetres =
    (lon.at(-1) - lon[0]) * 111320 * Math.cos(midpointLatitude);
  const heightMetres = (lat.at(-1) - lat[0]) * 111320;
  const horizontalMax = Math.max(widthMetres, heightMetres);
  const exaggeration = Number(byId("exaggeration").value);

  return {
    camera: {
      eye: { ...defaultCamera.eye },
      up: { ...defaultCamera.up },
    },
    uirevision: viewRevision,
    aspectmode: "manual",
    aspectratio: {
      x: widthMetres / horizontalMax,
      y: heightMetres / horizontalMax,
      z: 2 * peak * exaggeration / horizontalMax,
    },
    xaxis: axis("Longitude (°)"),
    yaxis: axis("Latitude (°)"),
    zaxis: {
      ...axis("Metres"),
      range: [-peak, peak],
      tickformat: ",.0f",
    },
  };
}

function profileMapTrace() {
  return {
    type: "scatter",
    mode: "lines+markers+text",
    x: profilePoints.map(([lon]) => lon),
    y: profilePoints.map(([, lat]) => lat),
    text: ["A", "B"].slice(0, profilePoints.length),
    textposition: "top center",
    textfont: { color: "#fff2cf", size: 14 },
    line: { color: "#ffe5a3", width: 2.5 },
    marker: {
      color: "#ffe5a3",
      size: 8,
      line: { color: "#0d171c", width: 1.5 },
    },
    hoverinfo: "skip",
    showlegend: false,
  };
}

async function draw() {
  const render3D = is3D;
  const inverted = mode === "inverted";
  const peak = -grid.metadata.web_deepest_m;
  const minimum = inverted ? 0 : -peak;
  const maximum = inverted ? peak : 0;
  const title = inverted ? "Artistic height" : "Seafloor elevation";
  const comparison = inverted ? "Seafloor elevation" : "Artistic height";

  const colorscale = palette[mode].map((color, index, colors) => [
    index / (colors.length - 1),
    color,
  ]);

  const common = {
    x: grid.longitude,
    y: grid.latitude,
    z: inverted ? invertedGrid : grid.elevation_m,
    customdata: inverted ? grid.elevation_m : invertedGrid,
    colorscale,
    showscale: false,
    hovertemplate:
      "%{x:.4f}° longitude · %{y:.4f}° latitude<br>"
      + `${title} %{z:,.0f} m<br>${comparison} %{customdata:,.0f} m`
      + "<extra></extra>",
  };

  const layout = {
    autosize: true,
    paper_bgcolor: "#0d171c",
    plot_bgcolor: "#101e24",
    font: { color: "#c3d7d6", family: "Segoe UI, sans-serif", size: 11 },
    margin: render3D
      ? { l: 4, r: 4, t: 2, b: 6 }
      : { l: 58, r: 14, t: 8, b: 43 },
    uirevision: viewRevision,
    showlegend: false,
  };

  const traces = [];

  if (render3D) {
    layout.scene = sceneLayout(peak);
    traces.push({
      ...common,
      type: "surface",
      cmin: minimum,
      cmax: maximum,
      connectgaps: false,
      lighting: {
        ambient: 0.76,
        diffuse: 0.73,
        roughness: 0.95,
        specular: 0.05,
      },
      lightposition: { x: -160, y: -240, z: 350 },
    });
  } else {
    layout.xaxis = axis("Longitude (°)");
    layout.yaxis = axis("Latitude (°)");
    traces.push({
      ...common,
      type: "heatmap",
      zmin: minimum,
      zmax: maximum,
      hoverongaps: false,
    });
    if (profilePoints.length) traces.push(profileMapTrace());
  }

  updateControls(peak, render3D);

  if (last3D !== undefined && last3D !== render3D) {
    Plotly.purge(plot);
  }
  last3D = render3D;

  await Plotly.react(plot, traces, layout, {
    responsive: true,
    scrollZoom: render3D,
    displaylogo: false,
    modeBarButtonsToRemove: ["toImage"],
  });

  if (is3D !== render3D) return;

  if (render3D && !plot.querySelector("canvas")) {
    throw new Error("A WebGL plot could not be created.");
  }

  plot.removeAllListeners?.("plotly_click");
  plot.removeAllListeners?.("plotly_webglcontextlost");

  if (render3D) {
    plot.on("plotly_webglcontextlost", () => useFallback("The 3D context was lost."));
  } else {
    plot.on("plotly_click", selectProfilePoint);
  }

  setStatus(fallbackMessage, Boolean(fallbackMessage));

  try {
    await renderProfile(inverted);
  } catch (error) {
    byId("profile-summary").textContent = "Cross-section could not be drawn.";
    console.error(error);
  }
}

async function queueDraw() {
  needsRender = true;
  if (rendering) return;
  rendering = true;

  try {
    while (needsRender) {
      needsRender = false;
      try {
        await draw();
      } catch (error) {
        if (last3D && is3D) {
          useFallback("3D rendering is unavailable.");
        } else if (!needsRender) {
          setStatus(`Terrain could not be drawn. ${error.message}`, true);
        }
      }
    }
  } finally {
    rendering = false;
  }
}

function nearestCell(axisValues, coordinate) {
  let low = 0;
  let high = axisValues.length - 1;

  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (axisValues[middle] < coordinate) low = middle;
    else high = middle;
  }

  return coordinate - axisValues[low] <= axisValues[high] - coordinate
    ? low
    : high;
}

function segmentKm(lon1, lat1, lon2, lat2) {
  const rad = Math.PI / 180;
  const a = Math.sin((lat2 - lat1) * rad / 2) ** 2
    + Math.cos(lat1 * rad) * Math.cos(lat2 * rad)
    * Math.sin((lon2 - lon1) * rad / 2) ** 2;
  return 2 * 6371.0088 * Math.asin(Math.min(1, Math.sqrt(a)));
}

function sampleProfile([a, b]) {
  const lon = grid.longitude;
  const lat = grid.latitude;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const columns = Math.abs(dx) * (lon.length - 1) / (lon.at(-1) - lon[0]);
  const rows = Math.abs(dy) * (lat.length - 1) / (lat.at(-1) - lat[0]);
  const steps = Math.max(2, Math.ceil(3 * Math.max(columns, rows)));

  const result = { distance: [], depth: [], coordinates: [] };
  let distance = 0;
  let previous = a;

  for (let i = 0; i <= steps; i += 1) {
    const x = a[0] + dx * i / steps;
    const y = a[1] + dy * i / steps;

    if (i > 0) distance += segmentKm(previous[0], previous[1], x, y);

    const row = nearestCell(lat, y);
    const column = nearestCell(lon, x);

    result.distance.push(distance);
    result.depth.push(grid.elevation_m[row][column]);
    result.coordinates.push([x, y]);
    previous = [x, y];
  }

  return result;
}

function selectProfilePoint(event) {
  if (!selectingProfile || is3D) return;

  const point = event.points?.find((item) => item.curveNumber === 0);
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return;

  const column = nearestCell(grid.longitude, point.x);
  const row = nearestCell(grid.latitude, point.y);
  if (grid.elevation_m[row][column] === null) return;

  const lon = grid.longitude[column];
  const lat = grid.latitude[row];
  const start = profilePoints[0];

  if (start && lon === start[0] && lat === start[1]) {
    byId("profile-help").textContent = "Choose a different ocean cell for B.";
    return;
  }

  profilePoints.push([lon, lat]);

  if (profilePoints.length === 2) {
    selectingProfile = false;
    selectedProfile = sampleProfile(profilePoints);
  }

  queueDraw();
}

async function renderProfile(inverted) {
  byId("clear-profile").disabled = !selectingProfile && !selectedProfile;

  if (selectingProfile) {
    byId("profile-help").textContent = is3D
      ? "Switch to 2D to finish selecting A–B."
      : `Click an ocean cell for point ${profilePoints.length ? "B" : "A"}.`;
  } else {
    byId("profile-help").textContent = selectedProfile
      ? "A–B selected. Draw again to choose a new line."
      : "Choose two ocean cells in 2D.";
  }

  profilePanel.hidden = !selectedProfile;
  if (!selectedProfile) {
    if (profileChart.data) Plotly.purge(profileChart);
    return;
  }

  const { distance, depth, coordinates } = selectedProfile;
  const values = inverted
    ? depth.map((value) => value === null ? null : -value)
    : depth;

  const maxMetres = Math.max(1, ...depth.filter(Number.isFinite).map(Math.abs)) * 1.06;
  const length = distance.at(-1);
  byId("profile-summary").textContent =
    `${length.toFixed(1)} km · Masked cells appear as gaps`;

  await Plotly.react(profileChart, [{
    type: "scatter",
    mode: "lines",
    x: distance,
    y: values,
    customdata: coordinates,
    connectgaps: false,
    line: { color: inverted ? "#e7d3a8" : "#67c7ce", width: 2 },
    hovertemplate:
      "%{x:.2f} km · %{y:,.0f} m<br>"
      + "%{customdata[0]:.4f}°, %{customdata[1]:.4f}°<extra></extra>",
  }], {
    autosize: true,
    paper_bgcolor: "#101e24",
    plot_bgcolor: "#101e24",
    font: { color: "#c3d7d6", size: 11 },
    margin: { l: 65, r: 16, t: 6, b: 39 },
    xaxis: {
      title: { text: "Distance from A (km)" },
      range: [0, length],
      gridcolor: "#31474e",
      fixedrange: true,
    },
    yaxis: {
      title: { text: inverted ? "Height (m)" : "Elevation (m)" },
      range: inverted ? [0, maxMetres] : [-maxMetres, 0],
      nticks: 4,
      gridcolor: "#31474e",
      tickformat: ",.0f",
      fixedrange: true,
    },
  }, {
    responsive: true,
    displayModeBar: false,
  });
}

function initControls() {
  for (const id of ["original", "inverted"]) {
    byId(id).disabled = false;
    byId(id).addEventListener("click", () => {
      mode = id;
      queueDraw();
    });
  }

  byId("reset").disabled = false;
  byId("reset").addEventListener("click", () => {
    viewRevision += 1;
    queueDraw();
  });

  byId("view-toggle").addEventListener("click", () => {
    if (!canUse3D) return;
    is3D = !is3D;
    viewRevision += 1;
    queueDraw();
  });

  byId("exaggeration").addEventListener("input", (event) => {
    byId("factor").value = `${event.target.value}×`;
  });
  byId("exaggeration").addEventListener("change", queueDraw);

  byId("draw-profile").disabled = false;
  byId("draw-profile").addEventListener("click", () => {
    profilePoints.length = 0;
    selectedProfile = null;
    selectingProfile = true;

    if (is3D) {
      is3D = false;
      viewRevision += 1;
    }

    queueDraw();
  });

  byId("clear-profile").addEventListener("click", () => {
    profilePoints.length = 0;
    selectedProfile = null;
    selectingProfile = false;
    queueDraw();
  });

  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver((entries) => {
      for (const { target, contentRect } of entries) {
        if (contentRect.width && contentRect.height && target.data) {
          Plotly.Plots.resize(target).catch(() => {});
        }
      }
    });

    observer.observe(plot);
    observer.observe(profileChart);
  }
}

async function start() {
  try {
    if (!window.Plotly) {
      throw new Error("The bundled Plotly script could not be loaded.");
    }
    if (!profilePanel || !profileChart || !byId("profile-summary")
        || !byId("profile-help") || !byId("draw-profile") || !byId("clear-profile")) {
      throw new Error("Add the cross-section controls and panel to index.html.");
    }

    const response = await fetch("/api/terrain", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`The data endpoint returned HTTP ${response.status}.`);
    }

    grid = await response.json();
    invertedGrid = grid.elevation_m.map((row) =>
      row.map((value) => value === null ? null : -value),
    );

    const meta = grid.metadata;
    byId("region").textContent = `${meta.region} · ${meta.dataset}`;
    byId("deepest").textContent = `${signed(meta.native_deepest_m)} m`;
    byId("sample").textContent =
      `Web grid ${grid.latitude.length} × ${grid.longitude.length} cells. `
      + `Deepest visible sample ${signed(meta.web_deepest_m)} m.`;

    initControls();

    if (hasWebGL()) queueDraw();
    else useFallback("WebGL is unavailable.");
  } catch (error) {
    setStatus(error.message, true);
  }
}

start();