# E2E Testing Quick Start

## 🚀 Get Started in 5 Minutes

### 1. Install Dependencies

```powershell
# Install test dependencies
pip install -e ".[test]"

# Install Playwright browser
playwright install chromium
```

### 2. Start the Application

```powershell
# Start the backend (in a separate terminal)
python -m uvicorn src.app:app --reload
```

### 3. Run Your First Test

```powershell
# Run all E2E tests
pytest tests/e2e/ -v

# Or run in headed mode to see the browser
$env:HEADLESS="false"; pytest tests/e2e/ -v
```

### 4. Run Specific Tests

```powershell
# Run only login tests
pytest tests/e2e/test_login_flow.py -v

# Run only trading tests
pytest tests/e2e/test_trading_flow.py -v

# Run a single test
pytest tests/e2e/test_login_flow.py::TestLoginFlow::test_login_with_valid_credentials -v
```

## 📝 Write Your First Test

Create a new file `tests/e2e/test_my_feature.py`:

```python
import pytest
from playwright.sync_api import Page, expect
from tests.e2e.helpers import TestHelpers

class TestMyFeature:
    """Test suite for my feature."""
    
    def test_feature_works(self, page: Page):
        """Test that my feature works correctly."""
        # Login
        TestHelpers.login(page, "testuser", "testpass123")
        
        # Navigate to panel
        TestHelpers.navigate_to_panel(page, "dashboard")
        
        # Verify something
        expect(page.locator("#my-element")).to_be_visible()
```

## 🔍 Common Commands

```powershell
# Run all tests with verbose output
pytest tests/e2e/ -v

# Run tests and see browser
$env:HEADLESS="false"; pytest tests/e2e/ -v

# Run tests against different URL
$env:BASE_URL="http://staging.example.com"; pytest tests/e2e/ -v

# Run tests and generate HTML report
pytest tests/e2e/ --html=report.html --self-contained-html

# Run a specific test class
pytest tests/e2e/test_login_flow.py::TestLoginFlow -v

# Run tests matching a pattern
pytest tests/e2e/ -k "login" -v
```

## 🛠️ Useful Helpers

```python
from tests.e2e.helpers import TestHelpers

# Login
TestHelpers.login(page, "username", "password")

# Logout  
TestHelpers.logout(page)

# Navigate
TestHelpers.navigate_to_panel(page, "dashboard")

# Get account info
balance = TestHelpers.get_account_balance(page)

# Take screenshot for debugging
TestHelpers.take_screenshot(page, "debug")
```

## 📖 Full Documentation

See [E2E Testing Guide](./e2e-testing-guide.md) for complete documentation.

## ❓ Troubleshooting

**Tests timeout?**
- Check application is running at http://localhost:8000
- Increase timeout in test: `expect(element).to_be_visible(timeout=60000)`

**Element not found?**
- Run in headed mode to see what's happening
- Check selector is correct: `page.locator("#my-element")`

**Browser not installed?**
- Run: `playwright install chromium`

**Need help?**
- Check [E2E Testing Guide](./e2e-testing-guide.md)
- Visit https://playwright.dev/python/
