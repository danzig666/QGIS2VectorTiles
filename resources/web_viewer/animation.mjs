// Animated markers (maplibre_converter._animation_frames): one icon layer per
// frame of the marker's image, each with "q2vt:frame" metadata {group, index,
// count, fps, opacity}; the first frame is drawn, the others transparent.
// The animator shows the frames in turn at the marker's frame rate by their
// icon-opacity (a paint property: the map only repaints, it does not lay
// the symbols out again). It owns the icon-opacity of these layers, so the
// layer opacity slider (StyleControl) is applied here. The first frame stays
// when the visitor's system asks for reduced motion and while printing.
import { scaleValue } from "./style_control.mjs";

export class Animator {
  constructor(map, control = null) {
    this.map = map;
    this.control = control;
    this.groups = new Map(); // group -> {fps, count, frames: [{id, opacity}], current}
    for (const def of map.getStyle().layers || []) {
      const frame = def.metadata && def.metadata["q2vt:frame"];
      if (!frame || !(frame.count > 1) || !(frame.fps > 0)) continue;
      let group = this.groups.get(frame.group);
      if (!group) {
        group = { fps: frame.fps, count: frame.count, frames: [], current: 0 };
        this.groups.set(frame.group, group);
      }
      group.frames[frame.index] = { id: def.id, opacity: frame.opacity ?? 1 };
    }
    this.paused = false;
    const query = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    this.reduce = !!(query && query.matches);
    if (query && query.addEventListener) {
      query.addEventListener("change", (event) => {
        this.reduce = event.matches;
        if (this.reduce) this.showAll(0);
        else this.start();
      });
    }
    this.frame = (now) => this.tick(now);
    this.start();
  }

  get available() { return this.groups.size > 0; }

  // The layer opacity slider's factor of a style layer (1 without one).
  factor(id) {
    const applied = this.control && this.control.applied && this.control.applied.get(id);
    return applied && typeof applied.factor === "number" ? applied.factor : 1;
  }

  show(group, index) {
    group.frames.forEach((frame, number) => {
      if (!frame || !this.map.getLayer(frame.id)) return;
      const opacity = number === index ? scaleValue(frame.opacity, this.factor(frame.id)) : 0;
      this.map.setPaintProperty(frame.id, "icon-opacity", opacity);
    });
    group.current = index;
  }

  showAll(index) {
    for (const group of this.groups.values()) this.show(group, Math.min(index, group.count - 1));
  }

  // After the layer opacity changed (StyleControl.apply): the shown frame again.
  refresh() {
    for (const group of this.groups.values()) this.show(group, group.current);
  }

  start() {
    if (!this.available || this.reduce || this.paused || this.running) return;
    this.running = true;
    requestAnimationFrame(this.frame);
  }

  tick(now) {
    if (this.reduce || this.paused) {
      this.running = false;
      return;
    }
    for (const group of this.groups.values()) {
      const index = Math.floor((now / 1000) * group.fps) % group.count;
      if (index !== group.current) this.show(group, index);
    }
    requestAnimationFrame(this.frame);
  }

  // Printing: the first frame, still.
  pause() {
    this.paused = true;
    this.showAll(0);
  }

  resume() {
    this.paused = false;
    this.start();
  }
}
