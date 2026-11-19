// Samostatný modul Lovelace. Čte stavy předané Home Assistantem a při výběru
// zařízení přepne místní podrobnosti. Nevolá služby, neukládá volbu a nekontaktuje API.
const CARD_TAG = 'remeha-heating-schematic-card';
const ID_PATTERN = /^[a-z_]+\.[a-z0-9_]+$/;
const ROLES = [
  'thermal', 'online', 'power', 'backup_power', 'hot_water', 'water_heater',
  'climate', 'room', 'outdoor', 'pressure', 'slope', 'base',
];
const DEVICES = [
  { id: 'pump', icon: 'mdi:fan', details: ['outdoor', 'pressure', 'backup'] },
  { id: 'water', icon: 'mdi:water', details: ['waterTarget', 'waterProgram'] },
  { id: 'house', icon: 'mdi:home-outline', details: ['roomTarget', 'slope', 'base'] },
];
const LABELS = {
  cs: {
    heading: 'Schéma vytápění', pump: 'Tepelné čerpadlo', water: 'Teplá voda', house: 'Dům',
    pumpInfo: 'El. příkon', waterInfo: 'Zásobník TUV', houseInfo: 'Pokojová teplota',
    outdoor: 'Venku', pressure: 'Tlak vody', backup: 'Elektrokotel',
    waterTarget: 'Požadavek TUV', waterProgram: 'Program TUV', roomTarget: 'Cíl vytápění',
    slope: 'Sklon křivky', base: 'Základ křivky', source: 'Podle režimu Remehy',
    heating: 'Vytápění domu', dhw: 'Ohřev teplé vody', offline: 'Remeha je offline',
    unknown: 'Režim nedostupný', idle: 'V klidu', other: 'Režim',
    heat_pump: 'Tepelné čerpadlo', performance: 'Výkon', eco: 'Úsporný', thermometers: 'Teploměry',
  },
  en: {
    heading: 'Heating diagram', pump: 'Heat pump', water: 'Hot water', house: 'Home',
    pumpInfo: 'Electric input', waterInfo: 'DHW cylinder', houseInfo: 'Room temperature',
    outdoor: 'Outside', pressure: 'Water pressure', backup: 'Electric backup',
    waterTarget: 'DHW target', waterProgram: 'DHW mode', roomTarget: 'Heating target',
    slope: 'Curve slope', base: 'Curve base', source: 'Reported Remeha mode',
    heating: 'Space heating', dhw: 'Heating hot water', offline: 'Remeha is offline',
    unknown: 'Mode unavailable', idle: 'Idle', other: 'Mode',
    heat_pump: 'Heat pump', performance: 'Performance', eco: 'Eco', thermometers: 'Thermometers',
  },
};

const STYLES = `
  :host {
    display: block;
    color-scheme: dark;
    --ink: #eef6fe;
    --muted: #b0c3d4;
    --edge-line: #324a60;
    --supply: #ff9b79;
    --return: #74cefc;
    --gap: clamp(6px, 2%, 16px);
  }
  *, *::before, *::after { box-sizing: border-box; }
  ha-card {
    background: #101921;
    color: var(--ink);
    border: 1px solid var(--edge-line);
    padding: clamp(10px, 2.4vw, 22px);
  }
  h2 { margin: 0 0 12px; font-size: clamp(18px, 3vw, 25px); line-height: 1.2; font-weight: 650; }
  .system {
    padding: clamp(7px, 1.5vw, 15px);
    border-radius: 12px;
    border: 1px solid #293e50;
    background: radial-gradient(circle, #263b4c 1px, transparent 1.2px) 0 0 / 15px 15px, #121e28;
  }
  .devices, .detail {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--gap);
  }
  button {
    display: grid;
    grid-template-rows: minmax(2.2em, auto) auto auto auto;
    justify-items: center;
    align-items: center;
    gap: clamp(4px, 1vw, 9px);
    min-width: 0;
    padding: 6px 2px 8px;
    border: 1px solid var(--edge-line);
    border-radius: 10px;
    background: linear-gradient(150deg, #26394a, #162531);
    color: inherit;
    font: inherit;
    line-height: 1.2;
    cursor: pointer;
  }
  button[aria-pressed='true'] {
    border-color: var(--return);
    background: linear-gradient(150deg, #284d63, #172a38);
  }
  button:focus-visible { outline: 2px solid #f2f9ff; outline-offset: 2px; }
  .label { font-size: clamp(11px, 1.6vw, 14px); font-weight: 650; }
  .caption { color: var(--muted); font-size: clamp(11px, 1.5vw, 12px); }
  .reading {
    font-size: clamp(17px, 3.2vw, 31px);
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    letter-spacing: -.035em;
    white-space: nowrap;
  }
  .unit { color: var(--muted); font-size: .56em; font-weight: 550; letter-spacing: 0; }
  .device-icon {
    display: grid;
    place-items: center;
    width: clamp(41px, 7vw, 72px);
    height: clamp(47px, 7.5vw, 80px);
    border-radius: 9px;
    border: 1px solid #cbe2f5;
    color: #153349;
    background: linear-gradient(140deg, #c1d8eb, #7699b3);
  }
  .device-icon ha-icon { --mdc-icon-size: clamp(26px, 5vw, 49px); }
  .water .device-icon { border-radius: 42% / 24%; color: #03507d; }
  .house .device-icon {
    border: 0;
    background: #acd0d0;
    color: #17444d;
    clip-path: polygon(50% 0%, 100% 36%, 100% 100%, 0 100%, 0 36%);
  }
  .house ha-icon { margin-top: 6px; }

  /* Dvě vodorovné části vedou ke středu zásobníku a domu. Každá má
     samostatný přívod a zpátečku. Zvýrazněné části odpovídají hlášenému
     režimu a rychlost čárek je pouze grafická, nezávislá na naměřeném výkonu. */
  .pipes {
    position: relative;
    height: 43px;
    --end: calc((100% - var(--gap) * 2) / 6);
  }
  .pipe, .port { position: absolute; background: #687b8c; }
  .pipe { height: 3px; border-radius: 2px; }
  .from-pump { left: var(--end); right: 50%; }
  .to-house { left: 50%; right: var(--end); }
  .supply { top: 20px; --pipe-color: var(--supply); --pipe-gap: #804034; }
  .return { top: 30px; --pipe-color: var(--return); --pipe-gap: #365878; }
  .port { top: 0; width: 3px; }
  .port.supply { height: 22px; transform: translateX(-3px); }
  .port.return { height: 32px; transform: translateX(3px); }
  .pump-port { left: var(--end); }
  .water-port { left: 50%; }
  .house-port { left: calc(100% - var(--end)); }
  .pipes[data-branch='house'] :is(.to-house, .from-pump),
  .pipes[data-branch='water'] .from-pump {
    background: repeating-linear-gradient(90deg, var(--pipe-color) 0 7px, var(--pipe-gap) 7px 13px);
    animation: remeha-pipe .7s linear infinite;
  }
  .pipes[data-branch] .return { animation-direction: reverse; }
  .pipes[data-branch='house'] :is(.pump-port, .house-port),
  .pipes[data-branch='water'] :is(.pump-port, .water-port) { background: var(--pipe-color); }
  @keyframes remeha-pipe { to { background-position-x: 13px; } }
  @media (prefers-reduced-motion: reduce) { .pipes .pipe { animation: none !important; } }
  .status { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 3px 12px; font-size: 12px; }
  .status [data-mode]::before {
    content: ''; display: inline-block; width: 7px; height: 7px; margin-right: 6px;
    border-radius: 50%; background: #93a6b6;
  }
  .status [data-mode].active::before { background: var(--supply); }
  .detail { padding: 11px 0 0; margin: 11px 0 0; border-top: 1px solid var(--edge-line); }
  .detail[hidden] { display: none; }
  dt { color: var(--muted); font-size: 12px; }
  dd { margin: 2px 0 0; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .thermometers { margin-top: 17px; container-type: inline-size; }
  .thermometers h3 { margin: 0 0 8px; font-size: 16px; }
  .thermometers dl {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin: 0;
  }
  .thermometers dl > div {
    min-width: 0; padding: 7px 8px; border-radius: 8px; border: 1px solid var(--edge-line);
    background: #152531;
  }
  .thermometers .reference { border-color: var(--return); }
  .thermometers dd { font-size: 18px; }
  .thermometers small { color: var(--muted); font-size: 11px; }
  button.sensor-link, button.sensor-link:hover {
    display: inline; padding: 0; border: 0; background: transparent; color: inherit; text-align: left;
  }
  @container (max-width: 430px) {
    .thermometers dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
`;

// Prázdné stavy, unknown i unavailable zůstanou bez čísla. Nulový příkon
// zůstává 0. Desetinný oddělovač odpovídá jazyku karty.
const valid = (value) => typeof value === 'string' && value.trim() !== ''
  && value !== 'unknown' && value !== 'unavailable';

class RemehaHeatingSchematicCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.selected = 'pump';
  }

  setConfig(config) {
    if (!ROLES.every((role) => ID_PATTERN.test(config.entities?.[role] ?? ''))) {
      throw new Error('The heating diagram requires valid entity IDs for all roles.');
    }
    this.entities = { ...config.entities };
    // Volitelná měření jsou samostatná od režimu Remehy. Staré konfigurace
    // bez tohoto seznamu stále zobrazí původní schéma a detail.
    const thermometers = config.thermometers ?? [];
    if (!Array.isArray(thermometers) || thermometers.some((item) =>
      !ID_PATTERN.test(item?.entity ?? '') || typeof item.name !== 'string'
      || (item.attribute !== undefined && item.attribute !== 'current_temperature'))) {
      throw new Error('Invalid thermometer configuration.');
    }
    this.thermometers = thermometers.map((item) => ({ ...item }));
    this.heading = config.heading;
    const language = config.language === 'cs' ? 'cs' : 'en';
    this.labels = LABELS[language];
    this.number = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });
    this.temperature = new Intl.NumberFormat(language, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    this.integer = new Intl.NumberFormat(language, { maximumFractionDigits: 0 });
    this.build();
    this.update();
  }

  set hass(hass) {
    this.states = hass?.states;
    this.temperatureUnit = hass?.config?.unit_system?.temperature;
    if (this.fields) this.update();
  }

  getCardSize() { return 4; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: 'auto' }; }

  // Značky obsahují výhradně pevné texty překladu. Stav z HA zapisujeme
  // přes textContent. Běžný příjem stavů nevyměňuje DOM ani nepřesouvá focus.
  build() {
    const t = this.labels;
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        <h2>${t.heading}</h2>
        <div class="system">
          <div class="devices">
            ${DEVICES.map(({ id, icon }) => `
              <button type="button" class="${id}" data-device="${id}" aria-controls="detail-${id}">
                <span class="label">${t[id]}</span>
                <span class="device-icon" aria-hidden="true"><ha-icon icon="${icon}"></ha-icon></span>
                <span class="reading"><span data-value="${id}">—</span> <span class="unit" data-unit="${id}"></span></span>
                <span class="caption">${t[id + 'Info']}</span>
              </button>`).join('')}
          </div>
          <div class="pipes" aria-hidden="true">
            ${DEVICES.map(({ id }) => `
              <span class="port supply ${id}-port"></span><span class="port return ${id}-port"></span>`).join('')}
            <span class="pipe supply from-pump"></span><span class="pipe return from-pump"></span>
            <span class="pipe supply to-house"></span><span class="pipe return to-house"></span>
          </div>
          <div class="status"><strong data-mode></strong><span>${t.source}</span></div>
        </div>
        ${DEVICES.map(({ id, details }) => `
          <dl class="detail" id="detail-${id}" hidden aria-label="${t[id]}">
            ${details.map((item) => `<div><dt>${t[item]}</dt><dd data-value="${item}">—</dd></div>`).join('')}
          </dl>`).join('')}
      </ha-card>`;
    const root = this.shadowRoot;
    this.fields = Object.fromEntries([...root.querySelectorAll('[data-value]')].map((el) => [el.dataset.value, el]));
    this.units = Object.fromEntries([...root.querySelectorAll('[data-unit]')].map((el) => [el.dataset.unit, el]));
    this.pipes = root.querySelector('.pipes');
    this.mode = root.querySelector('[data-mode]');
    this.buttons = [...root.querySelectorAll('[data-device]')];
    this.details = [...root.querySelectorAll('.detail')];
    if (typeof this.heading === 'string' && this.heading.trim()) root.querySelector('h2').textContent = this.heading;
    this.appendThermometers();
    for (const button of this.buttons) {
      button.addEventListener('click', () => this.select(button.dataset.device));
    }
    this.select(this.selected);
  }

  appendThermometers() {
    this.thermometerReadings = [];
    if (!this.thermometers.length) return;
    const panel = document.createElement('section');
    panel.className = 'thermometers';
    panel.innerHTML = `<h3>${this.labels.thermometers}</h3><dl></dl>`;
    const list = panel.querySelector('dl');
    for (const item of this.thermometers) {
      const row = document.createElement('div');
      if (item.reference === true) row.classList.add('reference');
      const label = document.createElement('dt');
      if (item.entity.startsWith('sensor.')) {
        // Zachovat otevření detailu snímače z dosavadní dlaždice HA.
        // Hlavice zůstává pasivní, detail by nabízel ovládání.
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'sensor-link';
        button.textContent = item.name;
        button.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
          detail: { entityId: item.entity }, bubbles: true, composed: true,
        })));
        label.append(button);
      } else {
        label.textContent = item.name;
      }
      const value = document.createElement('dd');
      value.innerHTML = '<span>—</span> <small></small>';
      row.append(label, value);
      list.append(row);
      this.thermometerReadings.push({ item, value: value.firstElementChild, unit: value.lastElementChild });
    }
    this.shadowRoot.querySelector('ha-card').append(panel);
  }

  select(id) {
    this.selected = id;
    for (const button of this.buttons) button.setAttribute('aria-pressed', String(button.dataset.device === id));
    for (const detail of this.details) detail.hidden = detail.id !== 'detail-' + id;
  }

  // Jednotky se berou ze senzorů, u atributů termostatu a ohřívače z nastavení
  // HA. Starý elektrický údaj odmítneme, když je čas posledního hlášení dostupný.
  measurement(entity, { attribute, electrical = false, unit = '' } = {}) {
    if (!valid(entity?.state)) return ['—', ''];
    const raw = attribute ? entity.attributes?.[attribute] : entity.state;
    if (!(typeof raw === 'number' || valid(raw)) || !Number.isFinite(Number(raw))) return ['—', ''];
    const measured = Number(raw);
    if (electrical) {
      const last = Date.parse(entity.last_reported);
      if (Number.isFinite(last) && Date.now() - last > 180_000) return ['—', ''];
    }
    const measurementUnit = entity.attributes?.unit_of_measurement ?? unit;
    const value = electrical ? this.integer.format(Math.max(0, measured))
      : (/^°[CF]$/.test(measurementUnit) ? this.temperature : this.number).format(measured);
    return [value, measurementUnit];
  }

  write(key, value, unit = '') {
    this.fields[key].textContent = unit && !this.units[key] ? value + ' ' + unit : value;
    if (this.units[key]) this.units[key].textContent = unit;
  }

  // Aktivní větev se odvozuje výhradně z provozního režimu a online příznaku.
  // V neznámém stavu zůstanou obě větve neutrální. Shelly měří nezávisle na Remeze.
  update() {
    const t = this.labels;
    const entity = (role) => this.states?.[this.entities[role]];
    const online = entity('online')?.state;
    const isOnline = online === 'on';
    const thermal = entity('thermal')?.state;
    const normalized = isOnline && valid(thermal) ? thermal.toLowerCase() : '';
    const branch = normalized === 'heatingch' ? 'house' : normalized === 'heatingdhw' ? 'water' : '';
    let status = t.unknown;
    if (online === 'off') status = t.offline;
    else if (branch === 'house') status = t.heating;
    else if (branch === 'water') status = t.dhw;
    else if (['idle', 'none', 'standby', 'off'].includes(normalized)) status = t.idle;
    else if (normalized) status = t.other + ' · ' + thermal;
    if (this.pipes.dataset.branch !== branch) this.pipes.dataset.branch = branch;
    this.mode.classList.toggle('active', Boolean(branch));
    this.mode.textContent = status;

    const measured = (role) => isOnline ? entity(role) : undefined;
    const tempUnit = this.temperatureUnit ?? '°C';
    this.write('pump', ...this.measurement(entity('power'), { electrical: true }));
    this.write('backup', ...this.measurement(entity('backup_power'), { electrical: true }));
    this.write('water', ...this.measurement(measured('hot_water')));
    this.write('house', ...this.measurement(measured('room')));
    this.write('outdoor', ...this.measurement(measured('outdoor')));
    this.write('pressure', ...this.measurement(measured('pressure')));
    this.write('slope', ...this.measurement(measured('slope')));
    this.write('base', ...this.measurement(measured('base')));
    this.write('waterTarget', ...this.measurement(measured('water_heater'), { attribute: 'temperature', unit: tempUnit }));
    this.write('roomTarget', ...this.measurement(measured('climate'), { attribute: 'temperature', unit: tempUnit }));
    const program = measured('water_heater')?.state;
    this.write('waterProgram', valid(program) ? (Object.hasOwn(t, program) ? t[program] : program) : '—');
    // Čidla jiných integrací dál reportují i při výpadku Remehy. U hlavice
    // čteme naměřený atribut a jednotku HA. Volba karty neposílá povely.
    for (const { item, value, unit } of this.thermometerReadings) {
      const reading = this.measurement(this.states?.[item.entity], {
        attribute: item.attribute, unit: tempUnit,
      });
      value.textContent = reading[0];
      unit.textContent = reading[1];
    }
  }
}

if (!customElements.get(CARD_TAG)) customElements.define(CARD_TAG, RemehaHeatingSchematicCard);
