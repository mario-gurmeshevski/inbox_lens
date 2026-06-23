function applyFilters() {
  const status = document.getElementById("filter-status").value;
  const priority = document.getElementById("filter-priority").value;
  const search = document.getElementById("filter-search").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (priority) params.set("priority", priority);
  if (search) params.set("search", search);
  window.location.href = "/emails?" + params.toString();
}

document
  .getElementById("filter-search")
  .addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyFilters();
  });
