(() => {
  "use strict";

  const one = (selector, root = document) => root.querySelector(selector);
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const menuButton = one("[data-admin-menu-toggle]");
  const nav = one("#school-nav");
  if (menuButton && nav) {
    menuButton.addEventListener("click", () => {
      const open = nav.classList.toggle("school-nav-open");
      menuButton.setAttribute("aria-expanded", String(open));
    });
  }

  all("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    });
  });
  all("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => {
      const dialog = button.closest("dialog");
      if (dialog) dialog.close();
    });
  });
  all("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  all("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  all("[data-toggle-password]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.togglePassword);
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
      button.setAttribute("aria-label", input.type === "password" ? "Покажи паролата" : "Скрий паролата");
    });
  });

  all("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const input = document.getElementById(button.dataset.copy);
      if (!input) return;
      try {
        await navigator.clipboard.writeText(input.value);
        const oldText = button.textContent;
        button.textContent = "Копирано";
        window.setTimeout(() => { button.textContent = oldText; }, 1800);
      } catch (_error) {
        input.select();
        document.execCommand("copy");
      }
    });
  });

  all(".school-color-input").forEach((wrapper) => {
    const color = one('input[type="color"]', wrapper);
    const text = one('input[type="text"]', wrapper);
    if (!color || !text) return;
    color.addEventListener("input", () => { text.value = color.value; });
    text.addEventListener("input", () => {
      if (/^#[0-9a-f]{6}$/i.test(text.value)) color.value = text.value;
    });
  });

  const translations = new Map([
    ["Search", "Търси"], ["Actions", "Действия"], ["Export", "Експорт"],
    ["Import CSV", "Импорт CSV"], ["Delete selected items", "Изтрий избраните"],
    ["Save", "Запази"], ["Cancel", "Отказ"], ["Create", "Създай"],
    ["Edit", "Редакция"], ["Delete", "Изтрий"], ["Details", "Подробности"],
    ["Previous", "Назад"], ["Next", "Напред"], ["Logout", "Изход"],
  ]);
  all("button, a, label, option").forEach((node) => {
    const text = node.textContent.trim();
    if (translations.has(text)) node.textContent = translations.get(text);
    if (text.startsWith("+ New ")) node.textContent = "+ Нов запис";
  });
  all('input[placeholder^="Search:"]').forEach((input) => { input.placeholder = "Търсене…"; });
})();
