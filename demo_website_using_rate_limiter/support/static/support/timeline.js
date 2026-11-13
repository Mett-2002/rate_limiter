"use strict";

// The API decides quota. This chart visualizes response times observed in this tab.
class RequestTimeline {
  constructor(svg) {
    this.svg = svg;
    this.origin = Date.now();
    this.started = performance.now();
    this.user = "alice";
    this.windowSeconds = 60;
    this.events = [];
    this.policies = new Map();
    this.width = 900;
    this.lastFrame = 0;
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    this.makeChart();
    this.resize = new ResizeObserver(([entry]) => {
      this.width = Math.max(280, entry.contentRect.width);
      this.svg.setAttribute("viewBox", `0 0 ${this.width} 270`);
      this.draw();
    });
    this.resize.observe(svg);
    this.animate = this.animate.bind(this);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.draw();
    });
    requestAnimationFrame(this.animate);
  }

  now() {
    // Keep movement monotonic if the computer's wall clock is adjusted.
    return this.origin + performance.now() - this.started;
  }

  element(name, attributes = {}, parent = this.svg) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    parent.append(node);
    return node;
  }

  text(value, attributes = {}, parent = this.svg) {
    const node = this.element("text", attributes, parent);
    node.textContent = value;
    return node;
  }

  makeChart() {
    this.element("title", {id: "timeline-svg-title"}).textContent = "Live rolling-window timeline";
    this.element("desc", {id: "timeline-svg-description"}).textContent =
      "Time runs left to right. Green circles are accepted requests; red crosses are HTTP 429 rejections. The highlighted interval moves forward with time. Expired accepted points fade; rejected attempts never occupy quota.";
    this.band = this.element("rect", {y: 45, height: 142, rx: 8, class: "timeline-window"});
    this.boundary = this.element("line", {y1: 44, y2: 215, class: "timeline-boundary"});
    this.windowLabel = this.text("", {y: 28, class: "timeline-window-label"});
    this.lanes = [100, 157].map(y => this.element("line", {y1: y, y2: y, class: "timeline-lane"}));
    this.text("Accepted", {x: 0, y: 104, class: "timeline-axis-label"});
    this.text("Rejected", {x: 0, y: 161, class: "timeline-axis-label"});
    this.axis = this.element("line", {y1: 215, y2: 215, class: "timeline-axis"});
    this.ticks = Array.from({length: 16}, () => {
      const group = this.element("g");
      return {group, line: this.element("line", {y1: 211, y2: 220, class: "timeline-tick"}, group),
        label: this.text("", {y: 240, "text-anchor": "middle", class: "timeline-axis-label"}, group)};
    });
    this.points = this.element("g");
    this.cursor = this.element("line", {y1: 40, y2: 217, class: "timeline-now"});
    this.nowLabel = this.text("NOW", {y: 258, "text-anchor": "middle", class: "timeline-now-label"});
    this.draw();
  }

  setUser(user) {
    this.user = user;
    this.windowSeconds = this.policies.get(user) || 60;
    this.rebuildPoints();
    this.draw();
  }

  setQuota(user, quota) {
    if (!Number.isFinite(quota.window_seconds) || quota.window_seconds <= 0) return;
    this.policies.set(user, quota.window_seconds);
    if (user === this.user) {
      this.windowSeconds = quota.window_seconds;
      this.draw();
    }
  }

  record(user, httpStatus) {
    // Validation, CSRF, connection and server errors are not limiter rejections.
    if (httpStatus !== 200 && httpStatus !== 429) return;
    const accepted = httpStatus === 200;
    this.events.push({user, at: this.now(), accepted});
    this.events = this.events.slice(-300);
    this.rebuildPoints();
    this.draw();
    document.getElementById("timeline-announcement").textContent =
      `${user}: ${accepted ? "accepted" : "rate limited (HTTP 429)"}. Timeline updated.`;
  }

  rebuildPoints() {
    this.points.replaceChildren();
    this.markers = this.events.filter(event => event.user === this.user).map((event, index) => {
      const group = this.element("g", {class: "timeline-point", role: "img"}, this.points);
      const label = `${event.accepted ? "Accepted" : "Rejected (HTTP 429)"} at ${new Date(event.at).toLocaleTimeString()}`;
      group.setAttribute("aria-label", label);
      this.element("title", {}, group).textContent = label;
      if (event.accepted) this.element("circle", {r: 6, class: "timeline-accepted"}, group);
      else this.element("path", {d: "M -5 -5 L 5 5 M -5 5 L 5 -5", class: "timeline-rejected"}, group);
      return {event, group, y: (event.accepted ? 100 : 157) + ((index % 3) - 1) * 7};
    });
    const observed = this.markers.map(marker => marker.event);
    document.getElementById("timeline-accepted-count").textContent = observed.filter(event => event.accepted).length;
    document.getElementById("timeline-rejected-count").textContent = observed.filter(event => !event.accepted).length;
    document.getElementById("timeline-empty").hidden = observed.length > 0;
    document.getElementById("timeline-user").textContent = this.user;
  }

  draw() {
    const now = this.now();
    const windowMs = this.windowSeconds * 1000;
    // Initially the window travels across a fixed axis; then the axis follows it.
    const start = Math.max(this.origin - windowMs * 1.15, now - windowMs * 1.6);
    const end = start + windowMs * 2.1;
    const left = 76, right = this.width - 16, span = right - left;
    const x = time => left + (time - start) / (end - start) * span;
    const boundaryX = Math.max(left, x(now - windowMs)), nowX = x(now);
    this.band.setAttribute("x", boundaryX);
    this.band.setAttribute("width", Math.max(0, nowX - boundaryX));
    this.boundary.setAttribute("x1", boundaryX);
    this.boundary.setAttribute("x2", boundaryX);
    this.windowLabel.setAttribute("x", boundaryX);
    this.windowLabel.textContent = `Last ${this.windowSeconds}s`;
    for (const line of [...this.lanes, this.axis]) {
      line.setAttribute("x1", left); line.setAttribute("x2", right);
    }
    this.cursor.setAttribute("x1", nowX); this.cursor.setAttribute("x2", nowX);
    this.nowLabel.setAttribute("x", nowX);
    const stepSeconds = this.width < 550 ? Math.max(30, Math.ceil(this.windowSeconds / 2)) : Math.max(10, Math.ceil(this.windowSeconds / 6));
    const step = stepSeconds * 1000;
    const first = Math.ceil(start / step) * step;
    this.ticks.forEach((tick, index) => {
      const time = first + index * step;
      const position = x(time);
      tick.group.setAttribute("display", time <= end ? "" : "none");
      tick.line.setAttribute("x1", position); tick.line.setAttribute("x2", position);
      tick.label.setAttribute("x", position);
      if (tick.time !== time) {
        tick.time = time;
        tick.label.textContent = new Date(time).toLocaleTimeString([], {minute: "2-digit", second: "2-digit"});
      }
    });
    for (const {event, group, y} of this.markers || []) {
      const position = x(event.at);
      group.setAttribute("display", position >= left && position <= right ? "" : "none");
      group.setAttribute("transform", `translate(${position.toFixed(2)} ${y})`);
      // Interval (now - window, now]: a boundary-old point is already outside.
      group.setAttribute("opacity", event.at > now - windowMs ? "1" : "0.28");
    }
  }

  animate(frame) {
    const interval = this.reducedMotion.matches ? 1000 : 50;
    if (!document.hidden && frame - this.lastFrame >= interval) {
      this.lastFrame = frame;
      this.draw();
    }
    requestAnimationFrame(this.animate);
  }
}
