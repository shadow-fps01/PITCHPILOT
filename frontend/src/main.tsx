import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "@/App";
import { AppProvider } from "@/context/AppContext";
import { AuthProvider } from "@/context/AuthContext";

import "@/index.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Root container #root not found in index.html");
}

createRoot(container).render(
  <StrictMode>
    <AuthProvider>
      <AppProvider>
        <App />
      </AppProvider>
    </AuthProvider>
  </StrictMode>,
);
