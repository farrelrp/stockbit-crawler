/**
 * Auto-login to Stockbit using playwright-extra with stealth plugin
 * and direct 2Captcha API for reCAPTCHA v2 solving.
 *
 * Approach:
 *   1. Launch stealth browser, navigate to login page
 *   2. Enter credentials visually in the form
 *   3. Solve invisible reCAPTCHA v2 via 2Captcha API (in parallel)
 *   4. Use route interception to inject captcha token into login POST
 *   5. Click Login button → intercepted POST includes our solved token
 *   6. Capture Bearer token from response
 *
 * Usage:
 *   node auto_login.js --email "user@example.com" --password "pass" --apikey "2CAPTCHA_KEY" [--headless] [--sitekey "KEY"]
 *
 * Output: JSON to stdout with { success, bearer_token, ws_cookie, error, logs }
 */
const { chromium } = require('playwright-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const https = require('https');
const http = require('http');

// --- Parse CLI arguments ---
function parseArgs() {
  const args = process.argv.slice(2);
  const parsed = {
    email: '',
    password: '',
    apikey: '',
    headless: true,
    sitekey: '6LeBXZYqAAAAAIAqBYdAV5HuBc6i0YeVziSYrXAZ',
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--email': parsed.email = args[++i]; break;
      case '--password': parsed.password = args[++i]; break;
      case '--apikey': parsed.apikey = args[++i]; break;
      case '--headless': parsed.headless = true; break;
      case '--no-headless': parsed.headless = false; break;
      case '--sitekey': parsed.sitekey = args[++i]; break;
    }
  }

  return parsed;
}

// --- Logging ---
const logs = [];
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  logs.push(line);
  process.stderr.write(line + '\n');
}

// --- Output result as JSON to stdout ---
function outputResult(result) {
  result.logs = logs;
  process.stdout.write(JSON.stringify(result));
}

// --- 2Captcha API helper: HTTP GET that returns a string ---
function httpGet(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https') ? https : http;
    client.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve(data));
      res.on('error', reject);
    }).on('error', reject);
  });
}

// --- Solve reCAPTCHA v2 (invisible) via 2Captcha API ---
async function solveRecaptchaV2(apikey, sitekey, pageUrl) {
  log(`📡 Submitting reCAPTCHA v2 (invisible) to 2Captcha API...`);
  log(`   sitekey: ${sitekey}`);
  log(`   pageUrl: ${pageUrl}`);

  // Step 1: Submit captcha task (with invisible=1 for invisible reCAPTCHA)
  const submitUrl =
    `http://2captcha.com/in.php?key=${apikey}` +
    `&method=userrecaptcha` +
    `&googlekey=${sitekey}` +
    `&pageurl=${encodeURIComponent(pageUrl)}` +
    `&invisible=1` +
    `&json=1`;

  const submitResp = await httpGet(submitUrl);
  let submitData;
  try {
    submitData = JSON.parse(submitResp);
  } catch (e) {
    throw new Error(`2Captcha submit parse error: ${submitResp}`);
  }

  if (submitData.status !== 1) {
    throw new Error(`2Captcha submit failed: ${submitData.request || submitResp}`);
  }

  const taskId = submitData.request;
  log(`✅ 2Captcha task submitted: ID=${taskId}`);
  log(`⏳ Waiting for 2Captcha solver (typically 15-60 seconds)...`);

  // Step 2: Poll for result
  // Wait 15 seconds before first poll (reCAPTCHA takes at least this long)
  await new Promise((r) => setTimeout(r, 15000));

  const pollUrl = `http://2captcha.com/res.php?key=${apikey}&action=get&id=${taskId}&json=1`;

  for (let attempt = 0; attempt < 30; attempt++) {
    const pollResp = await httpGet(pollUrl);
    let pollData;
    try {
      pollData = JSON.parse(pollResp);
    } catch (e) {
      log(`⚠️ Poll parse error (attempt ${attempt + 1}): ${pollResp}`);
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }

    if (pollData.status === 1) {
      log(`✅ 2Captcha solved! Token length: ${pollData.request.length}`);
      return pollData.request;
    }

    if (pollData.request === 'CAPCHA_NOT_READY') {
      log(`⏳ Captcha not ready yet... (poll ${attempt + 1}/30)`);
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }

    // Any other error
    throw new Error(`2Captcha error: ${pollData.request}`);
  }

  throw new Error('2Captcha timeout: solution not ready after 3 minutes');
}

// --- Main login flow ---
async function doLogin(config) {
  let bearer_token = null;
  let ws_cookie = null;

  // Register stealth plugin
  chromium.use(StealthPlugin());

  log('Launching stealth browser...');

  const browser = await chromium.launch({
    headless: config.headless,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
    ],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  });

  const page = await context.newPage();

  // --- Network interception for Bearer token ---
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes('exodus.stockbit.com')) {
      const auth = request.headers()['authorization'] || '';
      if (auth.startsWith('Bearer ') && auth.length > 60) {
        bearer_token = auth.substring(7);
        log(`✅ Captured Bearer token from API request (${bearer_token.length} chars)`);
      }
    }
    if (url.includes('ws3.stockbit.com')) {
      const cookie = request.headers()['cookie'] || '';
      if (cookie.includes('G_ENABLED_IDPS=')) {
        ws_cookie = cookie.substring(cookie.indexOf('G_ENABLED_IDPS='));
        log('✅ Captured WS cookie from request');
      }
    }
  });

  // Capture token from login API response
  page.on('response', async (response) => {
    const url = response.url();
    if (url.includes('/api/login') || url.includes('/api/auth')) {
      try {
        const status = response.status();
        if (status === 200) {
          const body = await response.json().catch(() => null);
          if (body) {
            log(`📨 Login API response (${status}): ${JSON.stringify(body).substring(0, 500)}`);
            const token =
              body?.data?.access_token ||
              body?.data?.token ||
              body?.access_token ||
              body?.token ||
              body?.data?.user?.access_token;
            if (token && token.length > 50) {
              bearer_token = token;
              log(`✅ Bearer token extracted from login response! (${bearer_token.length} chars)`);
            }
          }
        } else {
          const text = await response.text().catch(() => '');
          log(`⚠️ Login API response (${status}): ${text.substring(0, 500)}`);
        }
      } catch (e) { /* ignore parse errors */ }
    }
  });

  try {
    // =================================================================
    // STEP 1: Navigate to login page
    // =================================================================
    log('Navigating to Stockbit login page...');
    await page.goto('https://stockbit.com/login', {
      waitUntil: 'networkidle',
      timeout: 30000,
    });
    await page.waitForTimeout(2000);
    log(`On page: ${page.url()}`);

    // =================================================================
    // STEP 2: Enter credentials in the form (visible to user)
    // =================================================================
    log('Entering credentials...');
    await page.waitForSelector('#username', { timeout: 10000 });
    await page.fill('#username', config.email);
    log('Entered email');

    await page.waitForSelector('#password', { timeout: 5000 });
    await page.fill('#password', config.password);
    log('Entered password');
    await page.waitForTimeout(500);

    // =================================================================
    // STEP 3: Solve reCAPTCHA via 2Captcha API
    // (This runs while the browser shows credentials filled in)
    // =================================================================
    log('🔐 Solving reCAPTCHA via 2Captcha API (browser will wait)...');
    const captchaToken = await solveRecaptchaV2(
      config.apikey,
      config.sitekey,
      'https://stockbit.com/login'
    );
    log(`Captcha token ready (${captchaToken.length} chars)`);

    // =================================================================
    // STEP 4: Set up route interception to inject token into login POST
    // When the browser makes the POST to /api/login/email, we modify
    // the request body to include the solved captcha token.
    // =================================================================
    log('Setting up route interception for login POST...');
    let interceptedLogin = false;

    await page.route('**/api/login/email', async (route) => {
      const request = route.request();

      if (request.method() === 'POST') {
        log('🔀 Intercepted login POST — injecting captcha token...');

        // Get the original body
        let body = {};
        try {
          const postData = request.postData();
          if (postData) {
            body = JSON.parse(postData);
            log(`Original POST body keys: ${Object.keys(body).join(', ')}`);
          }
        } catch (e) {
          log(`⚠️ Could not parse original body, building from scratch`);
        }

        // Ensure credentials and captcha token are in the body
        body['username'] = body['username'] || config.email;
        body['password'] = body['password'] || config.password;
        body['g-recaptcha-response'] = captchaToken;

        log(`📤 Sending modified login POST with captcha token...`);
        log(`POST body keys: ${Object.keys(body).join(', ')}`);

        interceptedLogin = true;

        // Continue with the modified body
        await route.continue({
          postData: JSON.stringify(body),
          headers: {
            ...request.headers(),
            'content-type': 'application/json',
          },
        });
      } else {
        await route.continue();
      }
    });

    // =================================================================
    // STEP 5: Click Login button
    // The interceptor will modify the POST to include our captcha token
    // =================================================================
    log('Clicking Login button...');

    try {
      await page.click('#email-login-button', { timeout: 5000 });
      log('Clicked #email-login-button');
    } catch (e) {
      try {
        await page.click('button:has-text("Login")', { timeout: 5000 });
        log('Clicked Login button (text selector)');
      } catch (e2) {
        log('Pressing Enter as fallback...');
        await page.keyboard.press('Enter');
      }
    }

    // Wait for the login POST to be intercepted and processed
    await page.waitForTimeout(5000);
    log(`Login button clicked. Intercepted: ${interceptedLogin}`);
    log(`Current URL: ${page.url()}`);

    // =================================================================
    // STEP 5b: If route interception didn't fire, try direct POST
    // =================================================================
    if (!interceptedLogin && !bearer_token) {
      log('⚠️ Route interception did not fire. Trying direct fetch POST...');

      const loginResult = await page.evaluate(
        async ({ email, password, captchaToken }) => {
          try {
            const response = await fetch('https://stockbit.com/api/login/email', {
              method: 'POST',
              headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Referer': 'https://stockbit.com/login',
                'Origin': 'https://stockbit.com',
              },
              credentials: 'include',
              body: JSON.stringify({
                username: email,
                password: password,
                'g-recaptcha-response': captchaToken,
              }),
            });

            const status = response.status;
            const data = await response.json().catch(() => null);
            return { ok: response.ok, status, data, text: JSON.stringify(data || '').substring(0, 1000) };
          } catch (err) {
            return { ok: false, status: 0, error: err.message };
          }
        },
        { email: config.email, password: config.password, captchaToken }
      );

      log(`Direct POST response: ${loginResult.status} — ${loginResult.text?.substring(0, 500)}`);

      if (loginResult.ok && loginResult.data) {
        const data = loginResult.data;
        const token =
          data?.data?.access_token ||
          data?.data?.token ||
          data?.access_token ||
          data?.token ||
          data?.data?.user?.access_token;
        if (token && token.length > 50) {
          bearer_token = token;
          log(`✅ Bearer token from direct POST! (${bearer_token.length} chars)`);
        }
      }
    }

    // =================================================================
    // STEP 6: Handle verification page (if redirected)
    // =================================================================
    if (!bearer_token) {
      await page.waitForTimeout(3000);
      const currentUrl = page.url();

      if (currentUrl.includes('verification')) {
        log('🔐 Redirected to verification page — solving another captcha...');

        const verifyToken = await solveRecaptchaV2(
          config.apikey,
          config.sitekey,
          currentUrl
        );

        // Try direct POST from verification page
        const verifyResult = await page.evaluate(
          async ({ captchaToken }) => {
            try {
              // Inject token into form
              const ta = document.querySelector('#g-recaptcha-response') ||
                         document.querySelector('[name="g-recaptcha-response"]');
              if (ta) {
                ta.value = captchaToken;
                ta.innerHTML = captchaToken;
              }

              // Try data-callback
              const div = document.querySelector('[data-callback]');
              if (div) {
                const cb = div.getAttribute('data-callback');
                if (cb && typeof window[cb] === 'function') window[cb](captchaToken);
              }

              // Dynamic callback search
              if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
                function callCBs(o, d) {
                  if (d > 15 || !o || typeof o !== 'object') return;
                  for (const k in o) {
                    try {
                      if (k === 'callback' && typeof o[k] === 'function') o[k](captchaToken);
                      else if (typeof o[k] === 'object') callCBs(o[k], d + 1);
                    } catch (e) {}
                  }
                }
                callCBs(window.___grecaptcha_cfg.clients, 0);
              }

              return { injected: true };
            } catch (e) {
              return { injected: false, error: e.message };
            }
          },
          { captchaToken: verifyToken }
        );

        log(`Verification token injected: ${verifyResult.injected}`);

        // Click continue/verify button
        const btnSelectors = [
          'button:has-text("continue")', 'button:has-text("Continue")',
          'button:has-text("Verify")', 'button:has-text("Submit")',
          'button[type="submit"]',
        ];
        for (const sel of btnSelectors) {
          try {
            const btn = await page.$(sel);
            if (btn && (await btn.isVisible())) {
              await btn.click();
              log(`Clicked verification button: ${sel}`);
              break;
            }
          } catch (e) {}
        }

        await page.waitForTimeout(5000);
      }
    }

    // =================================================================
    // STEP 7: Wait for token capture
    // =================================================================
    log('Waiting for login completion and token capture...');

    for (let i = 0; i < 60; i++) {
      await page.waitForTimeout(500);
      if (bearer_token) {
        log('✅ Login successful — Bearer token captured!');
        break;
      }

      const url = page.url();
      if (url.includes('/stream') || url === 'https://stockbit.com/') {
        log(`✅ Redirected to: ${url}`);
        await page.waitForTimeout(5000);
        break;
      }
    }

    // If no token yet but we're logged in, navigate to trigger API calls
    if (!bearer_token) {
      const url = page.url();
      if (!url.includes('/login')) {
        log('No token yet, navigating to /stream to trigger API calls...');
        try {
          await page.goto('https://stockbit.com/stream', {
            waitUntil: 'networkidle',
            timeout: 15000,
          });
          await page.waitForTimeout(5000);
        } catch (e) {
          log(`Navigation warning: ${e.message}`);
        }
      }
    }

    // =================================================================
    // STEP 8: Extract cookies
    // =================================================================
    if (!ws_cookie) {
      log('Extracting cookies...');
      const finalCookies = await context.cookies();

      const gCookie = finalCookies.find((c) => c.name === 'G_ENABLED_IDPS');
      if (gCookie) {
        ws_cookie = `G_ENABLED_IDPS=${gCookie.value}`;
        log('✅ Found G_ENABLED_IDPS cookie');
      } else if (finalCookies.length > 0) {
        ws_cookie = finalCookies.map((c) => `${c.name}=${c.value}`).join('; ');
        log(`⚠️ G_ENABLED_IDPS not found, using all ${finalCookies.length} cookies`);
      }
    }

    // Remove route
    await page.unroute('**/api/login/email');

  } catch (err) {
    log(`❌ Error: ${err.message}`);
    await browser.close();
    return {
      success: false,
      bearer_token: null,
      ws_cookie: null,
      error: err.message,
    };
  }

  await browser.close();
  log('Browser closed');

  if (bearer_token) {
    log(`✅ Done! Bearer token: ${bearer_token.length} chars, WS cookie: ${ws_cookie ? 'yes' : 'no'}`);
    return { success: true, bearer_token, ws_cookie, error: null };
  } else {
    log('❌ Failed to capture Bearer token');
    return {
      success: false,
      bearer_token: null,
      ws_cookie: null,
      error: 'Failed to capture Bearer token after login',
    };
  }
}

// --- Entry point ---
(async () => {
  const config = parseArgs();

  if (!config.email || !config.password) {
    outputResult({ success: false, error: 'Missing --email or --password', bearer_token: null, ws_cookie: null });
    process.exit(1);
  }

  if (!config.apikey) {
    outputResult({ success: false, error: 'Missing --apikey (2Captcha API key)', bearer_token: null, ws_cookie: null });
    process.exit(1);
  }

  try {
    const result = await doLogin(config);
    outputResult(result);
    process.exit(result.success ? 0 : 1);
  } catch (err) {
    outputResult({ success: false, error: `Fatal error: ${err.message}`, bearer_token: null, ws_cookie: null });
    process.exit(1);
  }
})();
