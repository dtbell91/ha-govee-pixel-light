/*
 * Govee Pixel Light playlist card: what the light is showing, skip / hold / jump to an item, and edit the
 * playlist (switch items on and off, reorder, durations, the live weather pages and their timing, any item's
 * options). Served and registered by the govee_pixel integration; add it to a dashboard as
 *
 *   type: custom:govee-pixel-playlist-card
 *   entity: sensor.<light>_playlist_status
 */
const DOMAIN = "govee_pixel";

const TYPE_ICONS = {
  image: "mdi:image", weather: "mdi:weather-partly-cloudy", text: "mdi:format-text", animation: "mdi:animation-play",
  clock: "mdi:clock-outline", calendar: "mdi:calendar", weather_live: "mdi:weather-partly-rainy",
  forecast_live: "mdi:calendar-week", now_playing: "mdi:music",
};
const TYPES = Object.keys(TYPE_ICONS);

const WEATHER_PAGES = {
  temperature: "Temperature", warnings: "Warnings", high_low: "High / low", feels_like: "Feels like",
  rain: "Rain", wind: "Wind", uv: "UV index", fire: "Fire danger", sun: "Sunrise / sunset",
};
const DEFAULT_PAGE_SECONDS = 4, DEFAULT_TEMP_SECONDS = 6, DEFAULT_TRANSITION = 0.35;

const NEW_ITEM_OPTIONS = {
  image: { image_path: "/config/www/govee/picture.gif" },
  weather: { weather_entity: "weather.home" },
  text: { message: "Hello!" },
  animation: { animation: "heart" },
  clock: {},
  calendar: { calendar_entity: "calendar.family" },
  weather_live: { weather_entity: "weather.home" },
  forecast_live: { weather_entity: "weather.home" },
  now_playing: { this_room_only: true },
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (o) => JSON.parse(JSON.stringify(o ?? {}));

class GoveePixelPlaylistCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._open = null; // name of the item being edited
    this._draft = null; // its edited copy
    this._adding = null; // new item form
    this._busy = false;
    this._listSig = null;
  }

  setConfig(config) {
    if (!config || !config.entity) throw new Error("Set entity: the light's playlist status sensor");
    this._config = config;
    this._listSig = null;
  }

  static getStubConfig(hass) {
    const e = Object.values(hass.entities || {}).find(
      (x) => x.platform === DOMAIN && x.entity_id.startsWith("sensor.") && x.translation_key === "playlist_status");
    return { entity: e ? e.entity_id : "sensor.pixel_light_playlist_status" };
  }

  getCardSize() { return 6; }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    if (st === this._state && this.shadowRoot.childElementCount) return;
    this._state = st;
    this._render();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._tick(), 500);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  // ---------- data ----------
  get _attrs() { return (this._state && this._state.attributes) || {}; }
  get _items() { return this._attrs.items || []; }

  get _deviceId() {
    const e = this._hass.entities && this._hass.entities[this._config.entity];
    return e && e.device_id;
  }

  _deviceEntity(domain, key) {
    const id = this._deviceId;
    const e = Object.values(this._hass.entities || {}).find(
      (x) => x.device_id === id && x.platform === DOMAIN && x.entity_id.startsWith(domain + ".") && x.translation_key === key);
    return e && e.entity_id;
  }

  async _call(service, data = {}) {
    this._busy = true;
    this._tick();
    try {
      await this._hass.callService(DOMAIN, service, { device_id: this._deviceId, ...data });
      return true;
    } catch (err) {
      this._toast(err.message || String(err));
      return false;
    } finally {
      this._busy = false;
      this._tick();
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  // ---------- rendering ----------
  _render() {
    const root = this.shadowRoot;
    if (!root.childElementCount) {
      root.innerHTML = `<style>${STYLE}</style><ha-card><div class="head"></div><div class="list"></div><div class="add"></div></ha-card>`;
      root.addEventListener("click", (ev) => this._onClick(ev));
      root.addEventListener("change", (ev) => this._onInput(ev, true));
      root.addEventListener("input", (ev) => this._onInput(ev, false));
    }
    if (!this._state) {
      root.querySelector(".head").innerHTML = `<div class="warn">Entity ${esc(this._config.entity)} not found</div>`;
      return;
    }
    this._renderHead();
    // the list is redrawn only when the items change (not as the light moves on), so editing isn't disturbed
    const sig = JSON.stringify([this._items, this._attrs.rotation, this._open, this._draft === null, this._adding === null]);
    if (sig !== this._listSig) {
      this._listSig = sig;
      this._renderList();
      this._renderAdd();
    }
    this._tick();
  }

  _renderHead() {
    const a = this._attrs;
    const name = this._config.title || (this._state.attributes.friendly_name || "").replace(/ playlist status$/i, "") || "Playlist";
    const status = this._hass.formatEntityState ? this._hass.formatEntityState(this._state) : this._state.state;
    const playSwitch = this._deviceEntity("switch", "playlist");
    const on = playSwitch && this._hass.states[playSwitch] && this._hass.states[playSwitch].state === "on";
    const controls = a.playback_controls;
    const paused = ["interrupted", "paused"].includes(this._state.state);
    this.shadowRoot.querySelector(".head").innerHTML = `
      <div class="title">
        <span class="name">${esc(name)}</span>
        <span class="chip ${esc(this._state.state)}">${esc(status)}</span>
        ${playSwitch ? `<button class="icon" data-act="power" title="${on ? "Stop the playlist" : "Start the playlist"}">
          <ha-icon icon="${on ? "mdi:stop-circle-outline" : "mdi:play-circle-outline"}"></ha-icon></button>` : ""}
      </div>
      <div class="now">
        <div class="now-name">${a.current_item ? esc(a.current_item) : on ? (paused ? "Interrupted" : "&nbsp;") : "Stopped"}</div>
        <div class="bar"><div class="fill"></div></div>
      </div>
      ${controls ? `<div class="controls">
        <button class="icon" data-act="prev" title="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
        <button class="icon ${a.held ? "active" : ""}" data-act="hold" title="${a.held ? "Carry on rotating" : "Stay on this item"}">
          <ha-icon icon="${a.held ? "mdi:play" : "mdi:pause"}"></ha-icon></button>
        <button class="icon" data-act="next" title="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
        ${paused ? `<button class="text" data-act="resume">Resume</button>` : ""}
      </div>` : paused ? `<div class="controls"><button class="text" data-act="resume">Resume</button></div>` : ""}
      ${a.last_error ? `<div class="warn">${esc(a.last_error)}</div>` : ""}`;
  }

  // progress bar, the current item's highlight and the busy state: updated without redrawing
  _tick() {
    const root = this.shadowRoot;
    if (!this._state || !root.childElementCount) return;
    const a = this._attrs;
    const fill = root.querySelector(".fill");
    if (fill) {
      const item = this._items.find((i) => i.name === a.current_item);
      let p = a.held ? 1 : 0;
      if (a.item_ends && item && !a.held) {
        const left = (Date.parse(a.item_ends) - Date.now()) / 1000;
        p = Math.max(0, Math.min(1, 1 - left / item.duration));
      }
      fill.style.width = `${(p * 100).toFixed(1)}%`;
      fill.classList.toggle("held", !!a.held);
    }
    root.querySelectorAll(".item").forEach((el) => el.classList.toggle("current", el.dataset.name === a.current_item));
    root.querySelector("ha-card").classList.toggle("busy", this._busy);
  }

  _renderList() {
    const rotation = this._attrs.rotation || [];
    const controls = this._attrs.playback_controls;
    const items = this._items;
    const html = items.map((it, i) => {
      const open = this._open === it.name;
      const inRotation = rotation.includes(it.name);
      const why = !it.enabled ? "Off" : !inRotation ? "Nothing to show now" : "";
      return `<div class="item ${it.enabled ? "" : "off"} ${open ? "open" : ""}" data-name="${esc(it.name)}">
        <div class="row">
          <ha-icon class="type" icon="${TYPE_ICONS[it.type] || "mdi:help"}"></ha-icon>
          <div class="label" data-act="toggle-edit" data-i="${i}">
            <div class="iname">${esc(it.name)}</div>
            <div class="sub">${esc(it.type)} · ${esc(it.duration)} s${it.show_if ? " · conditional" : ""}${why ? ` · ${why}` : ""}</div>
          </div>
          ${controls && inRotation ? `<button class="icon" data-act="show" data-i="${i}" title="Show now"><ha-icon icon="mdi:play"></ha-icon></button>` : ""}
          <button class="icon" data-act="up" data-i="${i}" title="Move up" ${i === 0 ? "disabled" : ""}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
          <button class="icon" data-act="down" data-i="${i}" title="Move down" ${i === items.length - 1 ? "disabled" : ""}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
          <label class="switch" title="${it.enabled ? "In the rotation" : "Left out"}">
            <input type="checkbox" data-act="enable" data-i="${i}" ${it.enabled ? "checked" : ""}><span></span></label>
        </div>
        ${open && this._draft ? this._editor(this._draft) : ""}
      </div>`;
    }).join("");
    this.shadowRoot.querySelector(".list").innerHTML = html || `<div class="empty">The playlist is empty.</div>`;
  }

  _editor(d) {
    const o = d.options;
    let specific = "";
    if (d.type === "weather_live") {
      const order = o.pages ? [...o.pages, ...Object.keys(WEATHER_PAGES).filter((p) => !o.pages.includes(p))]
        : Object.keys(WEATHER_PAGES);
      const on = (p) => !o.pages || o.pages.includes(p);
      specific = `
        <div class="sect">Pages under the sky <span class="hint">shown in this order, each only when there is data for it</span></div>
        <div class="pages">${order.map((p, i) => `
          <div class="page ${on(p) ? "" : "off"}">
            <label><input type="checkbox" data-page="${p}" ${on(p) ? "checked" : ""}> ${WEATHER_PAGES[p]}</label>
            <button class="icon small" data-act="page-up" data-page="${p}" ${i === 0 ? "disabled" : ""}><ha-icon icon="mdi:chevron-up"></ha-icon></button>
            <button class="icon small" data-act="page-down" data-page="${p}" ${i === order.length - 1 ? "disabled" : ""}><ha-icon icon="mdi:chevron-down"></ha-icon></button>
          </div>`).join("")}</div>
        ${this._slider("temp_seconds", "Temperature page", o.temp_seconds ?? DEFAULT_TEMP_SECONDS, 1, 30, 0.5, "s")}
        ${this._slider("page_seconds", "Other pages", o.page_seconds ?? DEFAULT_PAGE_SECONDS, 1, 30, 0.5, "s")}
        ${this._slider("transition", "Slide between pages", o.transition ?? DEFAULT_TRANSITION, 0, 2, 0.05, "s")}
        <label class="check"><input type="checkbox" data-opt="restart_pages" ${o.restart_pages ? "checked" : ""}>
          Start from the first page every time <span class="hint">(otherwise it carries on where it left off)</span></label>
        ${this._pageTotal(d)}`;
    } else if (d.type === "forecast_live") {
      specific = this._slider("page_seconds", "Highs and lows, then days", o.page_seconds ?? DEFAULT_PAGE_SECONDS, 1, 30, 0.5, "s");
    } else if (d.type === "clock") {
      specific = `
        <label class="check"><input type="checkbox" data-opt="weather_background" ${o.weather_background !== false ? "checked" : ""}>
          Live weather behind the time <span class="hint">(from the playlist's live weather item, or weather_entity)</span></label>
        <label class="check"><input type="checkbox" data-opt="hour12" ${o.hour12 ? "checked" : ""}> 12-hour</label>
        <label class="check"><input type="checkbox" data-opt="seconds" ${o.seconds ? "checked" : ""}> Seconds bar</label>
        <label class="field">Date <select data-opt="date">
          ${["weekday_day", "day_month", "month_day", "none"].map((v) => `<option ${(o.date || "weekday_day") === v ? "selected" : ""}>${v}</option>`).join("")}
        </select></label>`;
    } else if (d.type === "text") {
      specific = `<label class="field">Message <input type="text" data-opt="message" value="${esc(o.message)}"></label>`;
    }
    return `<div class="editor">
      <label class="field">Name <input type="text" data-draft="name" value="${esc(d.name)}"></label>
      ${this._slider("duration", "Time on screen", d.duration, 1, 300, 1, "s", "draft")}
      ${specific}
      <label class="field">Show only if <span class="hint">(template)</span>
        <input type="text" data-draft="show_if" value="${esc(d.show_if || "")}" placeholder="{{ is_state('sun.sun', 'above_horizon') }}"></label>
      <details><summary>All options (JSON)</summary>
        <textarea data-draft="json" rows="6" spellcheck="false">${esc(JSON.stringify(o, null, 2))}</textarea></details>
      <div class="buttons">
        <button class="text danger" data-act="delete">Remove</button>
        <span class="grow"></span>
        <button class="text" data-act="cancel">Cancel</button>
        <button class="text primary" data-act="save">Save</button>
      </div>
    </div>`;
  }

  _slider(key, label, value, min, max, step, unit, where = "opt") {
    max = Math.max(max, Number(value) || 0);
    return `<label class="field slider">${esc(label)}
      <input type="range" data-${where}="${key}" min="${min}" max="${max}" step="${step}" value="${value}">
      <input type="number" class="num" data-${where}="${key}" min="${min}" max="${max}" step="${step}" value="${value}"><span class="unit">${unit}</span></label>`;
  }

  // how long the pages take to go round, against the item's time on screen
  _pageTotal(d) {
    const o = d.options;
    const pages = o.pages || Object.keys(WEATHER_PAGES);
    const total = pages.reduce((t, p) => t + (p === "temperature" ? (o.temp_seconds ?? DEFAULT_TEMP_SECONDS) : (o.page_seconds ?? DEFAULT_PAGE_SECONDS)), 0);
    const turns = Math.ceil(total / d.duration);
    return `<div class="hint total">All ${pages.length} pages take up to ${total.toFixed(1)} s${turns > 1 && !o.restart_pages
      ? `: about ${turns} turns in the rotation` : turns > 1 ? `: the last ones won't be reached in ${d.duration} s` : ""}.</div>`;
  }

  _renderAdd() {
    const el = this.shadowRoot.querySelector(".add");
    if (!this._adding) {
      el.innerHTML = `<button class="text" data-act="add-open"><ha-icon icon="mdi:plus"></ha-icon> Add item</button>`;
      return;
    }
    const n = this._adding;
    el.innerHTML = `<div class="editor">
      <label class="field">Name <input type="text" data-new="name" value="${esc(n.name)}"></label>
      <label class="field">Type <select data-new="type">${TYPES.map((t) => `<option ${t === n.type ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      ${this._slider("duration", "Time on screen", n.duration, 1, 300, 1, "s", "new")}
      <label class="field">Options (JSON)<textarea data-new="json" rows="4" spellcheck="false">${esc(n.json)}</textarea></label>
      <div class="buttons"><span class="grow"></span>
        <button class="text" data-act="add-cancel">Cancel</button>
        <button class="text primary" data-act="add-save">Add</button></div>
    </div>`;
  }

  // ---------- events ----------
  async _onClick(ev) {
    const btn = ev.target.closest("[data-act]");
    if (!btn || btn.disabled || btn.tagName === "INPUT") return;
    const act = btn.dataset.act;
    const item = btn.dataset.i !== undefined ? this._items[Number(btn.dataset.i)] : null;
    switch (act) {
      case "power": {
        const sw = this._deviceEntity("switch", "playlist");
        const on = this._hass.states[sw].state === "on";
        await this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: sw }).catch((e) => this._toast(e.message));
        break;
      }
      case "prev": return this._call("playlist_previous");
      case "next": return this._call("playlist_next");
      case "hold": return this._call("playlist_hold", { hold: !this._attrs.held });
      case "resume": return this._call("playlist_resume");
      case "show": return this._call("playlist_show_item", { name: item.name });
      case "up":
      case "down": {
        const i = Number(btn.dataset.i);
        return this._call("playlist_update", { name: item.name, position: act === "up" ? i - 1 : i + 1 });
      }
      case "toggle-edit":
        if (this._open === item.name) {
          this._open = this._draft = null;
        } else {
          this._open = item.name;
          this._draft = { name: item.name, type: item.type, duration: item.duration, show_if: item.show_if || "", options: clone(item.options) };
        }
        return this._render();
      case "page-up":
      case "page-down": {
        const o = this._draft.options;
        const order = o.pages ? [...o.pages, ...Object.keys(WEATHER_PAGES).filter((p) => !o.pages.includes(p))] : Object.keys(WEATHER_PAGES);
        const enabled = new Set(o.pages || order);
        const i = order.indexOf(btn.dataset.page);
        const j = act === "page-up" ? i - 1 : i + 1;
        [order[i], order[j]] = [order[j], order[i]];
        o.pages = order.filter((p) => enabled.has(p));
        return this._redrawEditor();
      }
      case "cancel":
        this._open = this._draft = null;
        return this._render();
      case "save": return this._save();
      case "delete":
        if (confirm(`Remove "${this._open}" from the playlist?`)) {
          if (await this._call("playlist_remove", { name: this._open })) {
            this._open = this._draft = null;
            this._render();
          }
        }
        return;
      case "add-open":
        this._adding = { name: "", type: "text", duration: 30, json: JSON.stringify(NEW_ITEM_OPTIONS.text, null, 2) };
        return this._render();
      case "add-cancel":
        this._adding = null;
        return this._render();
      case "add-save": return this._add();
    }
  }

  _onInput(ev, committed) {
    const t = ev.target;
    const val = t.type === "checkbox" ? t.checked : t.type === "range" || t.type === "number" ? Number(t.value) : t.value;
    if (t.dataset.act === "enable" && committed) {
      const item = this._items[Number(t.dataset.i)];
      this._call("playlist_update", { name: item.name, enabled: t.checked });
      return;
    }
    if (this._adding && t.dataset.new) {
      const n = this._adding;
      if (t.dataset.new === "type") {
        n.type = val;
        n.json = JSON.stringify(NEW_ITEM_OPTIONS[val] || {}, null, 2);
        this._renderAdd();
      } else if (t.dataset.new === "json") n.json = val;
      else n[t.dataset.new] = val;
      this._syncPair(t);
      return;
    }
    const d = this._draft;
    if (!d) return;
    if (t.dataset.page) {
      const all = Object.keys(WEATHER_PAGES);
      const order = d.options.pages ? [...d.options.pages, ...all.filter((p) => !d.options.pages.includes(p))] : all;
      const on = new Set(d.options.pages || all);
      if (t.checked) on.add(t.dataset.page); else on.delete(t.dataset.page);
      d.options.pages = order.filter((p) => on.has(p));
      if (d.options.pages.length === all.length && order.every((p, i) => p === all[i])) delete d.options.pages;
      if (committed) this._redrawEditor();
      return;
    }
    if (t.dataset.opt) {
      d.options[t.dataset.opt] = val;
      this._syncPair(t);
      if (committed) this._redrawEditor();
      return;
    }
    if (t.dataset.draft === "json") {
      try {
        d.options = JSON.parse(val);
        t.classList.remove("bad");
      } catch (e) {
        t.classList.add("bad");
      }
      return;
    }
    if (t.dataset.draft) {
      d[t.dataset.draft] = val;
      this._syncPair(t);
      if (committed && t.dataset.draft === "duration" && d.type === "weather_live") this._redrawEditor();
    }
  }

  // a slider and its number box show the same value
  _syncPair(t) {
    const key = t.dataset.opt || t.dataset.draft || t.dataset.new;
    const attr = t.dataset.opt ? "data-opt" : t.dataset.draft ? "data-draft" : "data-new";
    t.closest(".field")?.querySelectorAll(`[${attr}="${key}"]`).forEach((o) => { if (o !== t) o.value = t.value; });
  }

  _redrawEditor() {
    const el = this.shadowRoot.querySelector(".item.open .editor");
    if (!el) return;
    const open = el.querySelector("details")?.open;
    el.outerHTML = this._editor(this._draft);
    if (open) this.shadowRoot.querySelector(".item.open details").open = true;
  }

  async _save() {
    const d = this._draft;
    const item = this._items.find((i) => i.name === this._open);
    if (!item) return;
    if (this.shadowRoot.querySelector("textarea.bad")) return this._toast("The options aren't valid JSON");
    const data = { name: item.name, options: d.options, duration: Math.round(d.duration), show_if: d.show_if || null };
    if (d.name && d.name !== item.name) data.new_name = d.name;
    if (await this._call("playlist_update", data)) {
      this._open = this._draft = null;
      this._render();
    }
  }

  async _add() {
    const n = this._adding;
    let options;
    try {
      options = JSON.parse(n.json || "{}");
    } catch (e) {
      return this._toast("The options aren't valid JSON");
    }
    if (!n.name) return this._toast("Give the item a name");
    if (await this._call("playlist_add", { name: n.name, item_type: n.type, options, duration: Math.round(n.duration) })) {
      this._adding = null;
      this._render();
    }
  }
}

const STYLE = `
  ha-card { padding: 12px 16px 12px; transition: opacity .2s; }
  ha-card.busy { opacity: .7; }
  .title { display: flex; align-items: center; gap: 8px; }
  .name { font-size: 1.15em; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip { font-size: .75em; padding: 2px 8px; border-radius: 10px; background: var(--secondary-background-color); color: var(--secondary-text-color); white-space: nowrap; }
  .chip.rotating { background: rgba(var(--rgb-primary-color, 3,169,244), .15); color: var(--primary-color); }
  .chip.error { background: rgba(var(--rgb-error-color, 219,68,55), .15); color: var(--error-color); }
  .now { margin: 10px 0 4px; }
  .now-name { font-size: 1.05em; min-height: 1.3em; }
  .bar { height: 4px; border-radius: 2px; background: var(--divider-color); overflow: hidden; margin-top: 6px; }
  .fill { height: 100%; width: 0; background: var(--primary-color); transition: width .5s linear; }
  .fill.held { background: var(--secondary-text-color); }
  .controls { display: flex; justify-content: center; align-items: center; gap: 12px; margin: 6px 0 4px; }
  .controls .icon { --mdc-icon-size: 28px; width: 44px; height: 44px; }
  .controls .icon.active { color: var(--primary-color); }
  .warn { color: var(--error-color); font-size: .85em; margin-top: 6px; }
  button { font: inherit; color: inherit; background: none; border: none; cursor: pointer; }
  button:disabled { opacity: .3; cursor: default; }
  .icon { width: 36px; height: 36px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; color: var(--secondary-text-color); flex: none; }
  .icon:hover:not(:disabled) { background: var(--secondary-background-color); }
  .icon.small { width: 26px; height: 26px; --mdc-icon-size: 18px; }
  .text { padding: 6px 12px; border-radius: 6px; color: var(--primary-color); font-weight: 500; display: inline-flex; align-items: center; gap: 4px; }
  .text:hover { background: var(--secondary-background-color); }
  .text.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .text.danger { color: var(--error-color); }
  .list { margin-top: 8px; border-top: 1px solid var(--divider-color); }
  .item { border-bottom: 1px solid var(--divider-color); }
  .item.current { background: rgba(var(--rgb-primary-color, 3,169,244), .08); }
  .item.current .iname { color: var(--primary-color); font-weight: 500; }
  .item.off .row > :not(.switch) { opacity: .45; }
  .row { display: flex; align-items: center; gap: 4px; padding: 4px 0; }
  .type { color: var(--secondary-text-color); margin: 0 6px 0 2px; flex: none; }
  .label { flex: 1; min-width: 0; cursor: pointer; }
  .iname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { font-size: .8em; color: var(--secondary-text-color); }
  .empty { padding: 16px 0; color: var(--secondary-text-color); text-align: center; }
  .switch { position: relative; width: 36px; height: 20px; flex: none; margin-left: 4px; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch span { position: absolute; inset: 0; border-radius: 10px; background: var(--disabled-text-color, #bbb); transition: .2s; cursor: pointer; }
  .switch span::before { content: ""; position: absolute; width: 16px; height: 16px; left: 2px; top: 2px; border-radius: 50%; background: #fff; transition: .2s; }
  .switch input:checked + span { background: var(--primary-color); }
  .switch input:checked + span::before { transform: translateX(16px); }
  .editor { padding: 6px 4px 12px 36px; display: flex; flex-direction: column; gap: 8px; }
  .add .editor { padding-left: 0; }
  .add { margin-top: 8px; }
  .field { display: flex; flex-direction: column; gap: 4px; font-size: .9em; color: var(--secondary-text-color); }
  .field.slider { flex-direction: row; flex-wrap: wrap; align-items: center; }
  .field.slider input[type=range] { flex: 1; min-width: 120px; }
  .num { width: 64px; }
  .unit { width: 1em; }
  input[type=text], input[type=number], select, textarea { font: inherit; color: var(--primary-text-color); background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 4px; padding: 6px 8px; box-sizing: border-box; }
  input[type=range] { accent-color: var(--primary-color); }
  textarea { font-family: var(--code-font-family, monospace); font-size: .85em; width: 100%; resize: vertical; }
  textarea.bad { border-color: var(--error-color); }
  .check { font-size: .9em; display: flex; align-items: center; gap: 6px; }
  .sect { font-size: .9em; font-weight: 500; margin-top: 4px; }
  .hint { font-size: .85em; color: var(--secondary-text-color); font-weight: normal; }
  .pages { display: flex; flex-direction: column; border: 1px solid var(--divider-color); border-radius: 6px; padding: 2px 6px; }
  .page { display: flex; align-items: center; font-size: .9em; }
  .page label { flex: 1; display: flex; align-items: center; gap: 6px; padding: 2px 0; }
  .page.off label { color: var(--secondary-text-color); }
  details summary { cursor: pointer; font-size: .9em; color: var(--secondary-text-color); }
  .buttons { display: flex; gap: 6px; align-items: center; }
  .grow { flex: 1; }
`;

customElements.define("govee-pixel-playlist-card", GoveePixelPlaylistCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "govee-pixel-playlist-card",
  name: "Govee Pixel playlist",
  description: "What the pixel light is showing; skip, hold or pick an item; edit the playlist and the weather pages.",
  preview: false,
});
