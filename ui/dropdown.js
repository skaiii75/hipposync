export function enhanceSelect(select) {
  const wrapper = document.createElement("div");
  wrapper.className = "dropdown";
  wrapper.dataset.select = select.id;
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.id = `${select.id}-trigger`;
  trigger.className = "dropdown-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const label = select.getAttribute("aria-label") || select.labels?.[0]?.textContent || "";
  for (const node of [...(select.labels || [])]) node.htmlFor = trigger.id;
  const caption = document.createElement("span");
  caption.id = `${select.id}-value`;
  const labelText = document.createElement("span");
  labelText.id = `${select.id}-label`;
  labelText.className = "visually-hidden";
  labelText.textContent = label;
  trigger.setAttribute("aria-labelledby", `${labelText.id} ${caption.id}`);
  trigger.append(labelText, caption);

  const list = document.createElement("div");
  list.className = "dropdown-list";
  list.id = `${select.id}-list`;
  list.hidden = true;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-labelledby", labelText.id);
  trigger.setAttribute("aria-controls", list.id);
  select.before(wrapper);
  wrapper.append(select, trigger, list);
  select.hidden = true;

  const isOpen = () => !list.hidden;
  function close(restore = false) {
    list.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restore) trigger.focus();
  }

  function update() {
    caption.textContent = select.selectedOptions[0]?.label || "Choisir";
    [...list.children].forEach((option, index) => {
      option.setAttribute("aria-selected", String(index === select.selectedIndex));
    });
  }

  function open(last = false) {
    list.replaceChildren(...[...select.options].map((option, index) => {
      const item = document.createElement("div");
      item.className = "dropdown-option";
      item.setAttribute("role", "option");
      item.tabIndex = -1;
      item.textContent = option.label;
      item.addEventListener("click", () => {
        select.selectedIndex = index;
        update();
        close(true);
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return item;
    }));
    update();
    list.hidden = false;
    const rect = trigger.getBoundingClientRect();
    wrapper.classList.toggle("opens-up", window.innerHeight - rect.bottom < list.offsetHeight && rect.top > window.innerHeight - rect.bottom);
    trigger.setAttribute("aria-expanded", "true");
    (list.children[select.selectedIndex] || (last ? list.lastElementChild : list.firstElementChild))?.focus();
  }

  trigger.addEventListener("click", () => isOpen() ? close() : open());
  select.addEventListener("change", update);
  wrapper.addEventListener("focusout", (event) => {
    if (!wrapper.contains(event.relatedTarget)) close();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!wrapper.contains(event.target) && isOpen()) close();
  });
  wrapper.addEventListener("keydown", (event) => {
    const key = event.key;
    if (!isOpen()) {
      if (key === "ArrowDown" || key === "ArrowUp") {
        event.preventDefault();
        open(key === "ArrowUp");
      }
      return;
    }
    const options = [...list.children];
    const index = options.indexOf(document.activeElement);
    if (key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    } else if (key === "Tab") {
      close(true);
    } else if (key === "Enter" || key === " ") {
      if (index >= 0) {
        event.preventDefault();
        options[index].click();
      }
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) {
      event.preventDefault();
      const next = key === "Home" ? 0 : key === "End" ? options.length - 1
        : (index + (key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[next]?.focus();
    }
  });
  update();
  return update;
}
