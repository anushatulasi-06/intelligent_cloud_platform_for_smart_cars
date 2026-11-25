// Real recorded-CARLA UI/API regression. Run only in verify_local.py's loopback-only namespace.
// Five actual commands/uploads/classifications are followed by clearly separated UI fault injections.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [path.join(__dirname, '../frontend')] }));

function localUrl(value) {
  const url = new URL(value);
  assert(url.protocol === 'http:' && url.hostname === '127.0.0.1' && !url.username && !url.password && !url.hash,
    'Only literal loopback HTTP URLs are permitted');
  return value.replace(/\/$/, '');
}
const UI = localUrl(process.env.UI_BASE_URL || 'http://127.0.0.1:15173');
const API = localUrl(process.env.API_BASE_URL || 'http://127.0.0.1:18000');
const OUTPUT = path.resolve(process.env.UI_EVIDENCE_DIR || path.join(__dirname, '../.local_evidence/replay'));
const CAR = Number(process.env.VERIFY_CAR_ID || 1);
const EDGE = path.join(__dirname, '../backend/edge');
const manifest = JSON.parse(fs.readFileSync(path.join(EDGE, 'demo_clips/manifest.json')));
const scenarios = [
  ['emergency', 'Siren', 'emergency', null], ['in_vehicle', 'Glass / crash', 'in_vehicle_crash', 6],
  ['human', 'Scream', 'human_pullover', 15], ['traffic', 'Road traffic', 'traffic', 6],
  ['environmental', 'Thunderstorm', 'environmental', 0],
];
const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const evidence = { author: 'Rishi Sheth', started_at: new Date().toISOString(), result: 'RUNNING',
  scope: 'Isolated local recorded-CARLA replay; real API, C3 storage and model; separate preview fault injections',
  not_verified: ['Live CARLA server or physical vehicle', 'AWS deployment or SES delivery', 'Held-out model accuracy'],
  scenarios: [], preview_fault_injections: [], page_errors: [] };
function safeError(error) {
  return String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '<jwt>')
    .replace(/X-Amz-Signature=[a-f0-9]{64}/gi, 'X-Amz-Signature=<signature>');
}

async function until(label, probe, timeout = 60000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await probe();
    if (value) return value;
    await delay(300);
  }
  throw new Error(`Timed out: ${label}`);
}

(async () => {
  assert(process.platform === 'linux', 'Linux network isolation is required');
  const interfaces = fs.readFileSync('/proc/net/dev', 'utf8').split('\n').slice(2)
    .filter(row => row.includes(':')).map(row => row.split(':')[0].trim());
  assert.deepEqual(interfaces, ['lo'], 'Refusing mutation outside a loopback-only network namespace');
  assert(Number.isSafeInteger(CAR) && CAR > 0, 'Invalid car ID');
  fs.mkdirSync(OUTPUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1080 } });
    page = await context.newPage();
    page.on('pageerror', error => evidence.page_errors.push(error.message));
    await page.addInitScript(() => {
      window.__qaPreviewEvents = [];
      window.__qaBlockPreviewOnce = false;
      const play = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function () {
        if (window.__qaBlockPreviewOnce && this.getAttribute('aria-label') === 'Scenario preview audio') {
          window.__qaBlockPreviewOnce = false;
          return Promise.reject(new DOMException('Focused autoplay test', 'NotAllowedError'));
        }
        return play.call(this);
      };
      document.addEventListener('play', event => {
        if (event.target.getAttribute('aria-label') === 'Scenario preview audio') {
          window.__qaPreviewEvents.push({ at: Date.now(), duration: event.target.duration,
            blob: event.target.src.startsWith('blob:'), muted: event.target.muted, volume: event.target.volume });
        }
      }, true);
    });
    async function api(method, route, token, data, expected = 200) {
      let response;
      try {
        response = await context.request.fetch(API + route, { method, data,
          headers: token ? { Authorization: `Bearer ${token}` } : {}, maxRedirects: 0 });
      } catch { throw new Error(`Local API connection failed: ${method} ${route}`); }
      assert.equal(response.status(), expected, `${method} ${route}: unexpected status`);
      return response;
    }
    async function body(route, token) { return (await api('GET', route, token)).json(); }
    async function bytes(url) {
      localUrl(url);
      let response;
      try { response = await context.request.get(url, { maxRedirects: 0 }); }
      catch { throw new Error('Local signed object download failed (URL omitted)'); }
      assert.equal(response.status(), 200, 'Local object download failed');
      return response.body();
    }
    async function login(role) {
      const password = process.env[`VERIFY_${role.toUpperCase()}_PASSWORD`] || `${role}-dev-pass`;
      const email = process.env[`VERIFY_${role.toUpperCase()}_EMAIL`] || `${role}@autosound.dev`;
      await page.goto(UI + '/login');
      await page.getByLabel('Email', { exact: true }).fill(email);
      await page.getByLabel('Password', { exact: true }).fill(password);
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await page.waitForURL(role === 'iot' ? '**/devices' : '**/dashboard');
      const token = await page.evaluate(() => JSON.parse(sessionStorage.getItem('smartcar.session')).token);
      assert(token, 'JWT was not saved');
      return token;
    }
    const iot = await login('iot');
    const cloudLogin = await api('POST', '/login', null, {
      email: process.env.VERIFY_CLOUD_EMAIL || 'cloud@autosound.dev',
      password: process.env.VERIFY_CLOUD_PASSWORD || 'cloud-dev-pass' });
    const cloud = (await cloudLogin.json()).access_token;
    await page.goto(UI + `/simulator?car_id=${CAR}`);
    await page.locator('.sim-replay-notice').waitFor();
    assert.equal(await page.locator('.sim-source').innerText(), 'CARLA recorded replay');
    await until('recorded replay feed', async () => {
      const state = await body(`/simulator/${CAR}/state`, iot);
      return state.edge_mode === 'carla_replay' && state.online && state.frame_url && state;
    });
    assert.equal(await page.locator('.sim-live').innerText(), 'RECORDED REPLAY');
    const devices = await body(`/devices?car_id=${CAR}&page_size=100`, iot);
    const mic = devices.items.find(device => device.device_type === 'microphone' && device.firmware === 'carla-replay-v1');
    assert(mic, 'A replay microphone must be connected');
    async function commandButton(button) {
      const responsePromise = page.waitForResponse(response => response.url() === API + `/devices/${mic.id}/commands`
        && response.request().method() === 'POST');
      await button.click();
      const response = await responsePromise;
      assert.equal(response.status(), 202, 'UI command was not accepted');
      return (await response.json()).command_id;
    }
    async function acknowledged(id) {
      return until(`command ${id} acknowledgement`, async () => {
        const rows = await body(`/devices/${mic.id}/commands?limit=100`, iot);
        const row = rows.find(item => item.command_id === id);
        assert(!row || !['failed', 'expired'].includes(row.status), 'A real replay command failed or expired');
        return row?.status === 'acknowledged' && row;
      });
    }
    for (const [category, label, recordingName, override] of scenarios) {
      evidence.phase = `real scenario: ${category}`;
      const folder = path.join(EDGE, 'carla_recordings', recordingName);
      const metadata = JSON.parse(fs.readFileSync(path.join(folder, 'scenario.json')));
      const firstTime = Number(fs.readFileSync(path.join(folder, 'telemetry.csv'), 'utf8').split(/\r?\n/)[1].split(',')[0]);
      const eventSeconds = override ?? Math.max(0, metadata.events[0].t_s - firstTime);
      const frameHashes = new Set(fs.readdirSync(path.join(folder, 'frames')).filter(name => name.endsWith('.jpg'))
        .map(name => sha(fs.readFileSync(path.join(folder, 'frames', name)))));
      const clip = manifest.clips.find(item => item.scenario === category);
      const before = new Set((await body(`/device-data?car_id=${CAR}&device_id=${mic.id}&page_size=100`, iot)).items.map(row => row.record_id));
      const playsBefore = await page.evaluate(() => window.__qaPreviewEvents.length);
      const sentAt = Date.now();
      const id = await commandButton(page.locator(`.sim-scenario-${category}`));
      const firstFrame = await until(`${category} first recorded frame`, async () => {
        const state = await body(`/simulator/${CAR}/state`, iot);
        if (!state.frame_url) return false;
        const hash = sha(await bytes(state.frame_url));
        return frameHashes.has(hash) && { hash, position: state.position };
      });
      const nextFrame = await until(`${category} changing frame and GPS`, async () => {
        const state = await body(`/simulator/${CAR}/state`, iot);
        const hash = sha(await bytes(state.frame_url));
        const moved = state.position.lat !== firstFrame.position.lat || state.position.lon !== firstFrame.position.lon;
        return hash !== firstFrame.hash && frameHashes.has(hash) && moved && { hash, position: state.position };
      }, 12000);
      let pausedMilliseconds = 0;
      if (category === 'human') {
        const pause = await commandButton(page.getByRole('button', { name: 'Stop route', exact: true }));
        await acknowledged(pause);
        const stopped = await until('paused replay telemetry', async () => {
          const state = await body(`/simulator/${CAR}/state`, iot);
          return state.position.speed_kmh === 0 && state;
        });
        const stoppedHash = sha(await bytes(stopped.frame_url));
        const pausedAt = Date.now();
        await delay(2400);
        const still = await body(`/simulator/${CAR}/state`, iot);
        assert.equal(still.position.lat, stopped.position.lat);
        assert.equal(still.position.lon, stopped.position.lon);
        assert.equal(sha(await bytes(still.frame_url)), stoppedHash);
        assert.equal(await page.evaluate(() => window.__qaPreviewEvents.length), playsBefore, 'Sound played before the paused event');
        const resume = await commandButton(page.getByRole('button', { name: 'Start route', exact: true }));
        await acknowledged(resume);
        pausedMilliseconds = Date.now() - pausedAt;
      }
      const command = await acknowledged(id);
      const ackAt = Date.parse(command.acked_at);
      assert(ackAt - sentAt >= eventSeconds * 1000 - 1300, 'Scenario acknowledged before its configured sound event');
      const audio = page.getByLabel('Scenario preview audio');
      await until('event-synchronized audible preview', async () => {
        const fallback = page.getByRole('button', { name: 'Play preview', exact: true });
        if (await fallback.isVisible()) await fallback.click();
        return audio.evaluate(element => element.currentTime > 0.1 && element.duration === 5
          && element.src.startsWith('blob:') && !element.muted && element.volume > 0);
      }, 15000);
      const played = (await page.evaluate(() => window.__qaPreviewEvents))[playsBefore];
      assert(played && played.at >= ackAt - 100, 'Browser audio played before the command was acknowledged');
      const record = await until(`${category} actual C3 processing`, async () => {
        const rows = (await body(`/device-data?car_id=${CAR}&device_id=${mic.id}&page_size=100`, iot)).items;
        const row = rows.find(item => !before.has(item.record_id) && item.trigger === category);
        assert(!row || row.status !== 'failed', 'Replay audio processing failed');
        return row?.status === 'processed' && row.alert_id && row;
      }, 60000);
      const link = await body(`/device-data/${record.record_id}/audio`, iot);
      const rawSha = sha(await bytes(link.url));
      assert.equal(rawSha, clip.sha256, 'C3 stored bytes differ from the selected bundled recording');
      const alert = await body(`/alerts/${record.alert_id}`, cloud);
      assert.equal(alert.record_id, record.record_id);
      assert.equal(alert.sound_type, category, 'This bundled example classified differently from its verified expected category');
      assert(alert.model_version && alert.confidence >= 0 && alert.confidence <= 1);
      assert.equal(await page.locator('.sim-live').innerText(), 'RECORDED REPLAY');
      await page.screenshot({ path: path.join(OUTPUT, `${category}-replay.png`), fullPage: true });
      evidence.scenarios.push({ category, command_id: id, event_seconds: eventSeconds,
        acknowledgement_after_ms: ackAt - sentAt, browser_play_after_ms: played.at - sentAt,
        paused_ms: pausedMilliseconds, frames: [firstFrame.hash, nextFrame.hash],
        record_id: record.record_id, alert_id: alert.id, prediction: alert.sound_type,
        confidence: alert.confidence, model_version: alert.model_version, raw_audio_sha256: rawSha });
      await until('preview completion', () => audio.evaluate(element => element.ended), 10000);
      console.log(`PASS real replay ${category}: changing recorded frames/GPS, event ACK/audio, processed record, model alert and exact stored WAV`);
    }
    const stop = await commandButton(page.getByRole('button', { name: 'Stop route', exact: true }));
    await acknowledged(stop);
    await until('stopped final replay', async () => (await body(`/simulator/${CAR}/state`, iot)).position.speed_kmh === 0);

    // Fault injection is restricted to this page's command responses/event rows. These
    // synthetic commands never reach the real edge or create audio records.
    const commandPattern = `**/devices/${mic.id}/commands`;
    evidence.phase = 'preview fault: rejected queue request';
    await page.route(commandPattern, route => route.request().method() === 'POST'
      ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Focused queue failure' }) })
      : route.continue());
    await page.locator('.sim-scenario-emergency').click();
    await page.locator('.sim-audio-preview [role="alert"]').filter({ hasText: 'Scenario was not queued' }).waitFor();
    assert(await page.getByLabel('Scenario preview audio').isHidden());
    await page.unroute(commandPattern);
    evidence.preview_fault_injections.push('Rejected command cancels its waiting preview');
    let fakeId = 900000000, fakeStatus = 'delivered', rejectAudio = false;
    await page.route(commandPattern, route => route.request().method() === 'POST'
      ? route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ command_id: ++fakeId, status: 'queued' }) })
      : route.continue());
    const statePattern = `**/simulator/${CAR}/state`;
    await page.route(statePattern, async route => {
      const real = await route.fetch();
      const data = await real.json();
      data.events.unshift({ command_id: fakeId, device_id: mic.id, command: 'simulate_event', status: fakeStatus,
        ts: new Date().toISOString(), detail: null });
      await route.fulfill({ response: real, json: data });
    });
    for (const status of ['failed', 'expired']) {
      evidence.phase = `preview fault: ${status} command`;
      fakeStatus = 'delivered';
      await page.locator('.sim-scenario-emergency').click();
      await page.locator('.sim-audio-preview [role="status"]').filter({ hasText: 'Waiting for the edge' }).waitFor();
      fakeStatus = status;
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await page.locator('.sim-audio-preview [role="alert"]').filter({ hasText: `Scenario ${status}` }).waitFor();
      assert(await page.getByLabel('Scenario preview audio').isHidden());
      evidence.preview_fault_injections.push(`${status} command cancels its waiting preview`);
    }
    fakeStatus = 'delivered';
    evidence.phase = 'preview fault: autoplay fallback';
    await page.evaluate(() => { window.__qaBlockPreviewOnce = true; });
    await page.locator('.sim-scenario-emergency').click();
    await page.locator('.sim-audio-preview [role="status"]').filter({ hasText: 'Waiting for the edge' }).waitFor();
    fakeStatus = 'acknowledged';
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('button', { name: 'Play preview', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.sim-audio-preview audio').currentTime > 0.1);
    evidence.preview_fault_injections.push('Autoplay rejection offers working explicit playback');
    const audioPattern = '**/simulator/scenarios/*/audio';
    await page.route(audioPattern, route => {
      if (!rejectAudio) return route.continue();
      rejectAudio = false;
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Focused audio failure' }) });
    });
    fakeStatus = 'delivered'; rejectAudio = true;
    evidence.phase = 'preview fault: download retry before acknowledgement';
    await page.locator('.sim-scenario-traffic').click();
    await page.getByRole('button', { name: 'Retry preview', exact: true }).click();
    await page.locator('.sim-audio-preview [role="status"]').filter({ hasText: 'Waiting for the edge' }).waitFor();
    assert(await page.getByLabel('Scenario preview audio').evaluate(element => element.paused));
    fakeStatus = 'acknowledged';
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.sim-audio-preview audio').currentTime > 0.1);
    evidence.preview_fault_injections.push('Download retry waits for acknowledgement before automatic playback');
    await page.unroute(audioPattern); await page.unroute(commandPattern); await page.unroute(statePattern);

    await page.getByRole('button', { name: 'Log out', exact: true }).click();
    evidence.phase = 'Cloud Staff read-only replay';
    await login('cloud');
    await page.goto(UI + `/simulator?car_id=${CAR}`);
    await page.locator('.sim-replay-notice').waitFor();
    assert.equal(await page.getByRole('button', { name: 'Start route', exact: true }).count(), 0);
    assert.equal(await page.locator('.sim-source').innerText(), 'CARLA recorded replay');
    assert.equal(await page.locator('.sim-live').innerText(), 'RECORDED REPLAY');
    await page.screenshot({ path: path.join(OUTPUT, 'cloud-replay-monitor.png'), fullPage: true });
    assert.deepEqual(evidence.page_errors, []);
    evidence.result = 'PASS'; evidence.finished_at = new Date().toISOString();
    evidence.phase = 'complete';
    evidence.cloud_read_only = true;
    console.log('PASS replay UI: five real pipelines, pause/resume, truthful recorded labels, Cloud read-only and separate preview failure recovery');
  } catch (error) {
    evidence.result = 'FAIL'; evidence.failure = safeError(error); evidence.finished_at = new Date().toISOString();
    if (page) {
      try { await page.screenshot({ path: path.join(OUTPUT, 'replay-failure.png'), fullPage: true }); }
      catch { /* Preserve the original failure if the browser already disconnected. */ }
    }
    throw error;
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(OUTPUT, 'replay-browser-verification.json'), JSON.stringify(evidence, null, 2));
  }
})().catch(error => {
  console.error(safeError(error)); process.exitCode = 1;
});
