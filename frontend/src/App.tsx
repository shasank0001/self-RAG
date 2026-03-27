import { Show, SignInButton, SignUpButton, UserButton } from "@clerk/react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useChatStore } from "@/store/chatStore";

const navItems = [
  { path: "/", label: "Bins" },
  { path: "/history", label: "History" },
  { path: "/settings", label: "Settings" },
];

export default function App() {
  const { pathname } = useLocation();
  const isChatRoute = pathname.startsWith("/chat");
  const activeSessionId = useChatStore((state) => state.activeSessionId);
  const chatPath = activeSessionId ? `/chat/${activeSessionId}` : "/chat";

  return (
    <div className={isChatRoute ? "app-shell is-chat-route" : "app-shell"}>
      <header className="topbar">
        <h1>Self-RAG Atlas</h1>
        <nav>
          {navItems.slice(0, 1).map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => (isActive ? "active" : "")}
              end={item.path === "/"}
            >
              {item.label}
            </NavLink>
          ))}
          <NavLink to={chatPath} className={({ isActive }) => (isActive ? "active" : "")}>
            Chat
          </NavLink>
          {navItems.slice(1).map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => (isActive ? "active" : "")}
              end={item.path === "/"}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="auth-cta">
          <Show when="signed-out">
            <SignInButton />
            <SignUpButton />
          </Show>
          <Show when="signed-in">
            <UserButton />
          </Show>
        </div>
      </header>
      <main className={isChatRoute ? "content is-chat-route" : "content"}>
        <Outlet />
      </main>
    </div>
  );
}
