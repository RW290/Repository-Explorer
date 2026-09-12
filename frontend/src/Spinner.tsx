interface Props {
  label?: string;
  size?: "sm" | "md";
}

export function Spinner({ label, size = "md" }: Props) {
  return (
    <span className={`spinner spinner--${size}`} role="status" aria-label={label ?? "Loading"}>
      <span className="spinner__ring" aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  );
}