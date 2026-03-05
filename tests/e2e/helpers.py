"""
Helper utilities for E2E tests.

Provides reusable functions for common test scenarios.
"""
from typing import Optional
from playwright.sync_api import Page, expect
from tests.e2e.playwright_config import BASE_URL, TIMEOUT


class TestHelpers:
    """Helper class for E2E testing operations."""
    
    @staticmethod
    def login(page: Page, username: str = "testuser", password: str = "testpass123"):
        """
        Login to the application.
        
        Args:
            page: Playwright page object
            username: Username to login with
            password: Password to login with
        """
        page.goto(BASE_URL)
        page.fill("#login-user", username)
        page.fill("#login-pass", password)
        page.click("#login-form button[type='submit']")
        expect(page.locator("#app-view")).to_be_visible(timeout=TIMEOUT)
    
    @staticmethod
    def logout(page: Page):
        """
        Logout from the application.
        
        Args:
            page: Playwright page object
        """
        page.click("#logout-btn")
        expect(page.locator("#login-view")).to_be_visible()
    
    @staticmethod
    def navigate_to_panel(page: Page, panel: str):
        """
        Navigate to a specific panel.
        
        Args:
            page: Playwright page object
            panel: Panel name (dashboard, market, trading, leaderboard)
        """
        page.click(f"button.nav-btn[data-panel='{panel}']")
        expect(page.locator(f"#panel-{panel}")).to_be_visible()
    
    @staticmethod
    def navigate_to_sub_panel(page: Page, sub_panel: str):
        """
        Navigate to a sub-panel within dashboard.
        
        Args:
            page: Playwright page object
            sub_panel: Sub-panel name (password, cashflow, holdings)
        """
        page.click(f"button.sub-tab[data-sub='{sub_panel}']")
        expect(page.locator(f"#sub-{sub_panel}")).to_be_visible()
    
    @staticmethod
    def wait_for_toast(page: Page, message: Optional[str] = None, timeout: int = 5000):
        """
        Wait for a toast notification to appear.
        
        Args:
            page: Playwright page object
            message: Optional specific message to wait for
            timeout: Timeout in milliseconds
        """
        toast = page.locator(".toast")
        expect(toast).to_be_visible(timeout=timeout)
        if message:
            expect(toast).to_contain_text(message)
    
    @staticmethod
    def get_account_balance(page: Page) -> str:
        """
        Get the current account cash balance.
        
        Args:
            page: Playwright page object
            
        Returns:
            Balance as string
        """
        return page.locator("#sum-cash").inner_text()
    
    @staticmethod
    def get_locked_funds(page: Page) -> str:
        """
        Get the current locked funds amount.
        
        Args:
            page: Playwright page object
            
        Returns:
            Locked funds as string
        """
        return page.locator("#sum-locked").inner_text()
    
    @staticmethod
    def get_holdings_value(page: Page) -> str:
        """
        Get the current holdings market value.
        
        Args:
            page: Playwright page object
            
        Returns:
            Holdings value as string
        """
        return page.locator("#sum-holdings").inner_text()
    
    @staticmethod
    def get_yield_percentage(page: Page) -> str:
        """
        Get the current yield percentage.
        
        Args:
            page: Playwright page object
            
        Returns:
            Yield percentage as string
        """
        return page.locator("#sum-yield").inner_text()
    
    @staticmethod
    def take_screenshot(page: Page, name: str):
        """
        Take a screenshot for debugging.
        
        Args:
            page: Playwright page object
            name: Screenshot filename (without extension)
        """
        from tests.e2e.playwright.config import SCREENSHOTS_DIR
        screenshot_path = SCREENSHOTS_DIR / f"{name}.png"
        page.screenshot(path=str(screenshot_path))
        print(f"Screenshot saved: {screenshot_path}")
    
    @staticmethod
    def wait_for_api_response(page: Page, endpoint_pattern: str, timeout: int = 10000):
        """
        Wait for a specific API response.
        
        Args:
            page: Playwright page object
            endpoint_pattern: Pattern to match in the API endpoint
            timeout: Timeout in milliseconds
        """
        with page.expect_response(
            lambda response: endpoint_pattern in response.url,
            timeout=timeout
        ) as response_info:
            pass
        return response_info.value
    
    @staticmethod
    def check_no_console_errors(page: Page):
        """
        Check that there are no JavaScript console errors.
        
        Args:
            page: Playwright page object
        """
        console_errors = []
        
        def handle_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)
        
        page.on("console", handle_console)
        
        # Return the list of errors for assertion
        return console_errors
    
    @staticmethod
    def mock_api_response(page: Page, endpoint: str, response_data: dict):
        """
        Mock an API response for testing.
        
        Args:
            page: Playwright page object
            endpoint: API endpoint to mock
            response_data: Data to return
        """
        page.route(
            f"**/api/v1/{endpoint}",
            lambda route: route.fulfill(json=response_data)
        )


class VisualHelpers:
    """Helper class for visual testing operations."""
    
    @staticmethod
    def assert_element_visible(page: Page, selector: str, description: str = ""):
        """Assert that an element is visible with a descriptive message."""
        element = page.locator(selector)
        expect(element).to_be_visible()
        if description:
            print(f"✓ {description}")
    
    @staticmethod
    def assert_element_hidden(page: Page, selector: str, description: str = ""):
        """Assert that an element is hidden with a descriptive message."""
        element = page.locator(selector)
        expect(element).to_be_hidden()
        if description:
            print(f"✓ {description}")
    
    @staticmethod
    def assert_text_contains(page: Page, selector: str, text: str, description: str = ""):
        """Assert that an element contains specific text."""
        element = page.locator(selector)
        expect(element).to_contain_text(text)
        if description:
            print(f"✓ {description}")
