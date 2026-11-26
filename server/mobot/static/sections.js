const sectionHeader = document.querySelector('.site-header');
const sectionMenu = sectionHeader.querySelector('nav');
const sectionLinks = sectionMenu.querySelectorAll('[data-section-link]');
const homeSections = [...document.querySelectorAll('[data-nav-section]')];
let sectionFrame;
let sectionScroll;

function updateSectionNavigation() {
  sectionFrame = undefined;
  let activeSection = sectionScroll?.target;

  if (!activeSection && homeSections[0]?.getClientRects().length) {
    activeSection = homeSections[0];
    const marker = sectionHeader.getBoundingClientRect().bottom + 12;
    for (const section of homeSections) {
      if (section.getBoundingClientRect().top > marker) break;
      activeSection = section;
    }

    const reachedEnd = window.scrollY > 0 &&
      window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
    if (reachedEnd) activeSection = homeSections[homeSections.length - 1];
  }

  let activeLink;
  for (const link of sectionLinks) {
    if (link.dataset.sectionLink === activeSection?.id) {
      link.setAttribute('aria-current', 'location');
      activeLink = link;
    } else {
      link.removeAttribute('aria-current');
    }
  }

  if (activeLink) {
    sectionMenu.style.setProperty('--section-indicator-x', `${activeLink.offsetLeft}px`);
    sectionMenu.style.setProperty('--section-indicator-width', `${activeLink.offsetWidth}px`);
  }
  sectionMenu.dataset.sectionIndicator = activeLink ? 'visible' : 'hidden';
}

function scheduleSectionNavigation() {
  if (sectionFrame === undefined) sectionFrame = requestAnimationFrame(updateSectionNavigation);
}

function stopSectionScroll() {
  if (sectionScroll) {
    cancelAnimationFrame(sectionScroll.frame);
    sectionScroll = undefined;
  }
  scheduleSectionNavigation();
}

function scrollToSection(target) {
  stopSectionScroll();
  const start = window.scrollY;
  const margin = parseFloat(getComputedStyle(target).scrollMarginBlockStart);
  const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  const destination = Math.max(0, Math.min(maxScroll, start + target.getBoundingClientRect().top - margin));
  const distance = destination - start;

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || Math.abs(distance) < 2) {
    window.scrollTo({ top: destination, behavior: 'instant' });
    return;
  }

  const startedAt = performance.now();
  sectionScroll = { target };
  function animate(now) {
    const progress = Math.min(1, Math.max(0, (now - startedAt) / 360));
    const eased = progress * progress * (3 - 2 * progress);
    window.scrollTo({ top: start + distance * eased, behavior: 'instant' });
    if (progress < 1) sectionScroll.frame = requestAnimationFrame(animate);
    else stopSectionScroll();
  }
  sectionScroll.frame = requestAnimationFrame(animate);
}

sectionMenu.addEventListener('click', event => {
  const link = event.target.closest('[data-section-link]');
  if (!link || event.defaultPrevented || event.button !== 0 ||
      event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;

  const target = homeSections.find(section => section.id === link.dataset.sectionLink);
  if (!target?.getClientRects().length) return;

  event.preventDefault();
  if (location.hash !== link.hash) history.pushState(null, '', link.hash);
  scrollToSection(target);
});

window.addEventListener('scroll', scheduleSectionNavigation, { passive: true });
window.addEventListener('resize', stopSectionScroll);
window.addEventListener('hashchange', stopSectionScroll);
window.addEventListener('pageshow', stopSectionScroll);
window.addEventListener('wheel', stopSectionScroll, { passive: true });
window.addEventListener('touchstart', stopSectionScroll, { passive: true });
window.addEventListener('keydown', event => {
  if (sectionScroll && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) {
    stopSectionScroll();
  }
});
scheduleSectionNavigation();
