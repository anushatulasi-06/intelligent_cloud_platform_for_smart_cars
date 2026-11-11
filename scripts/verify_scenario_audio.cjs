// Focused browser audio reliability checks. Uses real authenticated WAV downloads
// and real Chromium media playback; command POSTs are intercepted for isolation.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [path.join(__dirname, '../frontend')] }));
const BASE = process.env.UI_BASE_URL || 'http://localhost:5173';
const OUTPUT = path.resolve(process.env.UI_EVIDENCE_DIR || path.join(__dirname, '../docs/deliverable3/evidence/ui'));

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}),
    args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    const errors = [], audioRequests = [];
    let commandAttempts = 0, rejectOneDownload = false;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (/\/simulator\/scenarios\/[^/]+\/audio$/.test(new URL(request.url()).pathname)) {
        audioRequests.push({ path: new URL(request.url()).pathname,
          bearer_header: /^Bearer /.test(request.headers().authorization || ''),
          query: new URL(request.url()).search });
      }
    });
    await page.addInitScript(() => {
      const realPlay = HTMLMediaElement.prototype.play;
      let blockOnce = true;
      HTMLMediaElement.prototype.play = function () {
        if (blockOnce && this.getAttribute('aria-label') === 'Scenario preview audio') {
          blockOnce = false;
          return Promise.reject(new DOMException('Autoplay blocked for focused test', 'NotAllowedError'));
        }
        return realPlay.call(this);
      };
    });
    await page.route('**/devices/*/commands', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      commandAttempts += 1;
      return route.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ command_id: 900000 + commandAttempts, status: 'queued' }) });
    });
    await page.route('**/simulator/scenarios/*/audio', async route => {
      if (!rejectOneDownload) return route.continue();
      rejectOneDownload = false;
      return route.fulfill({ status: 503, contentType: 'application/json',
        body: JSON.stringify({ detail: 'Scenario recording temporarily unavailable' }) });
    });
    await page.goto(BASE + '/login');
    await page.getByLabel('Email', { exact: true }).fill('iot@autosound.dev');
    await page.getByLabel('Password', { exact: true }).fill('iot-dev-pass');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/devices');
    await page.getByRole('link', { name: 'Simulator', exact: true }).click();
    const siren = page.getByRole('button', { name: /Siren/ });
    await page.waitForFunction(() => !document.querySelector('.sim-scenario-emergency')?.disabled);
    await siren.click();
    await page.getByRole('button', { name: 'Play preview', exact: true }).waitFor();
    const audio = page.getByLabel('Scenario preview audio');
    assert.equal(await audio.evaluate(element => element.paused), true);
    await page.getByRole('button', { name: 'Play preview', exact: true }).click();
    await page.waitForFunction(() => { const audio = document.querySelector('.sim-audio-preview audio');
      return audio.currentTime > 0.25 && audio.duration === 5 && audio.src.startsWith('blob:') && !audio.muted && audio.volume > 0; });
    assert.equal(commandAttempts, 1);

    // A download error still submits the independent scenario command. Retrying
    // the preview performs no extra command POST and then uses actual WAV bytes.
    rejectOneDownload = true;
    await page.getByRole('button', { name: /Horn/ }).click();
    await page.getByRole('button', { name: 'Retry preview', exact: true }).waitFor();
    assert.equal(commandAttempts, 2);
    await page.getByRole('button', { name: 'Retry preview', exact: true }).click();
    await page.waitForFunction(() => { const audio = document.querySelector('.sim-audio-preview audio');
      return audio.currentTime > 0.25 && audio.duration === 5 && !audio.paused; });
    assert.equal(commandAttempts, 2);
    assert(audioRequests.length >= 3);
    assert(audioRequests.every(request => request.bearer_header && request.query === ''));
    assert.deepEqual(errors, []);
    fs.mkdirSync(OUTPUT, { recursive: true });
    fs.writeFileSync(path.join(OUTPUT, 'scenario-audio-checks.json'), JSON.stringify({
      author: 'Rishi Sheth', checked_at: new Date().toISOString(), base_url: BASE,
      scope: 'Focused browser reliability; command POSTs intercepted, no vehicle or data mutations',
      checks: ['real authenticated WAV download', 'real unmuted 5-second media playback',
        'autoplay rejection exposes Play preview', 'audio failure preserves command attempt',
        'preview retry creates no duplicate command', 'Bearer header, no token in audio URL'],
      audio_requests: audioRequests, command_attempts_intercepted: commandAttempts, page_errors: errors,
    }, null, 2));
    console.log('PASS: actual WAV playback, autoplay fallback, download-error retry, independent command attempt and header-only JWT. Command POSTs intercepted for isolation.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
