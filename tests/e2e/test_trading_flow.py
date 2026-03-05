"""
E2E Test: Trading Flow

Tests the complete trading workflow from login to order placement.
"""
import pytest
from playwright.sync_api import Page, expect
from tests.e2e.playwright_config import BASE_URL, TIMEOUT


class TestTradingFlow:
    """Test suite for trading functionality."""
    
    @pytest.fixture(autouse=True)
    def login(self, page: Page):
        """Automatically login before each test."""
        page.goto(BASE_URL)
        page.fill("#login-user", "testuser")
        page.fill("#login-pass", "testpass123")
        page.click("#login-form button[type='submit']")
        expect(page.locator("#app-view")).to_be_visible(timeout=TIMEOUT)
    
    def test_navigate_to_trading_panel(self, page: Page):
        """Test navigation to trading panel."""
        # Click on trading tab
        page.click("button.nav-btn[data-panel='trading']")
        
        # Verify trading panel is visible
        expect(page.locator("#panel-trading")).to_be_visible()
        
        # Check for trading form elements
        expect(page.locator("#order-form")).to_be_visible()
    
    def test_dashboard_displays_account_summary(self, page: Page):
        """Test that dashboard shows account summary."""
        # Dashboard should be the default view
        expect(page.locator("#panel-dashboard")).to_be_visible()
        
        # Check summary cards are present
        expect(page.locator("#sum-cash")).to_be_visible()
        expect(page.locator("#sum-locked")).to_be_visible()
        expect(page.locator("#sum-holdings")).to_be_visible()
        expect(page.locator("#sum-yield")).to_be_visible()
        
        # Verify values are displayed (not empty)
        expect(page.locator("#sum-cash")).not_to_be_empty()
    
    def test_market_data_panel_loads(self, page: Page):
        """Test that market data panel loads correctly."""
        # Navigate to market panel
        page.click("button.nav-btn[data-panel='market']")
        
        # Verify market panel is visible
        expect(page.locator("#panel-market")).to_be_visible()
        
        # Check for market data elements (this depends on your HTML structure)
        # Adjust selectors based on actual implementation
    
    def test_view_holdings(self, page: Page):
        """Test viewing holdings list."""
        # Should be on dashboard by default
        expect(page.locator("#panel-dashboard")).to_be_visible()
        
        # Click on holdings sub-tab
        page.click("button.sub-tab[data-sub='holdings']")
        
        # Verify holdings sub-panel is visible
        expect(page.locator("#sub-holdings")).to_be_visible()
    
    def test_view_cashflow_history(self, page: Page):
        """Test viewing cashflow history."""
        # Should be on dashboard by default
        expect(page.locator("#panel-dashboard")).to_be_visible()
        
        # Click on cashflow sub-tab
        page.click("button.sub-tab[data-sub='cashflow']")
        
        # Verify cashflow sub-panel is visible
        expect(page.locator("#sub-cashflow")).to_be_visible()
    
    def test_change_password_form_exists(self, page: Page):
        """Test that password change form is accessible."""
        # Should be on dashboard by default
        expect(page.locator("#panel-dashboard")).to_be_visible()
        
        # Password management should be the default sub-tab
        expect(page.locator("#sub-password")).to_be_visible()
        
        # Check form elements
        expect(page.locator("#change-pw-form")).to_be_visible()
        expect(page.locator("#pw-old")).to_be_visible()
        expect(page.locator("#pw-new")).to_be_visible()
        expect(page.locator("#pw-confirm")).to_be_visible()
    
    def test_leaderboard_accessible(self, page: Page):
        """Test that leaderboard is accessible."""
        # Navigate to leaderboard
        page.click("button.nav-btn[data-panel='leaderboard']")
        
        # Verify leaderboard panel is visible
        expect(page.locator("#panel-leaderboard")).to_be_visible()
    
    def test_websocket_connection_establishes(self, page: Page):
        """Test that WebSocket connection is established."""
        # Wait a moment for WebSocket to connect
        page.wait_for_timeout(2000)
        
        # Check if clock is ticking (indicates WebSocket is working)
        clock = page.locator("#clock")
        if clock.is_visible():
            expect(clock).not_to_be_empty()
    
    def test_navigation_preserves_login_state(self, page: Page):
        """Test that navigating between panels preserves login state."""
        # Navigate through different panels
        page.click("button.nav-btn[data-panel='market']")
        page.wait_for_timeout(500)
        
        page.click("button.nav-btn[data-panel='trading']")
        page.wait_for_timeout(500)
        
        page.click("button.nav-btn[data-panel='leaderboard']")
        page.wait_for_timeout(500)
        
        page.click("button.nav-btn[data-panel='dashboard']")
        page.wait_for_timeout(500)
        
        # User should still be logged in
        expect(page.locator("#user-display")).to_be_visible()
        expect(page.locator("#user-display")).to_contain_text("testuser")
        
        # App view should still be visible
        expect(page.locator("#app-view")).to_be_visible()
