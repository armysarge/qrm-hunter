/** Wheel listener that may call preventDefault (Svelte's onwheel attribute is passive). */
export function wheel(node: HTMLElement, handler: (e: WheelEvent) => void) {
  let current = handler;
  const fn = (e: WheelEvent) => {
    e.preventDefault();
    current(e);
  };
  node.addEventListener("wheel", fn, { passive: false });
  return {
    update(next: (e: WheelEvent) => void) {
      current = next;
    },
    destroy() {
      node.removeEventListener("wheel", fn);
    },
  };
}
export function focusOnMount(node: HTMLElement) {
  queueMicrotask(() => node.focus());
}
