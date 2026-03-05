"""
E2E Test Template

Copy this file to create new tests.
Replace "MyFeature" and "test_something" with your actual feature/test names.
"""
import pytest
from playwright.sync_api import Page, expect
from tests.e2e.playwright_config import BASE_URL, TIMEOUT
from tests.e2e.helpers import TestHelpers, VisualHelpers


class TestMyFeature:
    """
    Test suite for [Feature Name].
    
    Tests:
    - Basic functionality works
    - Edge cases are handled
    - Error states display correctly
    """
    
    @pytest.fixture(autouse=True)
    def setup(self, page: Page):
        """Setup that runs before each test in this class."""
        # Example: Login before each test
        TestHelpers.login(page, "testuser", "testpass123")
        yield
        # Teardown code here (if needed)
    
    def test_something_works(self, page: Page):
        """
        Test that [specific functionality] works correctly.
        
        Steps:
        1. Navigate to feature
        2. Perform action
        3. Verify result
        """
        # Navigate to the feature
        TestHelpers.navigate_to_panel(page, "dashboard")
        
        # Perform action
        page.click("#my-button")
        
        # Verify result
        expect(page.locator("#result")).to_be_visible()
        expect(page.locator("#result")).to_contain_text("Expected Text")
    
    def test_edge_case_handled(self, page: Page):
        """Test that edge case is handled correctly."""
        # Setup edge case condition
        # ...
        
        # Perform action
        # ...
        
        # Verify proper handling
        # ...
        pass
    
    def test_error_displayed(self, page: Page):
        """Test that error is displayed when something goes wrong."""
        # Trigger error condition
        # ...
        
        # Verify error message
        TestHelpers.wait_for_toast(page, "Error message")


# Example: Test without auto-login
class TestPublicFeature:
    """Test suite for features that don't require login."""
    
    def test_public_page_accessible(self, page: Page):
        """Test that public page is accessible."""
        page.goto(BASE_URL)
        
        # Verify page loads
        expect(page.locator("#login-view")).to_be_visible()
        expect(page).to_have_title("📈 證券交易模擬遊戲")


# Example: Test with custom fixture
class TestAdvancedFeature:
    """Test suite with custom setup."""
    
    @pytest.fixture
    def logged_in_with_orders(self, page: Page):
        """Custom fixture that sets up specific state."""
        # Login
        TestHelpers.login(page, "testuser", "testpass123")
        
        # Setup: Place some orders
        TestHelpers.navigate_to_panel(page, "trading")
        # ... place orders
        
        yield page
        
        # Cleanup: Cancel orders
        # ... cleanup code
    
    def test_with_custom_setup(self, logged_in_with_orders: Page):
        """Test that uses custom fixture."""
        page = logged_in_with_orders
        
        # Test logic here
        # ...


# Example: Parameterized test
class TestWithParameters:
    """Test suite with parameterized tests."""
    
    @pytest.mark.parametrize("username,password,should_succeed", [
        ("valid_user", "valid_pass", True),
        ("invalid_user", "wrong_pass", False),
        ("", "", False),
    ])
    def test_login_scenarios(self, page: Page, username: str, password: str, should_succeed: bool):
        """Test different login scenarios."""
        page.goto(BASE_URL)
        
        if username:
            page.fill("#login-user", username)
        if password:
            page.fill("#login-pass", password)
        
        page.click("#login-form button[type='submit']")
        
        if should_succeed:
            expect(page.locator("#app-view")).to_be_visible(timeout=TIMEOUT)
        else:
            expect(page.locator("#login-error")).to_be_visible()


# Example: Testing API interactions
class TestApiIntegration:
    """Test suite for frontend-backend integration."""
    
    def test_order_submission_calls_api(self, page: Page):
        """Test that submitting order calls the backend API."""
        TestHelpers.login(page, "testuser", "testpass123")
        TestHelpers.navigate_to_panel(page, "trading")
        
        # Setup API response listener
        responses = []
        page.on("response", lambda response: responses.append(response))
        
        # Trigger action that calls API
        # page.click("#submit-order")
        
        # Verify API was called
        # api_responses = [r for r in responses if "/api/v1/orders" in r.url]
        # assert len(api_responses) > 0, "Expected API call to /api/v1/orders"


# Example: Testing with screenshots
class TestVisualValidation:
    """Test suite with visual validation."""
    
    def test_dashboard_layout(self, page: Page):
        """Test that dashboard layout is correct."""
        TestHelpers.login(page, "testuser", "testpass123")
        
        # Take screenshot for visual comparison
        TestHelpers.take_screenshot(page, "dashboard_layout")
        
        # Verify key elements are visible
        VisualHelpers.assert_element_visible(page, "#sum-cash", "Cash balance card")
        VisualHelpers.assert_element_visible(page, "#sum-locked", "Locked funds card")
        VisualHelpers.assert_element_visible(page, "#sum-holdings", "Holdings card")
        VisualHelpers.assert_element_visible(page, "#sum-yield", "Yield card")


# Tips:
# 1. Use descriptive test names that explain what is being tested
# 2. Use helpers from helpers.py instead of repeating code
# 3. Use expect() for assertions, never time.sleep()
# 4. Make tests independent - they should work in any order
# 5. Add docstrings to explain test purpose
# 6. Group related tests into classes
# 7. Use fixtures for common setup
# 8. Take screenshots on complex UI tests for debugging
