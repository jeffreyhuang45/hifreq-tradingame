# E2E Tests

End-to-End tests for the Trading Simulation Game frontend using Playwright.

## 📁 Structure

```
tests/e2e/
├── __init__.py              # Package marker
├── conftest.py              # Pytest fixtures (browser, page, etc.)
├── playwright_config.py     # Configuration (URLs, timeouts, browsers)
├── helpers.py               # Reusable helper functions
├── test_login_flow.py       # Login/authentication tests
├── test_trading_flow.py     # Trading workflow tests
├── screenshots/             # Screenshots on test failure
└── videos/                  # Video recordings (optional)
```

## 🚀 Quick Start

```powershell
# Install dependencies
pip install -e ".[test]"
playwright install chromium

# Run tests
pytest tests/e2e/ -v

# Run with visible browser
$env:HEADLESS="false"; pytest tests/e2e/ -v
```

## 📝 Test Files

### test_login_flow.py
Tests for authentication and session management:
- Login page loads correctly
- Login with valid credentials
- Login with invalid credentials
- Form validation
- Logout functionality

### test_trading_flow.py
Tests for trading workflow:
- Navigate to trading panel
- View account dashboard
- View market data
- View holdings
- View cashflow history
- Change password form
- Leaderboard access
- WebSocket connection
- Navigation state preservation

## 🛠️ Helper Functions

See [helpers.py](./helpers.py) for available helper functions:

```python
from tests.e2e.helpers import TestHelpers

# Login/logout
TestHelpers.login(page, "username", "password")
TestHelpers.logout(page)

# Navigation
TestHelpers.navigate_to_panel(page, "dashboard")
TestHelpers.navigate_to_sub_panel(page, "holdings")

# Account info
balance = TestHelpers.get_account_balance(page)
locked = TestHelpers.get_locked_funds(page)

# Utilities
TestHelpers.take_screenshot(page, "name")
TestHelpers.wait_for_toast(page, "Success message")
```

## ⚙️ Configuration

Configure via environment variables:

```powershell
# Base URL (default: http://localhost:8000)
$env:BASE_URL="http://localhost:8000"

# Headless mode (default: true)
$env:HEADLESS="false"
```

Or modify [playwright_config.py](./playwright_config.py):
- `BASE_URL` - Application URL
- `TIMEOUT` - Default timeout (milliseconds)
- `VIEWPORT` - Browser window size
- `BROWSERS` - Which browsers to test

## 📚 Documentation

- **Quick Start**: [docs/e2e-quick-start.md](../../docs/e2e-quick-start.md)
- **Full Guide**: [docs/e2e-testing-guide.md](../../docs/e2e-testing-guide.md)
- **Playwright Docs**: https://playwright.dev/python/

## 🐛 Debugging

```powershell
# Run with visible browser
$env:HEADLESS="false"; pytest tests/e2e/ -v

# Run specific test
pytest tests/e2e/test_login_flow.py::TestLoginFlow::test_login_with_valid_credentials -v

# Use Playwright inspector
playwright codegen http://localhost:8000
```

Screenshots are automatically saved to `screenshots/` on test failure.

## ✅ Best Practices

1. **Use helpers** - Reuse code from `helpers.py`
2. **Use expect()** - Never use `time.sleep()`
3. **Descriptive names** - Name tests clearly
4. **Independent tests** - Tests should not depend on each other
5. **Clean state** - Each test starts fresh

## 🔧 Adding New Tests

1. Create a new test file: `test_my_feature.py`
2. Import required modules:
   ```python
   import pytest
   from playwright.sync_api import Page, expect
   from tests.e2e.helpers import TestHelpers
   ```
3. Create test class and methods:
   ```python
   class TestMyFeature:
       def test_something(self, page: Page):
           TestHelpers.login(page)
           # ... your test logic
   ```
4. Run your test:
   ```powershell
   pytest tests/e2e/test_my_feature.py -v
   ```
