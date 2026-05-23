(function () {
  const collapseBtn = document.getElementById("sidebarToggleBtn");
  const offcanvasEl = document.getElementById("mobileSidebar");

  if (collapseBtn && offcanvasEl && window.bootstrap && window.bootstrap.Offcanvas) {
    const sidebar = new window.bootstrap.Offcanvas(offcanvasEl);
    collapseBtn.addEventListener("click", function () {
      sidebar.toggle();
    });
  }
})();
