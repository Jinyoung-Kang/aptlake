import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./theme.css";
import { applyInitialTheme } from "./hooks/useTheme";

applyInitialTheme();
const root = document.getElementById("root");
if (!root) throw new Error("index.html 에 #root 가 없습니다");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
