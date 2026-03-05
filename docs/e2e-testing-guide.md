# E2E Testing Guide for Trading Simulation Game

## 📋 Table of Contents

1. [Overview](#overview)
2. [Setup Instructions](#setup-instructions)
3. [Running Tests](#running-tests)
4. [Writing Tests](#writing-tests)
5. [Test Structure](#test-structure)
6. [Helper Functions](#helper-functions)
7. [Configuration](#configuration)
8. [Best Practices](#best-practices)
9. [Troubleshooting](#troubleshooting)

---

## 🎯 Overview

This guide covers End-to-End (E2E) testing for the trading simulation game frontend. We use **Playwright** with **pytest** to automate browser interactions and verify the application works correctly from a user's perspective.

### What is E2E Testing?

E2E tests simulate real user interactions with your application:
- Opening the browser
- Clicking buttons
- Filling forms
- Navigating between pages
- Verifying content appears correctly

### Why Playwright?

- ✅ Fast and reliable
- ✅ Supports multiple browsers (Chrome, Firefox, Safari)
- ✅ Excellent developer experience
- ✅ Built-in waiting and retry mechanisms
- ✅ Great debugging tools

---

## 🚀 Setup Instructions

### Step 1: Install Dependencies

```powershell
# Navigate to project root
cd "d:\0___NVIDIA_ALL\[ My_Lab ]\0_GitHub_Clone\hifreq-tradingame"

# Install test dependencies
pip install -e ".[test]"

# Install Playwright browsers
playwright install chromium
```

### Step 2: Verify Installation

```powershell
# Check pytest is installed
pytest --version

# Check playwright is installed
playwright --version
```

### Step 3: Start the Application

Before running E2E tests, you need the application running:

```powershell
# In one terminal, start the backend
python -m uvicorn src.app:app --reload
```

The application should be available at `http://localhost:8000`

---

## 🏃 Running Tests

### Run All E2E Tests

```powershell
# Run all E2E tests
pytest tests/e2e/ -v

# Run with HTML report
pytest tests/e2e/ --html=report.html --self-contained-html
```

### Run Specific Test File

```powershell
# Run only login tests
pytest tests/e2e/test_login_flow.py -v

# Run only trading tests
pytest tests/e2e/test_trading_flow.py -v
```

### Run Specific Test

```powershell
# Run a single test by name
pytest tests/e2e/test_login_flow.py::TestLoginFlow::test_login_with_valid_credentials -v
```

### Run in Headed Mode (See Browser)

```powershell
# Run with visible browser (great for debugging)
$env:HEADLESS="false"; pytest tests/e2e/ -v
```

### Run with Different Base URL

```powershell
# Test against a different environment
$env:BASE_URL="http://staging.example.com"; pytest tests/e2e/ -v
```

---

## ✍️ Writing Tests

### Basic Test Structure

```python
import pytest
from playwright.sync_api import Page, expect
from tests.e2e.playwright_config import BASE_URL

class TestMyFeature:
    """Test suite for my feature."""
    
    def test_something(self, page: Page):
        """Test description."""
        # Navigate to page
        page.goto(BASE_URL)
        
        # Interact with elements
        page.click("#my-button")
        page.fill("#my-input", "test value")
        
        # Assert expected behavior
        expect(page.locator("#result")).to_be_visible()
        expect(page.locator("#result")).to_have_text("Expected Text")
```

### Using Helper Functions

```python
from tests.e2e.helpers import TestHelpers

class TestDashboard:
    def test_view_account_balance(self, page: Page):
        # Login using helper
        TestHelpers.login(page, "testuser", "testpass123")
        
        # Navigate using helper
        TestHelpers.navigate_to_panel(page, "dashboard")
        
        # Get balance using helper
        balance = TestHelpers.get_account_balance(page)
        assert balance is not None
```

### Common Patterns

#### 1. Click and Wait

```python
# Click and wait for navigation
page.click("#submit-button")
expect(page.locator("#success-message")).to_be_visible()
```

#### 2. Fill Form

```python
# Fill multiple fields
page.fill("#username", "testuser")
page.fill("#password", "password123")
page.fill("#email", "test@example.com")
page.click("button[type='submit']")
```

#### 3. Check Visibility

```python
# Check element is visible
expect(page.locator("#my-element")).to_be_visible()

# Check element is hidden
expect(page.locator("#my-element")).to_be_hidden()
```

#### 4. Check Text Content

```python
# Exact match
expect(page.locator("#title")).to_have_text("Welcome")

# Contains text
expect(page.locator("#message")).to_contain_text("Success")
```

#### 5. Wait for API Responses

```python
# Wait for specific API call
with page.expect_response("**/api/v1/account/**"):
    page.click("#refresh-button")
```

---

## 📁 Test Structure

```
tests/e2e/
├── __init__.py              # Package marker
├── conftest.py              # Pytest fixtures and configuration
├── playwright_config.py     # Test configuration (URLs, timeouts, etc.)
├── helpers.py               # Reusable helper functions
├── test_login_flow.py       # Login/authentication tests
├── test_trading_flow.py     # Trading workflow tests
├── screenshots/             # Auto-generated screenshots on failure
└── videos/                  # Optional video recordings
```

### Test File Organization

Each test file should focus on a specific feature or workflow:

- **test_login_flow.py** - Authentication and session management
- **test_trading_flow.py** - Trading operations (order placement, cancellation)
- **test_account_management.py** - Account operations (password change, balance checks)
- **test_market_data.py** - Market data viewing and updates

---

## 🛠️ Helper Functions

### Available Helpers

#### TestHelpers Class

```python
from tests.e2e.helpers import TestHelpers

# Login
TestHelpers.login(page, "username", "password")

# Logout
TestHelpers.logout(page)

# Navigate to panel
TestHelpers.navigate_to_panel(page, "dashboard")  # or "market", "trading", "leaderboard"

# Navigate to sub-panel
TestHelpers.navigate_to_sub_panel(page, "holdings")  # or "cashflow", "password"

# Get account info
balance = TestHelpers.get_account_balance(page)
locked = TestHelpers.get_locked_funds(page)
holdings = TestHelpers.get_holdings_value(page)
yield_pct = TestHelpers.get_yield_percentage(page)

# Wait for toast notification
TestHelpers.wait_for_toast(page, "Success")

# Take screenshot
TestHelpers.take_screenshot(page, "my_screenshot")

# Wait for API response
TestHelpers.wait_for_api_response(page, "/api/v1/orders")

# Mock API response
TestHelpers.mock_api_response(page, "account/balance", {"balance": 100000})
```

#### VisualHelpers Class

```python
from tests.e2e.helpers import VisualHelpers

# Assert with descriptions
VisualHelpers.assert_element_visible(page, "#dashboard", "Dashboard is visible")
VisualHelpers.assert_element_hidden(page, "#login-view", "Login view is hidden")
VisualHelpers.assert_text_contains(page, "#title", "Welcome", "Title shows welcome")
```

---

## ⚙️ Configuration

### Environment Variables

Configure tests using environment variables:

```powershell
# Base URL (default: http://localhost:8000)
$env:BASE_URL="http://localhost:8000"

# Headless mode (default: true)
$env:HEADLESS="false"  # Set to "false" to see browser
```

### playwright_config.py

Modify `tests/e2e/playwright_config.py` to change:

- `BASE_URL` - Application URL
- `TIMEOUT` - Default timeout for operations (milliseconds)
- `HEADLESS` - Run with/without visible browser
- `VIEWPORT` - Browser window size
- `BROWSERS` - Which browsers to test (chromium, firefox, webkit)

---

## 📚 Best Practices

### 1. Use Descriptive Test Names

```python
# ❌ Bad
def test_1(page):
    pass

# ✅ Good
def test_login_with_valid_credentials_shows_dashboard(page):
    pass
```

### 2. Use Page Object Pattern

```python
class LoginPage:
    def __init__(self, page: Page):
        self.page = page
        self.username_input = page.locator("#login-user")
        self.password_input = page.locator("#login-pass")
        self.submit_button = page.locator("#login-form button[type='submit']")
    
    def login(self, username: str, password: str):
        self.username_input.fill(username)
        self.password_input.fill(password)
        self.submit_button.click()
```

### 3. Wait for Elements Properly

```python
# ❌ Bad - using sleep
import time
page.click("#button")
time.sleep(2)  # Don't do this!

# ✅ Good - using expect
page.click("#button")
expect(page.locator("#result")).to_be_visible()
```

### 4. Use Fixtures for Setup

```python
@pytest.fixture
def logged_in_page(page: Page):
    """Fixture that provides a logged-in page."""
    TestHelpers.login(page)
    return page

def test_dashboard(logged_in_page: Page):
    # Test starts already logged in
    TestHelpers.navigate_to_panel(logged_in_page, "dashboard")
    # ... rest of test
```

### 5. Clean Up Test Data

```python
def test_place_order(page: Page):
    # Setup
    TestHelpers.login(page)
    
    # Test
    # ... place order logic
    
    # Cleanup (if needed)
    # ... cancel order or reset state
```

### 6. Make Tests Independent

Each test should:
- Start from a clean state
- Not depend on other tests
- Be able to run in any order
- Clean up after itself

### 7. Use Meaningful Assertions

```python
# ❌ Bad
assert page.locator("#balance").inner_text() != ""

# ✅ Good
balance_text = page.locator("#balance").inner_text()
assert balance_text.startswith("$"), f"Expected balance to start with $, got: {balance_text}"
assert float(balance_text[1:].replace(",", "")) >= 0, "Balance should be non-negative"
```

---

## 🐛 Troubleshooting

### Common Issues

#### 1. Test Times Out

**Problem**: Test waits indefinitely and times out.

**Solution**:
```python
# Increase timeout for slow operations
expect(page.locator("#slow-element")).to_be_visible(timeout=60000)  # 60 seconds
```

#### 2. Element Not Found

**Problem**: `Error: Element not found`

**Solutions**:
- Check the selector is correct
- Verify element exists in the DOM
- Wait for the element to appear:

```python
# Wait for element before interacting
page.wait_for_selector("#my-element", state="visible")
page.click("#my-element")
```

#### 3. Flaky Tests

**Problem**: Tests pass sometimes, fail other times.

**Solutions**:
- Use Playwright's built-in waiting (expect)
- Avoid using `time.sleep()`
- Wait for specific conditions:

```python
# Wait for network to be idle
page.wait_for_load_state("networkidle")

# Wait for API response
with page.expect_response("**/api/v1/**"):
    page.click("#submit")
```

#### 4. Application Not Running

**Problem**: Tests fail because app isn't running.

**Solution**:
```powershell
# Start the application first
python -m uvicorn src.app:app --reload
```

#### 5. Browser Not Installed

**Problem**: `Error: Executable doesn't exist at ...`

**Solution**:
```powershell
# Install browsers
playwright install chromium
```

### Debugging Tips

#### 1. Run in Headed Mode

```powershell
$env:HEADLESS="false"; pytest tests/e2e/test_login_flow.py -v
```

#### 2. Use Playwright Inspector

```powershell
# Run with inspector (step through test)
playwright codegen http://localhost:8000
```

#### 3. Take Screenshots

```python
# Manual screenshot
page.screenshot(path="debug.png")

# Or use helper
TestHelpers.take_screenshot(page, "debug_screenshot")
```

#### 4. Print Page Content

```python
# Print HTML content
print(page.content())

# Print specific element
print(page.locator("#my-element").inner_html())
```

#### 5. Check Console Logs

```python
def test_with_console_logging(page: Page):
    page.on("console", lambda msg: print(f"Console: {msg.text}"))
    page.goto(BASE_URL)
    # ... rest of test
```

### Getting Help

1. **Check Playwright Documentation**: https://playwright.dev/python/
2. **Check pytest-playwright Documentation**: https://github.com/microsoft/playwright-pytest
3. **Review Test Examples**: Look at existing tests in `tests/e2e/`
4. **Use Verbose Output**: Add `-v` flag to pytest commands

---

## 📊 Running Tests in CI/CD

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e ".[test]"
          playwright install --with-deps chromium
      
      - name: Start application
        run: |
          python -m uvicorn src.app:app &
          sleep 5
      
      - name: Run E2E tests
        run: pytest tests/e2e/ -v
      
      - name: Upload screenshots on failure
        if: failure()
        uses: actions/upload-artifact@v3
        with:
          name: screenshots
          path: tests/e2e/screenshots/
```

---

## 🎓 Next Steps

1. **Run your first test**:
   ```powershell
   pytest tests/e2e/test_login_flow.py -v
   ```

2. **Watch it run in the browser**:
   ```powershell
   $env:HEADLESS="false"; pytest tests/e2e/test_login_flow.py -v
   ```

3. **Write your own test** for a new feature

4. **Explore the helpers** in `tests/e2e/helpers.py`

5. **Read the Playwright docs**: https://playwright.dev/python/

---

## 📝 Summary

- **Setup**: Install dependencies with `pip install -e ".[test]"` and `playwright install chromium`
- **Run**: Use `pytest tests/e2e/` to run all E2E tests
- **Write**: Create test files in `tests/e2e/` following the existing patterns
- **Debug**: Run in headed mode with `$env:HEADLESS="false"`
- **Help**: Use helpers from `tests/e2e/helpers.py` for common operations

Happy testing! 🎉
