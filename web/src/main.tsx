import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";

// Dark theme by default (rakazo tokens key off data-theme); ?theme=light flips it.
const theme = new URLSearchParams(window.location.search).get("theme") === "light" ? "light" : "dark";
document.documentElement.setAttribute("data-theme", theme);
document.title = "Airlock";
// The narration is the product; keep browsers from machine-translating it.
document.documentElement.setAttribute("translate", "no");
const noTranslate = document.createElement("meta");
noTranslate.name = "google";
noTranslate.content = "notranslate";
document.head.appendChild(noTranslate);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
