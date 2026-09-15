export type Theme = "light" | "dark";

interface Props {
  theme: Theme;
  onToggle: () => void;
}

export function ThemeToggle({ theme, onToggle }: Props) {
  const nextTheme = theme === "light" ? "dark" : "light";
  return (
    <button
      className="theme-toggle"
      type="button"
      onClick={onToggle}
      aria-label={`Switch to ${nextTheme} mode`}
      aria-pressed={theme === "dark"}
      title={`Switch to ${nextTheme} mode`}
    >
      <span className="theme-toggle__icon" aria-hidden="true">{theme === "light" ? "☾" : "☀"}</span>
      <span className="theme-toggle__label">{theme === "light" ? "Dark" : "Light"}</span>
    </button>
  );
}