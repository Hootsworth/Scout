/* ════════════════════════════════════════════════════════
   scout-sidebar.js — Shared sidebar logic for all pages
   Theme toggle · Deadline chip · Command palette
   ════════════════════════════════════════════════════════ */

// ── Theme Toggle ──────────────────────────────────────────
(function() {
  const saved = localStorage.getItem('scout_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
})();

document.addEventListener('DOMContentLoaded', function() {
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) {
    btn.addEventListener('click', function() {
      const current = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('scout_theme', next);
    });
  }

  // ── Placement Deadline Chip ────────────────────────────
  (function() {
    const deadline = localStorage.getItem('placement_deadline');
    const chip = document.getElementById('sidebar-deadline-chip');
    if (!chip || !deadline) return;
    const days = Math.ceil((new Date(deadline) - new Date()) / (1000*60*60*24));
    if (isNaN(days)) return;
    let color = '#4ade80', bg = 'rgba(74,222,128,0.1)';
    let pulse = '';
    if (days <= 3)  { color = '#f87171'; bg = 'rgba(248,113,113,0.15)'; pulse = 'animation:countdown-pulse 1.2s ease-in-out infinite;'; }
    else if (days <= 7)  { color = '#f87171'; bg = 'rgba(248,113,113,0.1)'; }
    else if (days <= 30) { color = '#fbbf24'; bg = 'rgba(251,191,36,0.1)'; }
    chip.style.cssText = `display:flex;align-items:center;gap:8px;padding:7px 11px;border-radius:10px;background:${bg};width:100%;margin-bottom:4px;${pulse}`;
    chip.innerHTML = `<span style="font-size:15px;flex-shrink:0;">📅</span><span class="sidebar-label" style="color:${color};font-size:11.5px;font-weight:700;letter-spacing:0.02em;">${days > 0 ? days + 'd left' : days === 0 ? 'Today!' : 'Expired'}</span>`;
  })();

  // ── Command Palette ────────────────────────────────────
  const COMMANDS = [
    { label: 'New Chat', icon: '💬', desc: 'Start a new conversation', href: 'index.html' },
    { label: 'Placements Hub', icon: '💼', desc: 'Manage applications & OA', href: 'placements.html' },
    { label: 'Self-Study Hub', icon: '📚', desc: 'Study topics & flashcards', href: 'study.html' },
    { label: 'Interview Simulator', icon: '🎙️', desc: 'Mock interviews & voice HR', href: 'simulator.html' },
    { label: 'Settings', icon: '⚙️', desc: 'API keys & personalization', href: 'settings.html' },
    { label: 'Toggle Theme', icon: '🌙', desc: 'Switch dark / light mode', action: () => document.getElementById('theme-toggle-btn')?.click() },
    { label: 'Mock OA Arena', icon: '🧑‍💻', desc: 'Launch a timed coding challenge', href: 'placements.html#mock-oa' },
    { label: 'Academic Assistant', icon: '🤖', desc: 'Personalized AI study coach', href: 'assistant.html' },
  ];

  let selIdx = 0;

  function renderCommands(query) {
    const list = document.getElementById('cmd-palette-list');
    if (!list) return;
    const filtered = query
      ? COMMANDS.filter(c => c.label.toLowerCase().includes(query.toLowerCase()) || c.desc.toLowerCase().includes(query.toLowerCase()))
      : COMMANDS;
    selIdx = 0;
    list.innerHTML = filtered.length ? filtered.map((cmd, i) => `
      <div class="cmd-item ${i === 0 ? 'selected' : ''}" data-idx="${i}" onclick="window.__cmdRun(${COMMANDS.indexOf(cmd)})">
        <span class="cmd-icon">${cmd.icon}</span>
        <div style="flex:1;min-width:0;">
          <div class="cmd-label">${cmd.label}</div>
          <div style="font-size:11.5px;color:var(--mute);margin-top:1px;">${cmd.desc}</div>
        </div>
        <span class="cmd-shortcut">↵</span>
      </div>
    `).join('') : `<div style="padding:24px;text-align:center;color:var(--mute);font-size:14px;">No results for "${query}"</div>`;
    window.__filteredCmds = filtered;
  }

  window.__cmdRun = function(globalIdx) {
    const cmd = COMMANDS[globalIdx];
    if (!cmd) return;
    closePalette();
    if (cmd.action) { cmd.action(); }
    else if (cmd.href) { window.location.href = cmd.href; }
  };

  function openPalette() {
    const overlay = document.getElementById('cmd-palette');
    if (!overlay) return;
    overlay.classList.add('open');
    const input = document.getElementById('cmd-palette-input');
    if (input) { input.value = ''; input.focus(); }
    renderCommands('');
  }

  function closePalette() {
    document.getElementById('cmd-palette')?.classList.remove('open');
  }

  window.openCommandPalette = openPalette;
  window.closeCommandPalette = closePalette;

  // Keyboard
  document.addEventListener('keydown', function(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); openPalette(); }
    if (e.key === 'Escape') closePalette();
  });

  // Input handler
  const input = document.getElementById('cmd-palette-input');
  if (input) {
    input.addEventListener('input', function() { renderCommands(this.value); selIdx = 0; });
    input.addEventListener('keydown', function(e) {
      const items = document.querySelectorAll('#cmd-palette-list .cmd-item');
      if (!items.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selIdx = Math.min(selIdx + 1, items.length - 1);
        items.forEach((el, i) => el.classList.toggle('selected', i === selIdx));
        items[selIdx]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selIdx = Math.max(selIdx - 1, 0);
        items.forEach((el, i) => el.classList.toggle('selected', i === selIdx));
        items[selIdx]?.scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        e.preventDefault();
        items[selIdx]?.click();
      }
    });
  }

  // Click outside
  document.getElementById('cmd-palette')?.addEventListener('click', function(e) {
    if (e.target === this) closePalette();
  });

  // Register PWA Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js')
        .then(reg => console.log('Scout PWA Service Worker registered:', reg.scope))
        .catch(err => console.error('Scout PWA Service Worker registration failed:', err));
    });
  }
});
