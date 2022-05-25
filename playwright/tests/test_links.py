import re
from playwright.sync_api import expect

two_minutes = 120 * 1000


def test_create_link(logged_in_user) -> None:
    """It should be possible to successfully create a link from a URL"""
    url_field = logged_in_user.locator('#rawUrl')
    url_field.focus()
    url_field.type("https://example.com/")
    logged_in_user.locator('#addlink').click()
    logged_in_user.wait_for_url(re.compile('/[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$'), timeout=two_minutes)
    replay_frame = logged_in_user.frame('https://example.com/')
    assert logged_in_user.title() == 'Perma | Example Domain'
    assert "Example Domain" in replay_frame.locator('h1').text_content()

def test_link_required(logged_in_user) -> None:
    """A friendly message should be displayed if the field is omitted"""
    url_field = logged_in_user.locator('#rawUrl')
    url_field.focus()
    logged_in_user.locator('#addlink').click()
    expect(logged_in_user.locator("p.message-large")).to_have_text("URL cannot be empty.")
