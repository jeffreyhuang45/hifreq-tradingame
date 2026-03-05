"""
Pytest configuration for E2E tests.
"""
import pytest
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from tests.e2e.playwright_config import BASE_URL, HEADLESS, BROWSERS, VIEWPORT, SCREENSHOTS_DIR


@pytest.fixture(scope="session")
def browser_type_launch_args():
    """Browser launch arguments."""
    return {
        "headless": HEADLESS,
    }


@pytest.fixture(scope="session")
def browser_context_args():
    """Browser context arguments."""
    return {
        "viewport": VIEWPORT,
        "ignore_https_errors": True,
        "record_video_dir": None,  # Set to a directory path to enable video recording
    }


@pytest.fixture(scope="session")
def playwright():
    """Create Playwright instance for the session."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright, browser_type_launch_args):
    """Create browser instance for the session."""
    browser_type = playwright.chromium  # Default to chromium
    browser = browser_type.launch(**browser_type_launch_args)
    yield browser
    browser.close()


@pytest.fixture
def context(browser, browser_context_args):
    """Create a new browser context for each test."""
    context = browser.new_context(**browser_context_args)
    yield context
    context.close()


@pytest.fixture
def page(context):
    """Create a new page for each test."""
    page = context.new_page()
    yield page
    page.close()


@pytest.fixture(autouse=True)
def auto_screenshot_on_failure(request, page):
    """Automatically take screenshot on test failure."""
    yield
    if request.node.rep_call.failed:
        screenshot_name = f"failure_{request.node.name}"
        screenshot_path = SCREENSHOTS_DIR / f"{screenshot_name}.png"
        page.screenshot(path=str(screenshot_path))
        print(f"\nScreenshot saved to: {screenshot_path}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook to make test results available to fixtures."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
