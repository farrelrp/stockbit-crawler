# Selenium Authentication Guide

The app now uses **Selenium WebDriver** for fully automated login! No more manual reCAPTCHA token copying needed.

## How It Works

The authentication system uses intelligent automation:

### 1. **Headless-First Approach**
- First attempt: Selenium runs in **headless mode** (no visible browser)
- Automatically fills in email and password
- Executes JavaScript to handle reCAPTCHA v3 automatically
- Extracts JWT token from browser storage after login

### 2. **Smart Captcha Handling**
- **If NO captcha**: Login completes invisibly in ~10-15 seconds
- **If captcha detected**: Browser window automatically appears
- **User action**: Solve the captcha in the visible browser
- **Auto-continue**: Login proceeds automatically after captcha is solved
- **Auto-close**: Browser closes when token is extracted

### 3. **Token Extraction**
After successful login, Selenium extracts the Bearer token from:
- localStorage
- sessionStorage  
- Cookies
- Window object

This token is then used for all API requests.

## User Experience

### Without Captcha (Most Common)
```
You: Click "Test Login"
↓
App: "Logging in... (10-20 seconds)"
↓
[Headless browser opens → logs in → closes]
↓
App: "✓ Login successful!"
```

**You see**: Just loading indicator, then success!
**Duration**: ~10-15 seconds

### With Captcha (Occasional)
```
You: Click "Test Login"
↓
App: "Logging in... If captcha is detected, browser will open"
↓
[Browser window opens]
↓
Console: "🔐 CAPTCHA REQUIRED"
Console: "Please solve the captcha in the browser window..."
↓
You: Solve captcha in the visible browser
↓
[Browser redirects → extracts token → closes automatically]
↓
App: "✓ Login successful!"
```

**You see**: Browser window with captcha
**Your action**: Solve the captcha (usually just click checkbox)
**Duration**: ~30-60 seconds (depends on you)

## Technical Details

### Dependencies
- **selenium**: WebDriver automation
- **webdriver-manager**: Automatic ChromeDriver management (no manual driver download!)

### Browser Options
The system configures Chrome with:
- User-agent spoofing
- Anti-detection measures
- Stealth mode (hides automation flags)
- Proper window size and viewport

### Captcha Detection
Automatically detects captcha by looking for:
- reCAPTCHA iframes
- Captcha-related DOM elements
- Error messages mentioning captcha
- Page content analysis

### Retry Logic
- Max 3 attempts
- First attempt: Headless
- If captcha detected: Switches to headful mode
- Retries automatically on transient failures

## Configuration

### Headless vs Headful
By default, the system uses **headless-first** approach:
1. Try headless (invisible)
2. If captcha → switch to headful (visible)

You can modify this in `selenium_auth.py`:
```python
def login(self, email, password, max_retries=2):
    # Change retry logic here
```

### Timeout Settings
Current timeouts:
- Page load: 10 seconds
- Element wait: 10 seconds
- Captcha solving: 120 seconds (2 minutes)
- Normal login redirect: 10 seconds

Modify in `selenium_auth.py`:
```python
success = self._wait_for_login_success(timeout=10)  # change this
```

### Browser Selection
Currently uses Chrome. To use Firefox:
```python
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager

options = Options()
if headless:
    options.add_argument('--headless')

service = Service(GeckoDriverManager().install())
driver = webdriver.Firefox(service=service, options=options)
```

## Advantages Over Manual Token Method

| Aspect | Manual reCAPTCHA Token | Selenium Automation |
|--------|----------------------|---------------------|
| Setup complexity | High (copy token from console) | Low (just enter credentials) |
| Token expiry | 2 minutes | N/A (automatic) |
| Captcha handling | Manual | Semi-automatic (user solves, app continues) |
| User experience | Technical, error-prone | Simple, streamlined |
| Token refresh | Must get new token | Fully automatic |
| Maintenance | Tedious | Easy |

## Troubleshooting

### "ChromeDriver not found"
**Solution**: Install dependencies:
```bash
pip install selenium webdriver-manager
```

The app will automatically download the correct ChromeDriver for your Chrome version.

### "Login timeout"
**Possible causes**:
- Slow internet connection
- Stockbit website is slow
- Captcha appeared but you didn't solve it

**Solution**: 
- Check internet connection
- Look for browser window that might have opened
- Try again (retries happen automatically)

### "Could not find email input field"
**Possible causes**:
- Stockbit changed their login page structure
- Page didn't load properly

**Solution**:
- Try again
- Check if you can manually login at stockbit.com/login
- Update selectors in `selenium_auth.py` if Stockbit changed their HTML

### Browser window doesn't close
**Cause**: Unexpected error during token extraction

**Solution**: Window will close on next login attempt. Or close manually.

### "Token not found in browser storage"
**Cause**: Stockbit might store token differently now

**Solution**: Update `_extract_token_from_storage()` in `selenium_auth.py` to check additional locations:
```python
# Add more places to look
token = self.driver.execute_script("""
    return window.yourCustomTokenLocation || 
           document.cookie.match(/token=([^;]+)/)?.[1];
""")
```

## Security Considerations

### Credentials Storage
- Credentials stored in `config_data/credentials.json`
- **For production**: Encrypt this file or use environment variables
- **For development**: Fine as-is (local project)

### Browser Automation Detection
Selenium automation can be detected by:
- Navigator.webdriver flag
- Missing Chrome/browser properties
- Automation-specific headers

**Our mitigations**:
- Disabled webdriver flag
- Realistic user-agent
- Anti-automation extensions disabled
- Normal browser behavior simulation

### reCAPTCHA v3 Scores
Google reCAPTCHA v3 assigns risk scores. Selenium might get lower scores (more suspicious) than real human users. If login consistently fails:
- Use headful mode more often
- Add random delays
- Simulate mouse movements (advanced)

## Advanced Customization

### Add Mouse Movements
Make automation more human-like:
```python
from selenium.webdriver.common.action_chains import ActionChains

actions = ActionChains(self.driver)
actions.move_to_element(email_field)
actions.pause(0.5)
actions.click()
actions.perform()
```

### Add Random Delays
```python
import random
time.sleep(random.uniform(1, 3))  # random delay 1-3 seconds
```

### Capture Screenshots on Failure
```python
try:
    # login logic
except Exception as e:
    self.driver.save_screenshot('login_error.png')
    raise
```

### Use Existing Browser Session
Keep browser open between logins:
```python
class SeleniumAuthenticator:
    def __init__(self):
        self.driver = self._create_driver()  # create once
        self.persistent = True
    
    def __del__(self):
        if not self.persistent:
            self._close_driver()
```

## Performance Tips

1. **Reuse Browser Session**: Don't close browser between token refreshes if you need frequent logins
2. **Headless Mode**: Faster and uses less resources when captcha isn't needed
3. **Parallel Logins**: Don't! One Selenium instance at a time to avoid conflicts
4. **Cache Tokens**: Only refresh when actually expired (app already does this)

## Comparison with Other Methods

### Selenium (Current)
✅ Fully automated
✅ Handles reCAPTCHA v3 automatically
✅ Semi-automatic captcha solving
✅ No token expiry issues
❌ Slower (~10-15 seconds)
❌ Requires browser

### Manual reCAPTCHA Token
❌ Manual token copying
❌ Tokens expire in 2 minutes
❌ Technical for users
✅ Fast (~2 seconds)
✅ No browser needed

### Direct API with Hardcoded Token
❌ Doesn't work (reCAPTCHA required)
❌ Violates Stockbit ToS
❌ Will get blocked

## Future Improvements

Potential enhancements:
1. **2Captcha Integration**: Pay service to solve captchas automatically
2. **Undetected ChromeDriver**: More sophisticated anti-detection
3. **Proxy Support**: Rotate IPs if needed
4. **Session Persistence**: Save browser session to disk
5. **Headless Detection Bypass**: More advanced stealth techniques

## Conclusion

The Selenium-based authentication system provides the best balance of:
- Automation (minimal manual work)
- Reliability (handles reCAPTCHA and captcha)
- User experience (simple and intuitive)
- Maintenance (easy to update)

For most users, this "just works" with no configuration needed. When captcha appears, it's obvious what to do. Much better than manually copying tokens!

