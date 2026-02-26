/* circuit.js — F1 DALI Circuit Visualizer
 * Draws an animated F1 circuit with real car positions derived
 * from the DALI race-state API.  Activated when the "Circuit" tab
 * is selected (switchTab / circuitInit / circuitDestroy exported to
 * the global scope for app.js to call).
 */

/* ─── roundRect polyfill (Chrome < 99 / older WebView) ─── */
if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function(x, y, w, h, r) {
    const rr = Math.min(r, w / 2, h / 2);
    this.beginPath();
    this.moveTo(x + rr, y);
    this.lineTo(x + w - rr, y);   this.arcTo(x+w, y,     x+w, y+h,   rr);
    this.lineTo(x + w, y + h - rr); this.arcTo(x+w, y+h,   x,   y+h,   rr);
    this.lineTo(x + rr, y + h);   this.arcTo(x,   y+h,   x,   y,     rr);
    this.lineTo(x, y + rr);       this.arcTo(x,   y,     x+w, y,     rr);
    this.closePath();
  };
}

/* ═══════════════════════════════════════════════════════════════════
   1.  TRACK GEOMETRY  (Monza-inspired, 760×490 canvas)
   ═══════════════════════════════════════════════════════════════════ */

/** Centerline control points (closed loop – first == last). */
const WAYPOINTS = [
  [190, 440], // [0] S/F
  [395, 440], // main straight mid
  [570, 440], // T1 braking
  [620, 436],
  [648, 420],
  [656, 402],
  [660, 378], // T1 exit
  [660, 352],
  [655, 325], // into chicane
  [638, 306],
  [616, 296], // chicane left
  [594, 292],
  [580, 280],
  [582, 264],
  [596, 252], // chicane right
  [616, 248],
  [638, 254],
  [654, 264],
  [660, 252], // out of chicane
  [654, 232],
  [638, 204], // into Curva Grande
  [608, 175],
  [562, 152], // Curva Grande apex
  [502, 136],
  [432, 124], // top straight
  [360, 118],
  [292, 122],
  [232, 136], // into hairpin
  [182, 158],
  [148, 188], // hairpin entry
  [126, 224],
  [126, 268], // hairpin apex
  [144, 305],
  [170, 336], // hairpin exit / Parabolica approach
  [180, 370],
  [186, 408], // Parabolica
  [190, 440], // close – same as [0]
];

/* ─── Catmull-Rom spline ─── */
function _crPt(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  return [
    0.5 * (2*p1[0] + (-p0[0]+p2[0])*t + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2 + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3),
    0.5 * (2*p1[1] + (-p0[1]+p2[1])*t + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2 + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3),
  ];
}

/** Build a dense array of (x,y) samples along the closed spline. */
function buildSplineSamples(pts, steps = 60) {
  const n   = pts.length - 1; // last pt == first pt
  const out = [];
  for (let i = 0; i < n; i++) {
    const p0 = pts[Math.max(0,  i - 1)];
    const p1 = pts[i];
    const p2 = pts[Math.min(n - 1, i + 1)];
    const p3 = pts[Math.min(n - 1, i + 2)];
    for (let j = 0; j < steps; j++) {
      out.push(_crPt(p0, p1, p2, p3, j / steps));
    }
  }
  out.push(out[0]); // close
  return out;
}

/** Build a cumulative arc-length table for the sample array. */
function buildArcTable(samples) {
  const table = [0];
  for (let i = 1; i < samples.length; i++) {
    const dx = samples[i][0] - samples[i-1][0];
    const dy = samples[i][1] - samples[i-1][1];
    table.push(table[i-1] + Math.sqrt(dx*dx + dy*dy));
  }
  return table;
}

/** Return [x, y] at normalised arc-length t ∈ [0,1]. */
function ptAtT(t, samples, arcTbl) {
  const total  = arcTbl[arcTbl.length - 1];
  const target = ((t % 1) + 1) % 1 * total;
  let lo = 0, hi = arcTbl.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    arcTbl[mid] < target ? (lo = mid + 1) : (hi = mid);
  }
  if (lo === 0) return samples[0];
  const frac = (arcTbl[lo] === arcTbl[lo-1]) ? 0
    : (target - arcTbl[lo-1]) / (arcTbl[lo] - arcTbl[lo-1]);
  return [
    samples[lo-1][0] + frac * (samples[lo][0] - samples[lo-1][0]),
    samples[lo-1][1] + frac * (samples[lo][1] - samples[lo-1][1]),
  ];
}

/** Return heading angle at t (radians). */
function angleAtT(t, samples, arcTbl, eps = 0.008) {
  const a = ptAtT(Math.max(0, t - eps), samples, arcTbl);
  const b = ptAtT(Math.min(1, t + eps), samples, arcTbl);
  return Math.atan2(b[1] - a[1], b[0] - a[0]);
}

/* Pre-compute once (filled on circuitInit) */
let _samples = null;
let _arcTbl  = null;

function ensureSpline() {
  if (!_samples) {
    _samples = buildSplineSamples(WAYPOINTS, 80);
    _arcTbl  = buildArcTable(_samples);
  }
}

/* ═══════════════════════════════════════════════════════════════════
   2.  CAR ANIMATION STATE
   ═══════════════════════════════════════════════════════════════════ */

/**
 * Each lap is replayed at scaled speed: a 75 s lap plays back in ~7.5 s.
 * Factor: divide the real lap seconds by this to get display milliseconds.
 */
const LAP_SPEED_FACTOR = 80;    // 1000 = realtime, 100 = 10×, 80 = ~12×
// Minimum must stay BELOW the fastest expected lap * factor so speed differences
// are never flattened.  Typical DALI lap ~72 s → 72×80 = 5760 ms; floor at 2 s.
const MIN_LAP_MS       = 2_000; // floor: safety net only, must not equalise laps
const PIT_DISPLAY_MS   = 4_000; // how long the car sits in the pit-lane
const LIGHTS_OUT_DELAY = 5_200; // ms — red-lights sequence before cars move

/**
 * Per-car anim record:
 *   queue          – pending events [{type, durationMs}]
 *   active         – event currently playing or null
 *   enqueuedLaps   – how many lap events pushed so far
 *   enqueuedPits   – how many pit events pushed so far
 *   enqueuedDNF    – whether a DNF event was already queued
 *   raceOver       – API flagged the race as finished
 *   t              – current track position [0..0.97]
 */
const carAnim = {};

let raceSnapshot     = null;
let _prevRaceOver    = false;
let _prevRaceStarted = false;
/**
 * Wall-clock timestamp (ms) of when race_started first became true.
 * Used as the origin for LIGHTS_OUT_DELAY and the animation barrier.
 */
let _raceStartedAt   = 0;
/**
 * Animation barrier: the wall-clock time at which all cars' first lap
 * animations begin (= _raceStartedAt + LIGHTS_OUT_DELAY).
 * Set once for ALL cars simultaneously when every car has ≥1 lap enqueued,
 * guaranteeing a perfectly synchronised start regardless of API poll order.
 */
let _animationBarrier = null;

function _blankAnim(id) {
  return {
    queue: [], active: null,
    enqueuedLaps: 0, enqueuedPits: 0, enqueuedDNF: false,
    raceOver: false, t: 0, id,
    // Chain timing: chainBase is set by _trySetBarrier() for ALL cars
    // simultaneously, ensuring a perfectly synchronised animated start.
    chainBase:         null, // set to _animationBarrier when barrier fires
    completedDuration: 0,   // total display-ms of all events already finished
    completedLaps:     0,   // how many lap animations have fully played out
  };
}

/**
 * Sets the animation barrier once every car has at least one lap enqueued,
 * or after a 30-second fallback timeout (guards against agents that never
 * report laps so that cars which DO have data still animate).
 * Assigns chainBase to ALL cars simultaneously so they share an identical
 * time origin and start from the S/F line together.
 *
 * chainBase = max(now, raceStartedAt + LIGHTS_OUT_DELAY)
 *   • Barrier fires early (all cars ready before lights-out): chainBase is in
 *     the future → cars wait at the line until the lights go out. ✓
 *   • Barrier fires late (some agent slow to report first lap): chainBase is
 *     Date.now() → cars start immediately from t=0, no stale past-anchor that
 *     would cause laps to expire instantly or start mid-way through. ✓
 */
function _trySetBarrier() {
  if (_animationBarrier !== null) return;  // already fired
  if (!_raceStartedAt) return;
  const cars = Object.values(carAnim);
  if (!cars.length) return;
  const allReady = cars.every(a => a.enqueuedLaps > 0);
  // Fallback: if 30 s after race start some cars still have 0 laps,
  // fire the barrier anyway so cars with data still animate.
  const timedOut = Date.now() - _raceStartedAt > 30_000;
  if (!allReady && !timedOut) return;
  _animationBarrier = Math.max(Date.now(), _raceStartedAt + LIGHTS_OUT_DELAY);
  for (const a of cars) a.chainBase = _animationBarrier;
}

/**
 * Returns true once every car's animation queue is fully drained.
 * Used by app.js to gate the leaderboard modal in circuit view.
 */
function circuitQueueDrained() {
  const cars = Object.values(carAnim);
  if (!cars.length) return false;
  return cars.every(a =>
    _isDNF(a) || (!a.active && a.queue.length === 0 && a.enqueuedLaps > 0)
  );
}

/**
 * Called every time /api/race-state returns fresh data.
 * Compares against the last-seen enqueued counts and pushes new events
 * into each car’s queue.  Never rewinds — only appends.
 */
function updateCarAnim(data) {
  const raceOver    = !!data.race_over;
  const raceStarted = !!data.race_started;

  // ── Detect a genuine new race ──────────────────────────────────────────
  // We ONLY reset on race_over → false (clean end-of-race cycle) OR when
  // laps_completed goes backwards for any car (MAS hard-restart mid-race).
  //
  // We deliberately do NOT reset on race_started → false because:
  //   • capture_pane occasionally returns empty / stale text mid-race
  //   • that causes race_started to momentarily flip false
  //   • resetting here would wipe all animation state mid-race (cars freeze)
  //
  if (_prevRaceOver && !raceOver) {
    // Normal cycle: race finished → new race starting.
    for (const key of Object.keys(carAnim)) delete carAnim[key];
    _prevRaceStarted  = false;
    _raceStartedAt    = 0;
    _animationBarrier = null;
  } else {
    // Mid-race MAS restart: detect by laps_completed decreasing below what
    // we already enqueued for any car.  This is the only reliable signal that
    // a genuinely new race is running (simple race_started flip is too noisy).
    for (const [id, car] of Object.entries(data.cars || {})) {
      const a = carAnim[id];
      if (a && car.laps_completed < a.enqueuedLaps) {
        // This car has fewer laps than we already animated → new race.
        for (const key of Object.keys(carAnim)) delete carAnim[key];
        _prevRaceStarted  = false;
        _raceStartedAt    = 0;
        _animationBarrier = null;
        break;
      }
    }
  }
  _prevRaceOver = raceOver;

  // Record the exact moment the race first starts so all cars sync.
  if (raceStarted && !_prevRaceStarted) {
    _raceStartedAt   = Date.now();
    _prevRaceStarted = true;
  }

  for (const [id, car] of Object.entries(data.cars || {})) {
    if (!carAnim[id]) {
      carAnim[id] = _blankAnim(id);
      // If the barrier already fired before this car appeared (e.g. late poll),
      // assign chainBase immediately so it can animate without waiting forever.
      if (_animationBarrier !== null) carAnim[id].chainBase = _animationBarrier;
    }
    const a = carAnim[id];
    if (raceOver) a.raceOver = true;

    // ── Enqueue new laps ─────────────────────────────────────────
    const lapTimes = car.lap_times || [];
    const newLaps  = car.laps_completed - a.enqueuedLaps;
    for (let i = 0; i < newLaps; i++) {
      const timeS      = lapTimes[a.enqueuedLaps + i] ?? 75;
      const durationMs = Math.max(MIN_LAP_MS, timeS * LAP_SPEED_FACTOR);
      a.queue.push({ type: 'lap', durationMs });
      a.enqueuedLaps++;
    }

    // ── Enqueue new pit stops ─────────────────────────────────
    const newPits = (car.pit_stops ?? 0) - a.enqueuedPits;
    for (let i = 0; i < newPits; i++) {
      a.queue.push({ type: 'pit', durationMs: PIT_DISPLAY_MS });
      a.enqueuedPits++;
    }

    // ── Enqueue DNF once ──────────────────────────────────────
    if (car.dnf && !a.enqueuedDNF) {
      a.queue.push({ type: 'dnf', durationMs: Infinity });
      a.enqueuedDNF = true;
    }

    // ── Visual metadata (always up-to-date) ─────────────────────
    a.color  = car.color;
    a.border = car.border;
    a.label  = car.label;
    a.driver = car.driver;
    a.pos    = car.position;
    a.totalT = car.total_time;
  }

  // Try to set the animation barrier once ALL cars have their first lap.
  // Until then no car moves, guaranteeing a perfectly synchronised start.
  _trySetBarrier();
}

/**
 * Advance a car’s event queue by one step (called once per animation frame).
 * Moves to the next queued event when the current one expires.
 */
function advanceCar(a) {
  // DNF is terminal — leave the car where it stopped.
  if (a.active?.type === 'dnf') return;

  if (a.active) {
    const elapsed = Date.now() - a.active.startAt;
    if (elapsed >= a.active.durationMs) {
      a.completedDuration += a.active.durationMs;
      if (a.active.type === 'lap') {
        a.completedLaps++;
        a.t = 1.0;   // guarantee car is at S/F line when lap expires,
                     // even if requestAnimationFrame was throttled and
                     // currentT() never reached 1.0 before expiry
      }
      a.active = null;
    }
  }

  if (!a.active && a.queue.length > 0 && a.chainBase !== null) {
    const ev = a.queue.shift();
    a.active = { ...ev, startAt: a.chainBase + a.completedDuration };
    if (a.active.type === 'lap') a.t = 0;
  }
}

/* Derived boolean state from active event */
function _isInPit(a) { return a.active?.type === 'pit'; }
function _isDNF(a)   {
  return a.active?.type === 'dnf' ||
         (a.enqueuedDNF && !a.active && a.queue.length === 0);
}

/** Return track position t ∈ [0, 1] for the current animation frame.
 *  Clamped to [0, 1]: negative elapsed (startAt slightly in the future due to
 *  chain-timing rounding) kept at 0 so the car waits at the S/F line instead
 *  of wrapping to ~0.99 and snapping.  ptAtT wraps 1.0 back to S/F point. */
function currentT(a) {
  if (!a.active) return a.t;       // idle / finished — hold position
  const elapsed = Date.now() - a.active.startAt;
  if (a.active.type === 'lap') {
    a.t = Math.max(0, Math.min(1.0, elapsed / a.active.durationMs));
    return a.t;
  }
  if (a.active.type === 'pit') return 0.005; // near S/F straight
  return a.t;                       // dnf or unknown — hold
}

/* ═══════════════════════════════════════════════════════════════════
   3.  CANVAS DRAW HELPERS
   ═══════════════════════════════════════════════════════════════════ */

const CANVAS_W = 760;
const CANVAS_H = 490;

/** Draw the static track (asphalt, edges, S/F line, labels). */
function drawTrack(ctx) {
  /* ── grass background ── */
  ctx.fillStyle = '#0e1a0e';
  ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);

  /* ── track outer edge (slightly lighter than asphalt) ── */
  ctx.lineWidth   = 42;
  ctx.lineCap     = 'round';
  ctx.lineJoin    = 'round';
  ctx.strokeStyle = '#363636';
  _strokeSpline(ctx);

  /* ── track surface (asphalt) ── */
  ctx.lineWidth   = 34;
  ctx.strokeStyle = '#252525';
  _strokeSpline(ctx);

  /* ── subtle centre-line dashes along main straight ── */
  ctx.lineWidth   = 1;
  ctx.strokeStyle = '#ffffff22';
  ctx.setLineDash([14, 12]);
  ctx.beginPath();
  ctx.moveTo(200, 440);
  ctx.lineTo(568, 440);
  ctx.stroke();
  ctx.setLineDash([]);

  /* ── Start / Finish line ── */
  const sfPt    = ptAtT(0, _samples, _arcTbl);
  const sfAngle = angleAtT(0, _samples, _arcTbl);
  const perp    = sfAngle + Math.PI / 2;
  const sfLen   = 20;
  ctx.lineWidth   = 3.5;
  ctx.strokeStyle = '#ffffff';
  ctx.beginPath();
  ctx.moveTo(sfPt[0] + Math.cos(perp)*sfLen, sfPt[1] + Math.sin(perp)*sfLen);
  ctx.lineTo(sfPt[0] - Math.cos(perp)*sfLen, sfPt[1] - Math.sin(perp)*sfLen);
  ctx.stroke();

  /* ── Sector lines ── */
  for (const [st, colour] of [[0.34, '#ff000055'], [0.67, '#00aaff55']]) {
    const pt  = ptAtT(st, _samples, _arcTbl);
    const ang = angleAtT(st, _samples, _arcTbl) + Math.PI / 2;
    ctx.lineWidth   = 2;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    ctx.moveTo(pt[0] + Math.cos(ang)*17, pt[1] + Math.sin(ang)*17);
    ctx.lineTo(pt[0] - Math.cos(ang)*17, pt[1] - Math.sin(ang)*17);
    ctx.stroke();
  }

  /* ── Kerb stripes at key apexes ── */
  _drawKerb(ctx, 0.19, '#cc2200');  // Chicane 1
  _drawKerb(ctx, 0.29, '#cc2200');  // Chicane 2 (wider)
  _drawKerb(ctx, 0.59, '#2266cc');  // Hairpin apex
}

function _strokeSpline(ctx) {
  ctx.beginPath();
  ctx.moveTo(_samples[0][0], _samples[0][1]);
  for (let i = 1; i < _samples.length; i++) {
    ctx.lineTo(_samples[i][0], _samples[i][1]);
  }
  ctx.closePath();
  ctx.stroke();
}

/** Draw short red/white kerb dots perpendicular to the track at position t. */
function _drawKerb(ctx, t, colour) {
  const pt  = ptAtT(t, _samples, _arcTbl);
  const ang = angleAtT(t, _samples, _arcTbl) + Math.PI / 2; // perpendicular
  const R   = 14;
  for (let i = 0; i < 5; i++) {
    const frac = (i / 4) * 2 - 1; // from -1 to +1
    ctx.fillStyle = i % 2 === 0 ? colour : '#ffffff';
    ctx.beginPath();
    ctx.arc(
      pt[0] + Math.cos(ang) * R * frac,
      pt[1] + Math.sin(ang) * R * frac,
      2.5, 0, Math.PI * 2,
    );
    ctx.fill();
  }
}

/** Draw a safety-car overlay blink if SC is active. */
let _scBlink = 0;
function drawSafetyCar(ctx) {
  _scBlink = (_scBlink + 1) % 60;
  if (_scBlink < 30) {
    ctx.fillStyle = '#ffd70033';
    ctx.fillRect(0, 0, CANVAS_W, CANVAS_H);
    ctx.fillStyle = '#ffd700';
    ctx.font = 'bold 11px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('🚗 SAFETY CAR', CANVAS_W / 2, 18);
  }
}

/**
 * Draw F1-style start lights.
 * Sequence (anchored to _raceStartedAt):
 *   0 – 1 s  : 1 red light on
 *   1 – 2 s  : 2 red lights on
 *   2 – 3 s  : 3 red lights on
 *   3 – 4 s  : 4 red lights on
 *   4 – 5 s  : 5 red lights on (LIGHTS_OUT_DELAY = 5200 ms)
 *   5 – 5.2 s: all lights on (brief hold)
 *   5.2 s    : LIGHTS OUT — cars begin moving
 *   5.2–7 s  : "LIGHTS OUT!" flash fades
 */
function drawStartLights(ctx) {
  if (!_raceStartedAt) return;
  const elapsed = Date.now() - _raceStartedAt;
  const fadeEnd = LIGHTS_OUT_DELAY + 1800;
  if (elapsed > fadeEnd) return;   // sequence fully over

  const CX = CANVAS_W / 2;
  const CY = 44;
  const R  = 15;           // light radius
  const GAP = 42;          // centre-to-centre spacing
  const N   = 5;
  const totalW = (N - 1) * GAP;
  const barPad = R + 16;

  const lightsOn = Math.min(N, Math.floor(elapsed / 1000));
  const isOut    = elapsed >= LIGHTS_OUT_DELAY;

  /* background bar */
  ctx.save();
  ctx.fillStyle = 'rgba(0,0,0,0.84)';
  ctx.beginPath();
  ctx.roundRect(CX - totalW / 2 - barPad, CY - R - 14,
                totalW + barPad * 2, R * 2 + 28, 10);
  ctx.fill();

  /* marshal board stripe */
  ctx.fillStyle = '#333';
  ctx.fillRect(CX - totalW / 2 - barPad, CY + R + 4,
               totalW + barPad * 2, 4);

  /* lights */
  for (let i = 0; i < N; i++) {
    const lx  = CX - totalW / 2 + i * GAP;
    const on  = !isOut && i < lightsOn;

    /* housing ring */
    ctx.beginPath();
    ctx.arc(lx, CY, R, 0, Math.PI * 2);
    ctx.fillStyle   = '#1a1a1a';
    ctx.fill();
    ctx.strokeStyle = '#444';
    ctx.lineWidth   = 1.5;
    ctx.stroke();

    if (on) {
      /* red glow halo */
      const grad = ctx.createRadialGradient(lx, CY, 0, lx, CY, R);
      grad.addColorStop(0,   '#ff4422');
      grad.addColorStop(0.6, '#cc1100');
      grad.addColorStop(1,   '#880000');
      ctx.beginPath();
      ctx.arc(lx, CY, R - 2, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();

      ctx.shadowColor = '#ff2200';
      ctx.shadowBlur  = 22;
      ctx.beginPath();
      ctx.arc(lx, CY, R - 2, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,80,40,0.3)';
      ctx.fill();
      ctx.shadowBlur = 0;

      /* specular highlight */
      ctx.beginPath();
      ctx.arc(lx - 4, CY - 4, 4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.fill();
    }
  }

  /* "LIGHTS OUT" flash */
  if (isOut && elapsed < fadeEnd) {
    const alpha = Math.max(0, 1 - (elapsed - LIGHTS_OUT_DELAY) / 1800);
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.font         = 'bold 18px monospace';
    ctx.shadowColor  = '#ffff00';
    ctx.shadowBlur   = 14;
    ctx.fillStyle    = `rgba(255,230,0,${alpha})`;
    ctx.fillText('LIGHTS OUT!', CX, CY);
    ctx.shadowBlur   = 0;
  }

  ctx.restore();
}

/** Draw a single car at position t on the track. */
function drawCar(ctx, a, t) {
  const [x, y] = ptAtT(t, _samples, _arcTbl);
  const angle  = angleAtT(t, _samples, _arcTbl);

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);

  const CW = 24, CH = 13;
  const r  = 3;

  /* shadow */
  ctx.fillStyle = 'rgba(0,0,0,0.45)';
  ctx.beginPath();
  ctx.roundRect(-CW/2 + 2, -CH/2 + 2, CW, CH, r);
  ctx.fill();

  /* border glow */
  ctx.shadowColor  = a.border;
  ctx.shadowBlur   = 8;

  /* car body */
  ctx.fillStyle = a.border;
  ctx.beginPath();
  ctx.roundRect(-CW/2, -CH/2, CW, CH, r);
  ctx.fill();

  /* inner dark fill */
  ctx.shadowBlur = 0;
  ctx.fillStyle  = a.color;
  ctx.beginPath();
  ctx.roundRect(-CW/2 + 2, -CH/2 + 2, CW - 4, CH - 4, r - 1);
  ctx.fill();

  /* driver initial */
  ctx.fillStyle     = '#ffffff';
  ctx.font          = 'bold 6px Arial';
  ctx.textAlign     = 'center';
  ctx.textBaseline  = 'middle';
  ctx.fillText((a.driver || a.id)[0].toUpperCase(), 0, 0);

  ctx.restore();

  /* label above car */
  ctx.fillStyle    = a.border;
  ctx.font         = 'bold 7.5px monospace';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'bottom';
  ctx.shadowColor  = '#000';
  ctx.shadowBlur   = 3;
  ctx.fillText(a.id.toUpperCase(), x, y - 9);
  ctx.shadowBlur   = 0;

  /* position / lap badge */
  ctx.font         = 'bold 7px monospace';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'top';
  const currentAnimLap = a.completedLaps + (a.active?.type === 'lap' ? 1 : 0);
  const badgeParts = [];
  if (a.pos)            badgeParts.push('P' + a.pos);
  if (currentAnimLap)   badgeParts.push('L' + currentAnimLap);
  if (badgeParts.length) {
    ctx.fillStyle = '#ffd700';
    ctx.fillText(badgeParts.join(' '), x, y + 7);
  }
}

/* draws DNF tombstone at a fixed location near pit-lane */
function drawDNF(ctx, a, idx) {
  const x = 300 + idx * 90, y = 475;
  ctx.fillStyle    = '#cc4444';
  ctx.font         = 'bold 7px monospace';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('✕ ' + (a.id||'').toUpperCase() + ' DNF', x, y);
}

/* draws the pit-stop label near S/F for a car in pit */
function drawPit(ctx, a, idx) {
  const sfPt = ptAtT(0.005 + idx * 0.013, _samples, _arcTbl);
  /* pit lane strip (inner side of S/F straight) */
  drawCar(ctx, a, 0.005 + idx * 0.013);
  ctx.fillStyle    = '#aaa';
  ctx.font         = 'bold 7px monospace';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'bottom';
  ctx.fillText('🔧 PIT', sfPt[0], sfPt[1] - 17);
}

/* ═══════════════════════════════════════════════════════════════════
   4.  MAIN RENDER LOOP
   ═══════════════════════════════════════════════════════════════════ */

let _animId  = null;
let _canvas  = null;
let _ctx     = null;

function renderFrame() {
  if (!_canvas || !_ctx || !_samples) return;
  _ctx.clearRect(0, 0, CANVAS_W, CANVAS_H);

  drawTrack(_ctx);

  if (raceSnapshot?.safety_car) drawSafetyCar(_ctx);
  drawStartLights(_ctx);   // start-light sequence (no-op once race is running)

  let dnfIdx = 0, pitIdx = 0;
  for (const a of Object.values(carAnim)) {
    advanceCar(a);          // tick the event queue forward
    if (_isDNF(a)) {
      drawDNF(_ctx, a, dnfIdx++);
    } else if (_isInPit(a)) {
      drawPit(_ctx, a, pitIdx++);
    } else {
      drawCar(_ctx, a, currentT(a));
    }
  }

  /* circuit label */
  _ctx.fillStyle    = '#ffffff18';
  _ctx.font         = '9px monospace';
  _ctx.textAlign    = 'left';
  _ctx.textBaseline = 'bottom';
  _ctx.fillText('DALI F1 Circuit — Monza', 6, CANVAS_H - 3);

  _animId = requestAnimationFrame(renderFrame);
}

/* ═══════════════════════════════════════════════════════════════════
   5.  SIDEBAR  (standings + events)
   ═══════════════════════════════════════════════════════════════════ */

function updateSidebar(data) {
  /* Status */
  const statusEl = document.getElementById('csb-status');
  if (statusEl) {
    let txt = 'Waiting for race…';
    if (data.race_over)    txt = '🏁 Race finished';
    else if (data.safety_car) txt = '🚗 Safety Car deployed';
    else if (data.race_started) {
      const lap  = data.current_lap  || '?';
      const tot  = data.total_laps   || '?';
      txt = `🟢 Lap ${lap} / ${tot}`;
    }
    statusEl.textContent = txt;
  }

  /* Standings */
  const standEl = document.getElementById('csb-standings');
  if (standEl && data.cars) {
    const sorted = Object.values(data.cars).sort((a, b) => {
      if (a.position && b.position) return a.position - b.position;
      return (b.laps_completed - a.laps_completed) || (a.total_time - b.total_time);
    });
    standEl.innerHTML = sorted.map(car => {
      const pos   = car.position ? `P${car.position}` : '–';
      const time  = car.total_time ? `${car.total_time}s` : '–';
      const badge = car.dnf ? '<span class="csb-dnf">DNF</span>'
                  : car.in_pit ? '<span class="csb-pit">PIT</span>' : '';
      return `<div class="csb-car" style="border-left:3px solid ${car.border}">
        <span class="csb-pos">${pos}</span>
        <span class="csb-name" style="color:${car.border}">${car.label}</span>
        <span class="csb-driver">${car.driver}</span>
        ${badge}
        <span class="csb-time">${time}</span>
      </div>`;
    }).join('');
  }

  /* Events */
  const evEl = document.getElementById('csb-events');
  if (evEl && data.recent_events) {
    evEl.innerHTML = (data.recent_events || [])
      .slice(-6)
      .map(e => {
        let cls = 'ev-normal';
        if (/safety car/i.test(e))   cls = 'ev-sc';
        if (/DNF|failure/i.test(e))  cls = 'ev-dnf';
        if (/green flag|CHEQUERED/i.test(e)) cls = 'ev-green';
        if (/RESULTS|winner/i.test(e)) cls = 'ev-gold';
        return `<div class="csb-event ${cls}">${e}</div>`;
      })
      .join('');
    evEl.scrollTop = evEl.scrollHeight;
  }
}

/* ═══════════════════════════════════════════════════════════════════
   6.  API POLLING
   ═══════════════════════════════════════════════════════════════════ */

let _pollId  = null;

async function fetchRaceState() {
  try {
    const r = await fetch('/api/race-state');
    if (!r.ok) return;
    const data = await r.json();
    raceSnapshot = data;
    updateCarAnim(data);
    updateSidebar(data);
  } catch (_) { /* server not up yet */ }
}

/* ═══════════════════════════════════════════════════════════════════
   7.  TAB LIFECYCLE  (called from app.js / switchTab)
   ═══════════════════════════════════════════════════════════════════ */

function circuitInit() {
  ensureSpline();

  _canvas = document.getElementById('track-canvas');
  if (!_canvas) return;

  /* Fixed internal resolution */
  _canvas.width  = CANVAS_W;
  _canvas.height = CANVAS_H;
  _ctx = _canvas.getContext('2d');

  fetchRaceState();
  _pollId = setInterval(fetchRaceState, 2000);
  renderFrame();
}

function circuitDestroy() {
  if (_animId) { cancelAnimationFrame(_animId); _animId = null; }
  if (_pollId) { clearInterval(_pollId);         _pollId = null; }
}
