const { chromium } = require('playwright');
const { mkdtempSync, renameSync, rmSync, writeFileSync } = require('node:fs');
const { dirname, join } = require('node:path');

async function login(courseUrl, file) {
  const course = new URL(courseUrl);
  const courseId = course.searchParams.get('id');
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ locale: 'fr-FR' });
  const page = await context.newPage();
  console.log('Connecte-toi à Centrale Nantes dans la fenêtre ouverte.');

  try {
    await page.goto(courseUrl, { waitUntil: 'commit' });
    const deadline = Date.now() + 600_000;
    while (Date.now() < deadline && !page.isClosed()) {
      const current = new URL(page.url());
      if (current.origin === course.origin && current.pathname === course.pathname) {
        const selector = courseId ? `body.course-${courseId}:not(.notloggedin) #region-main` : 'body:not(.notloggedin) #region-main';
        const ready = await page.locator(selector).count();
        const denied = await page.locator('#region-main .alert-danger, #notice').count();
        if (ready && !denied) {
          const cookies = await context.cookies([course.origin + '/', 'https://sso.ec-nantes.fr/cas/login']);
          if (cookies.some(cookie => cookie.name === 'MoodleSession' && cookie.domain === course.hostname)) {
            const directory = mkdtempSync(join(dirname(file), '.login-'));
            try {
              const temporary = join(directory, 'session.json');
              writeFileSync(temporary, JSON.stringify(cookies), { mode: 0o600 });
              renameSync(temporary, file);
            } finally {
              rmSync(directory, { recursive: true });
            }
            console.log('Session Hippocampus enregistrée.');
            return;
          }
        }
      }
      await new Promise(resolve => setTimeout(resolve, 750));
    }
    process.exitCode = 1;
    console.error('Connexion au cours non confirmée.');
  } finally {
    await browser.close();
  }
}

login(process.argv[2], process.argv[3]).catch(() => {
  console.error('La fenêtre de connexion a été fermée ou la connexion a été interrompue.');
  process.exitCode = 1;
});
