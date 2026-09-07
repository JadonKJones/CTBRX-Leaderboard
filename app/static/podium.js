// renderPodium(container, items) - builds the same markup as the Jinja podium() macro.
// items: [{rank, name, href, value, sub, avatar, flagHtml}]
(function () {
  function flag(cc) {
    return cc && cc !== 'XX'
      ? `<img class="flag" src="https://flagcdn.com/24x18/${cc.toLowerCase()}.png" width="24" height="18" alt="${cc}"> `
      : '';
  }
  window.podiumFlag = flag;

  window.renderPodium = function (container, items) {
    if (!container) return;
    if (!items || !items.length) { container.innerHTML = ''; return; }
    container.className = 'podium';
    container.innerHTML = items.slice(0, 3).map(function (it) {
      return `
        <a class="podium__slot podium__slot--${it.rank}" ${it.href ? `href="${it.href}"` : ''}>
          <div class="podium__pos">#${it.rank}</div>
          ${it.avatar ? `<img class="podium__avatar" src="${it.avatar}" alt="">` : ''}
          <div class="podium__name">${it.flagHtml || ''}${it.name}</div>
          <div class="podium__value">${it.value}</div>
          ${it.sub ? `<div class="podium__sub muted">${it.sub}</div>` : ''}
        </a>`;
    }).join('');
  };
})();
