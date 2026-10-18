/* AI Rádió spektrum: valós idejű, a zenére pulzáló megjelenítő Web Audio FFT-ből.
 *
 * Három stílus: "bars" (oszlopok tükörképpel és csúcsjelzőkkel), "wave"
 * (lágy, tükrözött hullám) és "radial" (körkörös, a mélyekre lüktető mag).
 * Ugyanazt az AnalyserNode-ot több vászon is használhatja. Hang nélkül halk,
 * lélegző üresjárati animációt mutat.
 */
(function () {
  "use strict";

  const PALETTES = {
    auto: ["#06b6d4", "#6366f1", "#d946ef"],
    news: ["#facc15", "#f97316", "#ef4444"],
    live: ["#fb7185", "#f43f5e", "#f97316"],
    manual: ["#818cf8", "#a855f7", "#ec4899"],
    idle: ["#334155", "#475569", "#64748b"],
  };
  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const hexToRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

  class Spectrum {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      // top: a vászon felső része, amit a spektrum szabadon hagy (pl. a cím alatt)
      this.opts = Object.assign({ bars: 72, mode: "bars", reflection: true, peaks: true, glow: 22, minHz: 32, maxHz: 14000, bloom: true, top: 0 }, opts);
      this.mode = this.opts.mode;
      this.analyser = null;
      this.levels = new Float32Array(this.opts.bars);
      this.trail = new Float32Array(this.opts.bars);
      this.peaks = new Float32Array(this.opts.bars);
      this.peakHold = new Float32Array(this.opts.bars);
      this.colors = PALETTES.idle.map(hexToRgb);
      this.target = this.colors.map((c) => c.slice());
      this.bass = 0;
      this.running = false;
      this.last = 0;
      this._frame = this._frame.bind(this);
      this._resize();
      if (window.ResizeObserver) new ResizeObserver(() => this._resize()).observe(canvas);
      else window.addEventListener("resize", () => this._resize());
    }

    attach(analyser) {
      this.analyser = analyser;
      this.freq = new Float32Array(analyser.frequencyBinCount);
      this._bands();
    }

    setActive(active) { this.active = active; }
    setMode(mode) { this.mode = mode; }
    setPalette(name) { this.target = (PALETTES[name] || PALETTES.auto).map(hexToRgb); }

    start() { if (!this.running) { this.running = true; requestAnimationFrame(this._frame); } }
    stop() { this.running = false; }

    _resize() {
      const r = this.canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.w = Math.max(1, r.width);
      this.h = Math.max(1, r.height);
      this.canvas.width = Math.round(this.w * dpr);
      this.canvas.height = Math.round(this.h * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    // Logaritmikus frekvenciasávok: a basszus ugyanannyi helyet kap, mint a magasak.
    _bands() {
      const n = this.opts.bars, sr = this.analyser.context.sampleRate, bins = this.analyser.frequencyBinCount;
      const hzPerBin = sr / 2 / bins;
      this.ranges = [];
      for (let i = 0; i < n; i++) {
        const f0 = this.opts.minHz * Math.pow(this.opts.maxHz / this.opts.minHz, i / n);
        const f1 = this.opts.minHz * Math.pow(this.opts.maxHz / this.opts.minHz, (i + 1) / n);
        const a = Math.max(1, Math.floor(f0 / hzPerBin)), b = Math.max(a + 1, Math.ceil(f1 / hzPerBin));
        // A magasabb sávok energiája kisebb: oktávonként +3 dB-lel kiegyenlítjük.
        this.ranges.push([a, Math.min(b, bins), 3 * Math.log2(f0 / 100)]);
      }
    }

    _measure(t) {
      const n = this.opts.bars;
      const live = this.analyser && this.active;
      if (live) this.analyser.getFloatFrequencyData(this.freq);
      const minDb = -88, maxDb = -18;
      for (let i = 0; i < n; i++) {
        let v;
        if (live) {
          const [a, b, tilt] = this.ranges[i];
          let m = -200;
          for (let k = a; k < b; k++) if (this.freq[k] > m) m = this.freq[k];
          v = (m + tilt - minDb) / (maxDb - minDb);
          v = Math.max(0, Math.min(1, v));
          v = Math.pow(v, 1.6); // kontraszt: a halk zaj ne töltse ki a képet
        } else if (this.active && !reduceMotion) {
          // szól, de nincs elemző (telefonon sima lejátszás): ütemre lüktető, élénkebb minta
          const beat = Math.pow(0.5 + 0.5 * Math.sin(t / 240), 6);
          v = 0.2 + (i < n * 0.2 ? beat * 0.45 : beat * 0.08) + 0.14 * Math.sin(t / 380 + i * 0.5)
            + 0.1 * Math.sin(t / 170 - i * 0.23) * Math.sin(t / 900 + i);
          v = Math.max(0.03, Math.min(0.95, v * (1 - (i / n) * 0.45)));
        } else {
          // üresjárat: lassan hullámzó, halk minta
          v = reduceMotion ? 0.05 : 0.05 + 0.04 * Math.sin(t / 900 + i * 0.32) + 0.025 * Math.sin(t / 470 - i * 0.11);
        }
        const cur = this.levels[i];
        this.levels[i] = cur + (v - cur) * (v > cur ? 0.55 : 0.14);
        this.trail[i] += (this.levels[i] - this.trail[i]) * 0.06;
        if (this.levels[i] >= this.peaks[i]) { this.peaks[i] = this.levels[i]; this.peakHold[i] = t + 420; }
        else if (t > this.peakHold[i]) this.peaks[i] = Math.max(this.levels[i], this.peaks[i] - 0.012);
      }
      let bass = 0;
      for (let i = 0; i < 6; i++) bass += this.levels[i];
      this.bass += (bass / 6 - this.bass) * 0.25;
    }

    _gradient(x0, y0, x1, y1, alpha = 1) {
      const g = this.ctx.createLinearGradient(x0, y0, x1, y1);
      this.colors.forEach((c, i) => g.addColorStop(i / (this.colors.length - 1), rgba(c, alpha)));
      return g;
    }

    _frame(t) {
      if (!this.running) return;
      requestAnimationFrame(this._frame);
      // a paletta lágyan úszik át az új állapot színeibe
      this.colors.forEach((c, i) => { for (let k = 0; k < 3; k++) c[k] += (this.target[i][k] - c[k]) * 0.06; });
      this._measure(t);
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      if (this.opts.bloom) this._bloom();
      if (this.mode === "wave") this._wave();
      else if (this.mode === "radial") this._radial();
      else this._bars();
    }

    _bloom() {
      const ctx = this.ctx, c = this.colors[1];
      const g = ctx.createRadialGradient(this.w / 2, this.h * 0.75, 0, this.w / 2, this.h * 0.75, Math.max(this.w, this.h) * 0.7);
      g.addColorStop(0, rgba(c, 0.05 + this.bass * 0.22));
      g.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, this.w, this.h);
    }

    _bars() {
      const ctx = this.ctx, n = this.opts.bars, W = this.w, H = this.h;
      const top = H * this.opts.top;
      const base = this.opts.reflection ? top + (H - top) * 0.78 : H;
      const room = (base - top) * 0.94;
      const gap = Math.max(1, W / n * 0.28), bw = (W - gap * (n - 1)) / n, r = Math.min(bw / 2, 4);
      const height = (v) => Math.max(2, v * room);
      // vízszintes színátmenet: a mélyektől a magasakig végigfut a paletta
      ctx.save();
      ctx.shadowBlur = this.opts.glow;
      ctx.shadowColor = rgba(this.colors[1], 0.55);
      ctx.fillStyle = this._gradient(0, 0, W, 0);
      for (let i = 0; i < n; i++) roundRect(ctx, i * (bw + gap), base - height(this.levels[i]), bw, height(this.levels[i]), r);
      ctx.restore();
      // üveges fény az oszlopok tetején
      const shine = ctx.createLinearGradient(0, base - room, 0, base);
      shine.addColorStop(0, "rgba(255,255,255,.42)");
      shine.addColorStop(0.55, "rgba(255,255,255,.06)");
      shine.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = shine;
      for (let i = 0; i < n; i++) roundRect(ctx, i * (bw + gap), base - height(this.levels[i]), bw, height(this.levels[i]), r);
      if (this.opts.peaks) {
        ctx.fillStyle = "rgba(255,255,255,.9)";
        for (let i = 0; i < n; i++) roundRect(ctx, i * (bw + gap), base - height(this.peaks[i]) - 5, bw, 2, 1);
      }
      if (this.opts.reflection) {
        ctx.save();
        ctx.globalAlpha = 0.24;
        ctx.fillStyle = this._gradient(0, 0, W, 0, 0.9);
        for (let i = 0; i < n; i++) {
          const h = Math.min(H - base, height(this.levels[i]) * 0.45);
          roundRect(ctx, i * (bw + gap), base + 3, bw, h, r);
        }
        ctx.restore();
        // a tükörkép lefelé elhalványul
        const fade = ctx.createLinearGradient(0, base, 0, H);
        fade.addColorStop(0, "rgba(0,0,0,0)");
        fade.addColorStop(1, this.opts.fadeTo || "rgba(18,21,28,1)");
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = fade;
        ctx.fillRect(0, base, W, H - base);
        ctx.globalCompositeOperation = "source-over";
      }
    }

    _wavePath(values, mid, amp, sign) {
      const ctx = this.ctx, n = values.length, W = this.w;
      ctx.moveTo(0, mid);
      let px = 0, py = mid - sign * values[0] * amp;
      ctx.lineTo(px, py);
      for (let i = 1; i < n; i++) {
        const x = (i / (n - 1)) * W, y = mid - sign * values[i] * amp;
        ctx.quadraticCurveTo(px, py, (px + x) / 2, (py + y) / 2);
        px = x; py = y;
      }
      ctx.lineTo(W, py);
      ctx.lineTo(W, mid);
    }

    _wave() {
      const ctx = this.ctx, H = this.h, W = this.w, top = H * this.opts.top;
      const mid = top + (H - top) / 2, amp = (H - top) * 0.46;
      const fill = this._gradient(0, 0, W, 0, 0.9);
      // lassú utófény a mélységért
      ctx.save();
      ctx.globalAlpha = 0.25;
      ctx.fillStyle = fill;
      ctx.beginPath(); this._wavePath(this.trail, mid, amp * 1.08, 1); this._wavePath(this.trail, mid, amp * 1.08, -1); ctx.fill();
      ctx.restore();
      ctx.save();
      ctx.shadowBlur = this.opts.glow;
      ctx.shadowColor = rgba(this.colors[1], 0.6);
      ctx.fillStyle = fill;
      ctx.globalAlpha = 0.85;
      ctx.beginPath(); this._wavePath(this.levels, mid, amp, 1); this._wavePath(this.levels, mid, amp, -1); ctx.fill();
      ctx.restore();
      ctx.strokeStyle = "rgba(255,255,255,.55)";
      ctx.lineWidth = 1.2;
      ctx.beginPath(); this._wavePath(this.levels, mid, amp, 1); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(W, mid); ctx.strokeStyle = rgba(this.colors[2], 0.35); ctx.stroke();
    }

    _radial() {
      const ctx = this.ctx, n = this.opts.bars, cx = this.w / 2, cy = this.h / 2;
      const R = Math.min(this.w, this.h) * (this.opts.radius || 0.24) * (1 + this.bass * 0.12);
      const len = Math.min(this.w, this.h) * 0.24;
      const half = Math.ceil(n * 0.75); // a legmagasabb sávok kimaradnak, így szimmetrikusabb a kör
      ctx.save();
      ctx.translate(cx, cy);
      ctx.shadowBlur = this.opts.glow;
      ctx.shadowColor = rgba(this.colors[1], 0.6);
      ctx.lineCap = "round";
      ctx.lineWidth = Math.max(2, (Math.PI * R) / half * 0.55);
      for (let side = -1; side <= 1; side += 2) {
        for (let i = 0; i < half; i++) {
          const a = -Math.PI / 2 + side * (i + 0.5) / half * Math.PI;
          const l = 3 + this.levels[i] * len;
          const c = this.colors[Math.min(2, Math.floor((i / half) * 3))];
          ctx.strokeStyle = rgba(c, 0.95);
          ctx.beginPath();
          ctx.moveTo(Math.cos(a) * R, Math.sin(a) * R);
          ctx.lineTo(Math.cos(a) * (R + l), Math.sin(a) * (R + l));
          ctx.stroke();
        }
      }
      ctx.shadowBlur = 0;
      ctx.lineWidth = 2;
      ctx.strokeStyle = rgba(this.colors[2], 0.35 + this.bass * 0.5);
      ctx.beginPath(); ctx.arc(0, 0, R - 8, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
    ctx.fill();
  }

  window.AiradioSpectrum = Spectrum;
})();
