import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./theme.css";

try {
  document.documentElement.dataset.theme = localStorage.getItem("aptlake.theme") || "light";
} catch {
  document.documentElement.dataset.theme = "light";
}

const root = document.getElementById("root");
if (!root) throw new Error("index.html 에 #root 가 없습니다");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
