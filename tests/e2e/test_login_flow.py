"""
E2E Test: Login Flow

Tests the authentication flow from the frontend perspective.
"""
import pytest
from playwright.sync_api import Page, expect
from tests.e2e.playwright_config import BASE_URL, TIMEOUT


class TestLoginFlow:
    """Test suite for login and authentication."""
    
    def test_login_page_loads(self, page: Page):
        """Test that the login page loads correctly."""
        page.goto(BASE_URL)
        
        # Check that login view is visible
        expect(page.locator("#login-view")).to_be_visible()
        
        # Check for login form elements
        expect(page.locator("#login-user")).to_be_visible()
        expect(page.locator("#login-pass")).to_be_visible()
        expect(page.locator("#login-form button[type='submit']")).to_be_visible()
        
        # Check title
        expect(page).to_have_title("📈 證券交易模擬遊戲")
    
    def test_login_with_valid_credentials(self, page: Page):
        """Test successful login with valid credentials."""
        page.goto(BASE_URL)
        
        # Fill in login form
        page.fill("#login-user", "testuser")
        page.fill("#login-pass", "testpass123")
        
        # Submit form
        page.click("#login-form button[type='submit']")
        
        # Wait for app view to appear
        expect(page.locator("#app-view")).to_be_visible(timeout=TIMEOUT)
        
        # Verify login view is hidden
        expect(page.locator("#login-view")).to_be_hidden()
        
        # Check that user display shows username
        expect(page.locator("#user-display")).to_contain_text("testuser")
        
        # Verify main navigation is present
        expect(page.locator(".main-nav")).to_be_visible()
    
    def test_login_with_invalid_credentials(self, page: Page):
        """Test login failure with invalid credentials."""
        page.goto(BASE_URL)
        
        # Fill in login form with invalid credentials
        page.fill("#login-user", "invaliduser")
        page.fill("#login-pass", "wrongpassword")
        
        # Submit form
        page.click("#login-form button[type='submit']")
        
        # Check for error message
        error_msg = page.locator("#login-error")
        expect(error_msg).to_be_visible(timeout=5000)
        expect(error_msg).not_to_be_empty()
        
        # Verify still on login page
        expect(page.locator("#login-view")).to_be_visible()
        expect(page.locator("#app-view")).to_be_hidden()
    
    def test_login_with_empty_fields(self, page: Page):
        """Test form validation for empty fields."""
        page.goto(BASE_URL)
        
        # Try to submit with empty fields
        page.click("#login-form button[type='submit']")
        
        # HTML5 validation should prevent submission
        # The form should not submit and user stays on login page
        expect(page.locator("#login-view")).to_be_visible()
    
    def test_logout_functionality(self, page: Page):
        """Test that logout works correctly."""
        # First login
        page.goto(BASE_URL)
        page.fill("#login-user", "testuser")
        page.fill("#login-pass", "testpass123")
        page.click("#login-form button[type='submit']")
        
        # Wait for app to load
        expect(page.locator("#app-view")).to_be_visible(timeout=TIMEOUT)
        
        # Click logout
        page.click("#logout-btn")
        
        # Should return to login page
        expect(page.locator("#login-view")).to_be_visible()
        expect(page.locator("#app-view")).to_be_hidden()
        
        # Password field should be cleared
        expect(page.locator("#login-pass")).to_have_value("")
