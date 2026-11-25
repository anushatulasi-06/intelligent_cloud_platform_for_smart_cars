// Truthfully labeled LOCAL C1–C5 rehearsal. Requires verify_local.py's disposable
// loopback-only stack, private VERIFY_* credentials and historical verification JSON.
// No desktop/microphone/system audio is captured: only explicitly wired app audio elements.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const playwrightEntry = require.resolve(process.env.PLAYWRIGHT_MODULE || require.resolve('playwright', { paths: [path.join(__dirname, '../frontend')] }));

function local(value) {
  const u = new URL(value);
  assert(u.protocol === 'http:' && u.hostname === '127.0.0.1' && !u.username && !u.password && !u.hash,
    'Only literal loopback HTTP URLs are allowed');
  return value.replace(/\/$/, '');
}
const UI = local(process.env.UI_BASE_URL || 'http://127.0.0.1:15173');
const API = local(process.env.API_BASE_URL || 'http://127.0.0.1:18000');
const OUTPUT = path.resolve(process.env.RECORD_OUTPUT_DIR || process.env.UI_EVIDENCE_DIR || path.join(__dirname, '../.local_evidence/recording'));
const FFMPEG = process.env.FFMPEG_BIN || 'ffmpeg';
const FFPROBE = process.env.FFPROBE_BIN || 'ffprobe';
const CAR = Number(process.env.VERIFY_CAR_ID || 1);
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const evidence = { author: 'Rishi Sheth', result: 'RUNNING', started_at: new Date().toISOString(),
  scope: 'LOCAL REHEARSAL: C1–C5 integration; Rishi individual C2/C3. No AWS deployment or SES proof.',
  privacy: ['Viewport only, no browser address bar', 'Opaque curtain during all authentication',
    'No terminal, environment, bootstrap or device-key screen', 'Only selected browser audio elements captured'],
  checks: [], scenes: [], sounds: [], page_errors: [], historical_audio: [], replay_records: [] };

async function until(label, fn, timeout = 60000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await sleep(300); }
  throw new Error(`Timed out: ${label}`);
}
function safeError(error) {
  return String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g, '<jwt>')
    .replace(/X-Amz-Signature=[a-f0-9]{64}/gi, 'X-Amz-Signature=<signature>');
}
function processOk(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  assert.equal(result.status, 0, `${path.basename(command)} failed; media evidence was not finalized`);
  return result.stdout;
}
function executable(command) {
  const candidates = command.includes(path.sep) ? [path.resolve(command)]
    : (process.env.PATH || '').split(path.delimiter).map(directory => path.join(directory, command));
  const found = candidates.find(candidate => {
    try { fs.accessSync(candidate, fs.constants.X_OK); return fs.statSync(candidate).isFile(); }
    catch { return false; }
  });
  assert(found, 'An existing FFmpeg executable is required; nothing will be installed');
  return found;
}

(async () => {
  assert(process.platform === 'linux');
  const links = fs.readFileSync('/proc/net/dev', 'utf8').split('\n').slice(2).filter(row => row.includes(':')).map(row => row.split(':')[0].trim());
  assert.deepEqual(links, ['lo'], 'Run only inside the isolated local rehearsal network namespace');
  assert(process.env.HISTORICAL_EVIDENCE_FILE, 'Historical verification evidence is required');
  const historical = JSON.parse(fs.readFileSync(process.env.HISTORICAL_EVIDENCE_FILE));
  assert.equal(historical.status, 'passed');
  assert.equal(historical.count, 25);
  assert.equal(new Set(historical.alerts.map(row => row.id)).size, 25);
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.mkdirSync(path.join(OUTPUT, 'screens'), { recursive: true });
  // recordVideo normally looks for Playwright's downloaded encoder under HOME. Reuse the
  // preinstalled system FFmpeg through a PRIVATE disposable registry instead. Set this before
  // importing Playwright, which computes its registry directory when the module is loaded.
  assert(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, 'The harness must supply its existing Chromium executable');
  const coreEntry = require.resolve('playwright-core', { paths: [path.dirname(playwrightEntry)] });
  const registry = JSON.parse(fs.readFileSync(path.join(path.dirname(coreEntry), 'browsers.json')));
  const revision = registry.browsers.find(browser => browser.name === 'ffmpeg').revision;
  const privateCache = path.join(OUTPUT, '.browser-cache');
  const encoderDirectory = path.join(privateCache, `ffmpeg-${revision}`);
  fs.mkdirSync(encoderDirectory, { recursive: true });
  fs.symlinkSync(executable(FFMPEG), path.join(encoderDirectory, 'ffmpeg-linux'));
  process.env.PLAYWRIGHT_BROWSERS_PATH = privateCache;
  const { chromium } = require(playwrightEntry);
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'],
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 },
    recordVideo: { dir: path.join(OUTPUT, 'raw'), size: { width: 1440, height: 1080 } } });
  let page, frame, video, audioResult, videoEndedAt, phase = 'preflight';
  const tokens = {};
  try {
    async function api(method, endpoint, role = 'cloud', data, status = 200) {
      let response;
      try { response = await context.request.fetch(API + endpoint, { method, data, maxRedirects: 0,
        headers: tokens[role] ? { Authorization: `Bearer ${tokens[role]}` } : {} }); }
      catch { throw new Error(`Local API failed: ${method} ${endpoint}`); }
      assert.equal(response.status(), status, `Unexpected local API status: ${method} ${endpoint}`);
      return response;
    }
    async function body(endpoint, role = 'cloud') { return (await api('GET', endpoint, role)).json(); }
    async function download(url) {
      local(url);
      let response;
      try { response = await context.request.get(url, { maxRedirects: 0 }); }
      catch { throw new Error('Local audio download failed (signed URL omitted)'); }
      assert.equal(response.status(), 200, 'Stored local audio unavailable');
      const bytes = await response.body(); await response.dispose(); return bytes;
    }
    for (const role of ['owner', 'iot', 'cloud']) {
      const email = process.env[`VERIFY_${role.toUpperCase()}_EMAIL`];
      const password = process.env[`VERIFY_${role.toUpperCase()}_PASSWORD`];
      assert(email && password, `Private ${role} credentials must be supplied by the harness`);
      const result = await api('POST', '/login', '', { email, password });
      tokens[role] = (await result.json()).access_token;
    }
    const readiness = await body('/admin/readiness');
    assert.equal(readiness.status, 'ready');
    assert.equal(readiness.scope.side_effect_modes.email, 'logged', 'SES must be disabled');
    evidence.side_effect_modes = readiness.scope.side_effect_modes;
    // Recheck all 25 stored clips with the actual authenticated audio endpoint before filming.
    // The video later plays two representative historical clips, not all 25 in sequence.
    for (const row of historical.alerts) {
      const link = await body(`/alerts/${row.id}/audio`);
      const bytes = await download(link.url);
      assert.equal(sha(bytes), row.audio_sha256, 'Historical stored audio SHA differs');
      evidence.historical_audio.push({ id: row.id, audio_sha256: row.audio_sha256, size_bytes: bytes.length });
    }
    evidence.checks.push('All 25 historical alert clips downloaded through JWT-protected links and matched their imported SHA-256');

    // Intercept only the wrapper URL. Its iframe is the actual application, with a persistent
    // LOCAL REHEARSAL caption and a full opaque curtain before any login page is visited.
    const wrapper = UI + '/__local_rehearsal__';
    await context.addInitScript(() => {
      if (window.parent === window) return;
      const guard = () => {
        try {
          const sensitive = location.pathname === '/login' || document.querySelector('.device-key')
            || /X-Amz-(?:Credential|Signature)=|Bearer eyJ/.test(document.body?.innerText || '');
          if (!sensitive) return;
          const curtain = parent.document.querySelector('#privacy');
          if (curtain) { curtain.hidden = false; curtain.textContent = 'Private authentication or credentials are concealed'; }
          parent.document.querySelector('iframe')?.style.setProperty('visibility', 'hidden');
        } catch { /* The wrapper and its app are same-origin; unrelated frames get no access. */ }
      };
      new MutationObserver(guard).observe(document, { childList: true, subtree: true, characterData: true });
      guard();
    });
    await context.route(wrapper, route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><style>
      *{box-sizing:border-box}body{margin:0;background:#0f172a;font-family:Arial,sans-serif}iframe{position:absolute;left:0;top:0;width:100%;height:calc(100vh - 144px);border:0;visibility:hidden;background:white}
      #privacy{position:absolute;inset:0 0 144px;background:#0f172a;z-index:20;display:flex;align-items:center;justify-content:center;color:#e2e8f0;text-align:center;font-size:25px;line-height:1.6}#privacy[hidden]{display:none}
      #caption{position:absolute;bottom:0;left:0;right:0;height:144px;padding:18px 28px;color:white;border-top:3px solid #38bdf8;background:#0f172a}
      #caption b{font-size:15px;color:#7dd3fc;letter-spacing:.05em}#caption p{font-size:19px;line-height:1.5;margin:10px 0 0}#start{padding:14px 24px;margin-top:18px;background:#2563eb;color:white;border:0;border-radius:8px;font-size:18px}
      </style><iframe title="Actual local Smart Car Platform application" src="${UI}/login"></iframe>
      <div id="privacy"><div>LOCAL REHEARSAL<br>C1–C5 · private local services<br><button id="start">Begin walkthrough</button></div></div>
      <div id="caption"><b>LOCAL REHEARSAL · RISHI SHETH · C2/C3 INDIVIDUAL COMPONENTS + C1/C4/C5 INTEGRATION</b><p id="text">Actual local UI, data, processing and model. Recorded CARLA footage. No AWS deployment or email delivery is claimed.</p></div>` }));
    page = await context.newPage(); video = page.video();
    page.on('pageerror', error => evidence.page_errors.push(error.message));
    await page.goto(wrapper);
    frame = await (await page.$('iframe')).contentFrame();
    await page.evaluate(() => {
      window.__qaAudio = { entries: [], events: [], startedAt: null };
      const Audio = window.AudioContext || window.webkitAudioContext;
      const ctx = new Audio();
      const destination = ctx.createMediaStreamDestination();
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
      const recorder = new MediaRecorder(destination.stream, { mimeType });
      const chunks = [];
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      window.__qaWire = element => {
        if (window.__qaAudio.entries.some(row => row.element === element)) return;
        const source = ctx.createMediaElementSource(element), analyser = ctx.createAnalyser();
        analyser.fftSize = 2048; source.connect(analyser); analyser.connect(destination); analyser.connect(ctx.destination);
        const row = { element, analyser, peak: 0, label: element.getAttribute('aria-label') || 'App audio' };
        window.__qaAudio.entries.push(row);
        element.addEventListener('play', () => window.__qaAudio.events.push({ at: Date.now(), label: row.label,
          caption: document.querySelector('#text').textContent, muted: element.muted, volume: element.volume }));
      };
      window.__qaResetPeaks = () => { for (const row of window.__qaAudio.entries) row.peak = 0; };
      window.__qaPeak = () => Math.max(0, ...window.__qaAudio.entries.map(row => row.peak));
      const samples = new Float32Array(2048);
      const measure = () => {
        for (const row of window.__qaAudio.entries) {
          row.analyser.getFloatTimeDomainData(samples);
          row.peak = Math.max(row.peak, Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length));
        }
        requestAnimationFrame(measure);
      };
      measure();
      document.querySelector('#start').onclick = async () => {
        await ctx.resume(); window.__qaAudio.startedAt = Date.now(); recorder.start(250);
        document.querySelector('#start').remove();
      };
      window.__qaFinishAudio = () => new Promise(resolve => {
        recorder.onstop = async () => {
          const bytes = new Uint8Array(await new Blob(chunks, { type: mimeType }).arrayBuffer());
          let binary = '';
          for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
          resolve({ base64: btoa(binary), startedAt: window.__qaAudio.startedAt, stoppedAt: Date.now(),
            events: window.__qaAudio.events, mimeType });
        };
        recorder.stop();
      });
    });
    await page.locator('#start').click();
    async function cover(message = 'Private sign-in · credentials are concealed') {
      await page.evaluate(message => {
        document.querySelector('iframe').style.visibility = 'hidden';
        const curtain = document.querySelector('#privacy'); curtain.textContent = message; curtain.hidden = false;
      }, message);
    }
    async function caption(text, ms = 5500) {
      await page.locator('#text').evaluate((element, text) => { element.textContent = text; }, text);
      evidence.scenes.push({ phase, at: Date.now(), caption: text });
      console.log('SCENE', phase); await sleep(ms);
    }
    async function protect() {
      await frame.addStyleTag({ content: '.device-key{display:none!important}' });
      assert.equal(await frame.locator('input[autocomplete="current-password"]').count(), 0, 'Login is still visible behind the curtain');
    }
    async function reveal() {
      await protect();
      await page.evaluate(() => { document.querySelector('iframe').style.visibility = 'visible'; document.querySelector('#privacy').hidden = true; });
    }
    async function go(route) {
      await cover('Loading the next local UI screen');
      await frame.goto(UI + route); await frame.locator('.page').first().waitFor(); await sleep(800); await reveal();
    }
    async function login(role) {
      await cover();
      const logout = frame.getByRole('button', { name: 'Log out', exact: true });
      if (await logout.count()) await logout.click();
      await frame.goto(UI + '/login');
      await frame.getByLabel('Email', { exact: true }).fill(process.env[`VERIFY_${role.toUpperCase()}_EMAIL`]);
      await frame.getByLabel('Password', { exact: true }).fill(process.env[`VERIFY_${role.toUpperCase()}_PASSWORD`]);
      await frame.getByRole('button', { name: 'Sign in', exact: true }).click();
      await frame.waitForURL(role === 'iot' ? '**/devices' : '**/dashboard');
      await sleep(900); await reveal();
    }
    async function shot(name) { await page.screenshot({ path: path.join(OUTPUT, 'screens', name + '.png') }); }
    async function scroll(y) { await frame.locator('.scroll-area').evaluate((element, y) => element.scrollTo({ top: y, behavior: 'smooth' }), y); await sleep(600); }
    async function playStored(locator, expectedSha, label, seconds = 5) {
      await locator.waitFor();
      const url = await locator.getAttribute('src');
      const bytes = await download(url);
      assert.equal(sha(bytes), expectedSha, 'Displayed audio does not match its stored clip');
      await page.evaluate(() => window.__qaResetPeaks());
      // Play EXACT stored bytes as a same-origin Blob so WebAudio can capture samples rather
      // than CORS-mandated silence. This changes no API record, classification or sound bytes.
      await locator.evaluate((element, encoded) => {
        const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
        element.src = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
        element.load(); parent.__qaWire(element); element.muted = false; element.volume = 1;
      }, bytes.toString('base64'));
      await locator.evaluate(element => element.play());
      const duration = await locator.evaluate(element => element.duration);
      await sleep(Math.min(seconds, Number.isFinite(duration) ? duration + .2 : seconds) * 1000);
      const peak = await page.evaluate(() => window.__qaPeak());
      assert(peak > 0.0001, 'Browser audio capture is silent');
      evidence.sounds.push({ label, sha256: expectedSha, captured_peak_rms: peak, source: 'Exact stored bytes played by the visible browser audio element' });
      await locator.evaluate(element => element.pause());
    }
    phase = 'Introduction';
    await caption('LOCAL rehearsal of the actual C1–C5 application. Rishi Sheth owns C2/C3; the recording also exercises the shared C1/C4/C5 integrations.', 6000);
    phase = 'C1 owner dashboard'; await login('owner');
    await caption('C1: the owner dashboard is authenticated and scoped to this owner’s cars and alerts. Historical alerts are imported with their real playable audio.', 7000); await shot('01-owner-dashboard');
    phase = 'C1 subscriptions'; await go('/dashboard?tab=subscriptions');
    await frame.getByRole('combobox', { name: 'Notify by', exact: true }).selectOption('in_app');
    const toggle = frame.getByRole('switch').first(); const original = await toggle.isChecked();
    await toggle.setChecked(!original); await frame.getByRole('button', { name: 'Save', exact: true }).click();
    await until('subscription saved', async () => (await body(`/subscriptions?car_id=${CAR}`, 'owner')).some(row => row.alert_type === 'emergency' && row.enabled === !original));
    await caption('C1: an owner changes an in-app subscription through the UI. Email sending is disabled throughout this local rehearsal.', 6500); await shot('02-owner-subscriptions');
    phase = 'C1 owner map'; await go(`/map?car_id=${CAR}`);
    await caption('C1: the owner map uses the current API’s permitted cars and replay locations. This isolated local run does not fetch external map tiles.', 6500); await shot('03-owner-map');
    phase = 'C1 tracking'; await go(`/tracking?car_id=${CAR}`);
    await frame.getByRole('button', { name: 'Show', exact: true }).click();
    await caption('C1: route tracking requests the selected car and time range. Location and alert access remain limited by the signed-in owner.', 6500);

    phase = 'C2 inventory'; await login('iot'); await go('/devices');
    const inventory = await body(`/devices?car_id=${CAR}&page_size=100`, 'iot');
    const mic = inventory.items.find(row => row.device_type === 'microphone' && row.firmware === 'carla-replay-v1');
    assert(mic, 'The recorded replay microphone is not connected');
    await caption('C2: IoT Staff inspect device type, status and last heartbeat. Cloud Staff can inspect inventory but cannot mutate device settings.', 6500);
    await frame.getByRole('button', { name: '+ Add Device', exact: true }).click();
    const add = frame.getByRole('dialog', { name: 'Add device' });
    await add.getByRole('combobox', { name: /^Car/ }).selectOption(String(CAR));
    await add.getByLabel('Name', { exact: true }).fill('Local rehearsal microphone');
    await add.getByLabel(/^Wi-Fi SSID/).fill('Local demonstration network');
    await add.getByLabel(/^Security/).selectOption('wpa2');
    await caption('C2: the actual registration form includes car, type, SSID, Wi-Fi security, password and IP mode. Credentials stay blank; no device key is shown.', 8000); await shot('04-device-registration-fields');
    await add.getByRole('button', { name: 'Cancel', exact: true }).click();
    phase = 'C2 device commands'; await go(`/devices?device_id=${mic.id}`);
    await caption('C2: device detail shows firmware, last seen, Wi-Fi configuration and command history. Saved passwords are never prefilled.', 6500);
    for (const command of ['Stop capture', 'Start capture', 'Sync config']) {
      const pending = frame.waitForResponse(response => response.request().method() === 'POST' && response.url() === API + `/devices/${mic.id}/commands`);
      await frame.getByRole('button', { name: command, exact: true }).click();
      const response = await pending; assert.equal(response.status(), 202);
      const id = (await response.json()).command_id;
      await until(command + ' acknowledged', async () => (await body(`/devices/${mic.id}/commands?limit=100`, 'iot')).some(row => row.command_id === id && row.status === 'acknowledged'));
    }
    await scroll(520); await caption('C2: capture and sync commands were acknowledged by the replay device. These simulated device operations leave the computer’s network and operating system unchanged.', 6000);

    phase = 'C2/C3 recorded scenarios'; await go(`/simulator?car_id=${CAR}`);
    await frame.locator('.sim-replay-notice').waitFor();
    await frame.getByLabel('Scenario preview audio').evaluate(element => parent.__qaWire(element));
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../backend/edge/demo_clips/manifest.json')));
    for (const [category, label] of [['emergency', 'Siren'], ['in_vehicle', 'Glass / crash'], ['human', 'Scream'], ['traffic', 'Road traffic'], ['environmental', 'Thunderstorm']]) {
      phase = 'Replay ' + category;
      const before = new Set((await body(`/device-data?car_id=${CAR}&device_id=${mic.id}&page_size=100`, 'iot')).items.map(row => row.record_id));
      await page.evaluate(() => window.__qaResetPeaks());
      await caption(`${label}: recorded CARLA footage plays through the real device/API pipeline. The browser sound begins at the acknowledged event time.`, 1000);
      await frame.locator(`.sim-scenario-${category}`).click();
      await scroll(110);
      const audio = frame.getByLabel('Scenario preview audio');
      await until('audible replay event', async () => {
        const fallback = frame.getByRole('button', { name: 'Play preview', exact: true });
        if (await fallback.isVisible()) await fallback.click();
        return audio.evaluate(element => element.currentTime > .1 && !element.paused && !element.muted);
      }, 45000);
      await shot('replay-' + category);
      await until('replay audio completion', () => audio.evaluate(element => element.ended), 10000);
      const peak = await page.evaluate(() => window.__qaPeak()); assert(peak > .0001, 'Replay audio was not captured');
      const record = await until('processed replay record', async () => (await body(`/device-data?car_id=${CAR}&device_id=${mic.id}&page_size=100`, 'iot')).items.find(row => !before.has(row.record_id) && row.trigger === category && row.status === 'processed' && row.alert_id));
      const link = await body(`/device-data/${record.record_id}/audio`, 'iot');
      const expected = manifest.clips.find(row => row.scenario === category).sha256;
      assert.equal(sha(await download(link.url)), expected);
      const alert = await body(`/alerts/${record.alert_id}`);
      evidence.replay_records.push({ category, record_id: record.record_id, alert_id: record.alert_id, prediction: alert.sound_type, audio_sha256: expected });
      evidence.sounds.push({ label: 'Replay ' + category, sha256: expected, captured_peak_rms: peak, source: 'Actual event-synchronized browser preview' });
    }
    await frame.getByRole('button', { name: 'Stop route', exact: true }).click();
    await caption('All five new replay clips reached processed records and model-linked alerts. The footage is recorded CARLA replay, clearly labeled in the UI.', 6500);

    phase = 'C3 processing monitor'; await login('cloud'); await go(`/devices?tab=data&car_id=${CAR}`);
    await caption('C3: the monitor shows live intake states, a twelve-hour chart and record filters. The five newly recorded examples are separate from the 25 imported historical alerts.', 7000); await shot('06-cloud-processing');
    const latest = evidence.replay_records.at(-1);
    await frame.locator(`button[title="${latest.record_id}"]`).click();
    const recordDialog = frame.getByRole('dialog', { name: 'Audio record' }); await recordDialog.waitFor();
    await caption('C3: record detail shows timestamps, attempts, status, clip identity and its generated alert. This is the exact stored audio, played through the visible browser control.', 2000);
    await playStored(recordDialog.getByLabel('Raw audio recording'), latest.audio_sha256, 'C3 stored replay audio', 5);
    await recordDialog.getByRole('button', { name: 'Close', exact: true }).click();
    phase = 'C4 generated alert'; await go(`/alerts/${latest.alert_id}`);
    assert.equal(await frame.locator('code').filter({ hasText: /X-Amz|Credential=|http:/ }).count(), 0, 'A signed URL is visible');
    await caption('C4: the new alert contains the model’s predicted category, confidence, severity and class probabilities. Signed credentials are not printed in the UI.', 7000); await shot('07-generated-alert');
    phase = 'C4 historical audio'; await go('/alerts');
    await caption('All 25 historical audio links were downloaded and SHA-256 checked before filming. Two representative historical alerts are played next; the video does not play all 25.', 6500);
    for (const row of [...historical.alerts].sort((a, b) => a.size_bytes - b.size_bytes).slice(0, 2)) {
      await go(`/alerts/${row.id}`);
      await caption('Historical alert: stored facts and original audio remain available. The sample below is the exact verified object, not a replacement soundtrack.', 2000);
      await playStored(frame.getByLabel('Alert audio recording'), row.audio_sha256, 'Historical alert ' + row.id.slice(0, 6), 5);
    }
    phase = 'C5 model management'; await go('/config?tab=models');
    const models = await body('/admin/models'); assert(models.some(row => row.is_active));
    await caption('C4/C5: the packaged CNN model is active and its threshold is managed in the UI. The five NEW replay predictions came from this model; historical probabilities remain imported facts.', 8000); await shot('08-active-model');
    phase = 'C5 alert configuration'; await go('/config?tab=alert-types');
    await caption('C5: Cloud Staff manage alert categories and severity configuration. These controls are protected by the same authenticated role checks.', 6500);
    phase = 'C5 database and backup'; await go('/config?tab=database');
    await caption('C5: health and store statistics come from local PostgreSQL, MongoDB and MinIO. The Amazon S3 label identifies the storage interface, not an AWS deployment.', 6500);
    const backupResponse = frame.waitForResponse(response => response.request().method() === 'POST' && response.url() === API + '/admin/db/backup');
    await frame.getByRole('button', { name: 'Run backup now', exact: true }).click();
    const response = await backupResponse; assert.equal(response.status(), 202); const job = (await response.json()).job_id;
    await until('backup completion', async () => (await body('/admin/db/backups')).some(row => row.job_id === job && row.status === 'succeeded'));
    await caption(readiness.scope.side_effect_modes.backup === 'simulated'
      ? 'The local backup workflow completed successfully in SIMULATED-DUMP mode. This rehearsal does not establish real dump/restore or AWS backup execution.'
      : 'The local real-dump backup job completed successfully. This demonstrates the configured local stores, with no AWS request or email send.', 8000); await shot('09-database-backup');
    phase = 'Conclusion';
    await caption('LOCAL REHEARSAL COMPLETE · 25 historical clips hash-verified; five new recorded-CARLA scenarios processed with browser sound. C1–C5 UI integration; no AWS deployment is claimed.', 8000);
    assert.deepEqual(evidence.page_errors, []);
    audioResult = await page.evaluate(() => window.__qaFinishAudio());
    fs.writeFileSync(path.join(OUTPUT, 'browser-only-audio.webm'), Buffer.from(audioResult.base64, 'base64'));
    delete audioResult.base64;
    videoEndedAt = Date.now();
    await context.close();
    const rawVideo = await video.path();
    const raw = JSON.parse(processOk(FFPROBE, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'json', rawVideo]));
    const duration = Number(raw.format.duration);
    const offset = (audioResult.startedAt - (videoEndedAt - duration * 1000)) / 1000;
    assert(Math.abs(offset) < 5, 'Audio/video clock alignment is outside the expected local tolerance');
    const target = path.join(OUTPUT, 'CMPE281_Local_Rehearsal_Rishi_Sheth.mp4');
    processOk(FFMPEG, ['-y', '-i', rawVideo, '-itsoffset', String(offset), '-i', path.join(OUTPUT, 'browser-only-audio.webm'),
      '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-pix_fmt', 'yuv420p',
      '-c:a', 'aac', '-b:a', '160k', '-af', 'apad', '-shortest', '-movflags', '+faststart', target]);
    const info = JSON.parse(processOk(FFPROBE, ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', target]));
    assert(info.streams.some(stream => stream.codec_type === 'audio' && stream.codec_name === 'aac'));
    assert(info.streams.some(stream => stream.codec_type === 'video' && stream.codec_name === 'h264'));
    const volume = spawnSync(FFMPEG, ['-hide_banner', '-i', target, '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
      { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    assert.equal(volume.status, 0, 'Final audio decode failed');
    const levels = Object.fromEntries([...volume.stderr.matchAll(/(mean_volume|max_volume):\s*(-?[0-9.]+) dB/g)]
      .map(match => [match[1], Number(match[2])]));
    assert(Number.isFinite(levels.max_volume) && levels.max_volume > -60, 'Encoded rehearsal soundtrack is silent');
    evidence.result = 'PASS'; evidence.video = { filename: path.basename(target), duration_seconds: Number(info.format.duration),
      video_codec: 'h264', audio_codec: 'aac', browser_audio_offset_seconds: offset, raw_video: path.relative(OUTPUT, rawVideo) };
    evidence.video.encoded_audio_db = levels;
    evidence.browser_audio = audioResult;
    evidence.checks.push('Actual app-only WebAudio captured with nonzero RMS for all five new previews and three stored-audio plays');
    evidence.finished_at = new Date().toISOString();
    console.log('PASS local rehearsal video with actual browser audio');
  } catch (error) {
    evidence.result = 'FAIL'; evidence.failure = safeError(error); evidence.phase = phase;
    if (page && !page.isClosed()) {
      await page.evaluate(() => { document.querySelector('iframe')?.style.setProperty('visibility', 'hidden');
        const curtain = document.querySelector('#privacy'); if (curtain) { curtain.hidden = false; curtain.textContent = 'Local rehearsal stopped for verification'; } }).catch(() => {});
    }
    throw error;
  } finally {
    await context.close().catch(() => {}); await browser.close();
    fs.rmSync(privateCache, { recursive: true, force: true });
    fs.writeFileSync(path.join(OUTPUT, 'recording-verification.json'), JSON.stringify(evidence, null, 2));
  }
})().catch(error => { console.error(safeError(error)); process.exitCode = 1; });
