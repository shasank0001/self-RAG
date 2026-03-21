import { createBrowserRouter } from "react-router-dom";

import App from "./App";
import { RequireAuth } from "./lib/auth/RequireAuth";
import { BinListPage } from "./pages/BinListPage";
import { ChatPage } from "./pages/ChatPage";
import { HistoryPage } from "./pages/HistoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignInPage } from "./pages/SignInPage";
import { SignUpPage } from "./pages/SignUpPage";

export const router = createBrowserRouter([
  {
    path: "/sign-in",
    element: <SignInPage />,
  },
  {
    path: "/sign-up",
    element: <SignUpPage />,
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <App />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <BinListPage /> },
      { path: "chat", element: <ChatPage /> },
      { path: "chat/:sessionId", element: <ChatPage /> },
      { path: "history", element: <HistoryPage /> },
      { path: "history/:sessionId", element: <HistoryPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
