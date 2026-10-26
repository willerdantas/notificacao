document.addEventListener("DOMContentLoaded", () => {
  const overlay = document.getElementById("page-transition");
  const mainContent = document.querySelector("main");

  const resetOverlay = () => {
    if (overlay) {
      overlay.classList.remove("active");
    }
  };

  resetOverlay();
  window.addEventListener("pageshow", resetOverlay);

  if (mainContent) {
    mainContent.classList.add("page-fade-in");
  }

  if (!overlay) return;

  const showOverlayAndGo = (url) => {
    overlay.classList.add("active");
    setTimeout(() => {
      window.location.href = url;
    }, 380);
  };

  // Intercepta links internos para exibir a animação antes de navegar
  document.querySelectorAll("a[href]").forEach((link) => {
    const href = link.getAttribute("href");

    const isInternal =
      href &&
      !href.startsWith("http") &&
      !href.startsWith("#") &&
      !href.startsWith("mailto:") &&
      link.target !== "_blank";

    if (isInternal) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        showOverlayAndGo(href);
      });
    }
  });

  // Exibe a animação também ao enviar formulários que naveguem de tela
  document.querySelectorAll("form[data-transition='true']").forEach((form) => {
    form.addEventListener("submit", (event) => {
      // Não exibe a animação se a validação da página cancelou o envio.
      if (!event.defaultPrevented) {
        overlay.classList.add("active");
      }
    });
  });
});
