(() => {
  const search = document.getElementById("tariffsSearch");
  const rows = [...document.querySelectorAll("#tariffsTableBody tr[data-search]")];
  const count = document.getElementById("tariffsResultCount");
  const noResults = document.getElementById("tariffsNoResults");
  if (!search || !count || !noResults) return;

  const updateResults = () => {
    const query = search.value.trim().toLocaleLowerCase();
    let visibleRows = 0;
    rows.forEach(row => {
      const matches = row.dataset.search.includes(query);
      row.hidden = !matches;
      if (matches) visibleRows += 1;
    });
    count.textContent = `${visibleRows} modèle${visibleRows === 1 ? "" : "s"}`;
    noResults.hidden = visibleRows > 0;
  };

  search.addEventListener("input", updateResults);
})();
