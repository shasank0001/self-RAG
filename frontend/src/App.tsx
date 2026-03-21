import { Show, SignInButton, SignUpButton, UserButton } from "@clerk/react";
import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  { path: "/", label: "Bins" },
  { path: "/chat", label: "Chat" },
  { path: "/history", label: "History" },
  { path: "/settings", label: "Settings" },
];

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>Self-RAG Atlas</h1>
        <nav>
          {navItems.map((item) => (
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
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
