const TELEGRAM = window.Telegram?.WebApp ?? null;

const DEFAULT_THEME = {
  bg_color: '#10131a',
  secondary_bg_color: 'rgba(255, 255, 255, 0.08)',
  text_color: '#f7f9fb',
  hint_color: '#6dc8ff',
};

const GAME_DURATION = 60; // seconds
const ORB_LIFETIME = 2200; // ms
const HINT_TIMEOUT = 2500;

const SPAWN_CONFIG = [
  { threshold: 0, interval: 1200 },
  { threshold: 20, interval: 900 },
  { threshold: 40, interval: 750 },
  { threshold: 55, interval: 600 },
];

const ORB_TYPES = [
  { name: 'common', weight: 0.7, score: 1 },
  { name: 'rare', weight: 0.2, score: 3 },
  { name: 'bonus', weight: 0.1, score: 5, extraTime: 2 },
];

class Haptics {
  constructor(telegram) {
    this.supportsTelegramHaptics = Boolean(telegram?.HapticFeedback);
    this.supportsVibration = 'vibrate' in navigator;
    this.telegram = telegram;
  }

  impact(style = 'light') {
    if (this.supportsTelegramHaptics) {
      this.telegram.HapticFeedback.impactOccurred(style);
    } else if (this.supportsVibration) {
      navigator.vibrate?.(style === 'heavy' ? 24 : 12);
    }
  }

  notification(type = 'success') {
    if (this.supportsTelegramHaptics) {
      this.telegram.HapticFeedback.notificationOccurred(type);
    } else if (this.supportsVibration) {
      navigator.vibrate?.([20, 10, 20]);
    }
  }
}

class SkyOrchardGame {
  constructor({
    area,
    scoreEl,
    timerEl,
    overlay,
    finalScoreEl,
    restartButton,
    hint,
    telegram,
  }) {
    this.area = area;
    this.scoreEl = scoreEl;
    this.timerEl = timerEl;
    this.overlay = overlay;
    this.finalScoreEl = finalScoreEl;
    this.restartButton = restartButton;
    this.hint = hint;
    this.telegram = telegram;
    this.haptics = new Haptics(telegram);

    this.score = 0;
    this.timeLeft = GAME_DURATION;
    this.spawnTimeout = null;
    this.timerInterval = null;
    this.orbId = 0;
    this.isRunning = false;
    this.hintTimeoutId = null;
    this.telegramShareHandler = null;
    this.telegramBackHandler = null;

    this.boundHandleOrbTap = this.handleOrbTap.bind(this);
    this.handleVisibilityChange = this.handleVisibilityChange.bind(this);

    this.setupListeners();
    this.applyReducedMotionPreference();
  }

  setupListeners() {
    this.area.addEventListener('pointerdown', (event) => {
      const target = event.target.closest('.orb');
      if (!target) return;
      this.boundHandleOrbTap(event, target);
    });

    this.restartButton.addEventListener('click', () => this.start());
    document.addEventListener('visibilitychange', this.handleVisibilityChange);
  }

  handleVisibilityChange() {
    if (document.visibilityState === 'hidden') {
      this.pause();
    } else if (this.isRunning) {
      this.resume();
    }
  }

  applyReducedMotionPreference() {
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    if (reduceMotion?.matches) {
      document.body.classList.add('reduced-motion');
    }
    reduceMotion?.addEventListener('change', (event) => {
      document.body.classList.toggle('reduced-motion', event.matches);
    });
  }

  start() {
    this.resetState();
    this.isRunning = true;
    this.overlay.hidden = true;
    this.spawnLoop();
    this.startTimer();

    window.clearTimeout(this.hintTimeoutId);
    this.hint.classList.add('visible');
    this.hintTimeoutId = window.setTimeout(() => this.hint.classList.remove('visible'), HINT_TIMEOUT);

    this.haptics.notification('success');
    const mainButton = this.telegram?.MainButton;
    const backButton = this.telegram?.BackButton;

    if (this.telegramShareHandler) {
      mainButton?.offClick?.(this.telegramShareHandler);
      this.telegramShareHandler = null;
    }
    if (this.telegramBackHandler) {
      backButton?.offClick?.(this.telegramBackHandler);
      this.telegramBackHandler = null;
    }
    mainButton?.hide?.();
    backButton?.hide?.();
  }

  resetState() {
    this.score = 0;
    this.timeLeft = GAME_DURATION;
    this.updateScore(0);
    this.updateTimer(this.timeLeft);
    this.area.querySelectorAll('.orb').forEach((orb) => orb.remove());

    window.clearTimeout(this.spawnTimeout);
    window.clearInterval(this.timerInterval);
    this.spawnTimeout = null;
    this.timerInterval = null;
  }

  spawnLoop() {
    if (!this.isRunning) return;
    this.spawnOrb();

    const current = SPAWN_CONFIG.reduce((acc, cfg) => (this.score >= cfg.threshold ? cfg : acc), SPAWN_CONFIG[0]);
    const jitter = Math.random() * 220 - 110;
    const delay = Math.max(320, current.interval + jitter);

    this.spawnTimeout = window.setTimeout(() => this.spawnLoop(), delay);
  }

  spawnOrb() {
    const orb = document.createElement('button');
    orb.type = 'button';
    orb.className = 'orb';
    orb.dataset.id = `${++this.orbId}`;

    const type = this.pickOrbType();
    orb.dataset.type = type.name;
    orb.textContent = `+${type.score}`;

    this.area.append(orb);

    const { clientWidth: width, clientHeight: height } = this.area;
    const orbWidth = orb.offsetWidth;
    const orbHeight = orb.offsetHeight;

    const maxLeft = Math.max(0, width - orbWidth);
    const maxTop = Math.max(0, height - orbHeight);

    const left = Math.random() * maxLeft;
    const top = Math.random() * maxTop * 0.8; // keep top 80%

    orb.style.left = `${left}px`;
    orb.style.top = `${top}px`;

    const lifetime = ORB_LIFETIME + Math.random() * 600;

    orb.dataset.expiresAt = `${Date.now() + lifetime}`;

    window.setTimeout(() => this.removeOrb(orb, true), lifetime);
  }

  pickOrbType() {
    const roll = Math.random();
    let cumulative = 0;
    for (const type of ORB_TYPES) {
      cumulative += type.weight;
      if (roll <= cumulative) {
        return type;
      }
    }
    return ORB_TYPES[0];
  }

  handleOrbTap(event, orb) {
    event.preventDefault();
    if (!this.isRunning || orb.dataset.captured === 'true') return;
    orb.dataset.captured = 'true';

    const type = ORB_TYPES.find((t) => t.name === orb.dataset.type) ?? ORB_TYPES[0];
    this.collectOrb(orb, type);
  }

  collectOrb(orb, type) {
    this.haptics.impact(type.name === 'bonus' ? 'heavy' : 'medium');
    this.incrementScore(type.score);

    if (type.extraTime) {
      this.timeLeft = Math.min(GAME_DURATION, this.timeLeft + type.extraTime);
      this.updateTimer(this.timeLeft);
    }

    orb.classList.add('collected');
    window.setTimeout(() => orb.remove(), 220);
  }

  removeOrb(orb, expired) {
    if (!orb.isConnected) return;
    if (orb.dataset.captured === 'true') return;
    orb.remove();
    if (expired && this.isRunning) {
      this.haptics.impact('light');
    }
  }

  incrementScore(amount) {
    this.score += amount;
    this.updateScore(this.score);
  }

  updateScore(value) {
    this.scoreEl.textContent = value.toString();
  }

  startTimer() {
    const tick = () => {
      this.timeLeft -= 1;
      this.updateTimer(this.timeLeft);
      if (this.timeLeft <= 0) {
        this.finish();
      }
    };

    this.timerInterval = window.setInterval(tick, 1000);
  }

  updateTimer(value) {
    this.timerEl.textContent = Math.max(0, Math.ceil(value)).toString();
  }

  pause() {
    if (!this.isRunning) return;
    window.clearTimeout(this.spawnTimeout);
    window.clearInterval(this.timerInterval);
    this.spawnTimeout = null;
    this.timerInterval = null;
  }

  resume() {
    if (!this.isRunning) return;
    this.spawnLoop();
    this.startTimer();
  }

  finish() {
    this.isRunning = false;
    window.clearTimeout(this.spawnTimeout);
    window.clearInterval(this.timerInterval);
    this.spawnTimeout = null;
    this.timerInterval = null;

    this.overlay.hidden = false;
    this.finalScoreEl.textContent = `Вы собрали ${this.score} энергии`;

    this.haptics.notification('success');
    this.configureTelegramButtons();
  }

  configureTelegramButtons() {
    if (!this.telegram) return;

    const mainButton = this.telegram.MainButton;
    const backButton = this.telegram.BackButton;

    if (this.telegramShareHandler) {
      mainButton?.offClick?.(this.telegramShareHandler);
    }
    if (this.telegramBackHandler) {
      backButton?.offClick?.(this.telegramBackHandler);
    }

    const shareScore = () => {
      const payload = JSON.stringify({ score: this.score, timestamp: Date.now() });
      this.telegram.sendData?.(payload);
      this.telegram.close?.();
    };

    this.telegramShareHandler = shareScore;
    this.telegramBackHandler = () => {
      this.telegram.close?.();
    };

    mainButton?.setParams?.({
      text: `Поделиться ${this.score} ⚡️`,
      is_active: true,
      is_visible: true,
    });

    mainButton?.onClick?.(shareScore);

    backButton?.show?.();
    backButton?.onClick?.(this.telegramBackHandler);
  }
}

function applyTelegramTheme(telegram) {
  if (!telegram) return;

  const params = telegram.themeParams ?? {};
  const theme = { ...DEFAULT_THEME, ...params };

  const root = document.documentElement;
  root.style.setProperty('--bg-color', theme.bg_color);
  root.style.setProperty('--bg-color-secondary', theme.secondary_bg_color);
  root.style.setProperty('--text-color', theme.text_color);
  root.style.setProperty('--accent-color', theme.hint_color ?? DEFAULT_THEME.hint_color);

  telegram.setHeaderColor?.('secondary_bg_color');
  telegram.setBackgroundColor?.('bg_color');
}

function initTelegram() {
  if (!TELEGRAM) return;

  TELEGRAM.ready();
  TELEGRAM.expand();
  applyTelegramTheme(TELEGRAM);
}

function init() {
  initTelegram();

  const app = document.getElementById('app');
  if (!app) return;

  const game = new SkyOrchardGame({
    area: document.getElementById('game-area'),
    scoreEl: document.getElementById('score'),
    timerEl: document.getElementById('timer'),
    overlay: document.getElementById('overlay'),
    finalScoreEl: document.getElementById('final-score'),
    restartButton: document.getElementById('restart'),
    hint: document.getElementById('touch-hint'),
    telegram: TELEGRAM,
  });

  window.gameInstance = game; // helpful for debugging

  game.start();
}

document.addEventListener('DOMContentLoaded', init);
