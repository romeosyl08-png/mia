// Apply saved colors before the first paint, including when storage is blocked.
(() => {
  try {
    const saved = JSON.parse(localStorage.getItem("ai2ai-appearance") || "null");
    const root = document.documentElement;
    root.dataset.theme = saved?.theme === "dark" ? "dark" : "light";
    const accents = ["#6C6FCB", "#E8A33D", "#3F8E8A", "#B4483A", "#4E9F3D"];
    const accent = accents.includes(saved?.accent) ? saved.accent : accents[0];
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-ink", accent === "#E8A33D" ? "#211914" : "#FFFFFF");
  } catch {
    // The CSS defaults also work without JavaScript or local storage.
  }
})();
