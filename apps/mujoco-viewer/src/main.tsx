import { createRoot } from "react-dom/client";
import { ProductViewerApp } from "./app/ProductViewerApp.js";
import { WorkbenchApp } from "./app/WorkbenchApp.js";

const mountPoint = typeof document === "undefined" ? null : document.getElementById("app");

if (mountPoint !== null) {
  createRoot(mountPoint).render(new URLSearchParams(location.search).has("workbench") ? <WorkbenchApp /> : <ProductViewerApp />);
}

