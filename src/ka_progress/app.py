import asyncio
import dataclasses
import getpass

from rich.console import Console
from rich.table import Table
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

console = Console(highlight=False)

# Known math grade landing slugs to traverse
MATH_GRADE_SLUGS = [
    "/math/cc-2nd-grade-math",
    "/math/cc-third-grade-math",
    "/math/cc-fourth-grade-math",
    "/math/cc-fifth-grade-math",
    "/math/cc-sixth-grade-math",
    "/math/cc-seventh-grade-math",
    "/math/cc-eighth-grade-math",
]

@dataclasses.dataclass
class UnitStatus:
    course: str
    unit_title: str
    # Counts of completed items
    completed_articles: int = 0
    completed_exercises: int = 0
    completed_videos: int = 0
    # Counts of items that need attention
    unread_articles: int = 0
    unmastered_exercises: int = 0
    unwatched_videos: int = 0

    @property
    def is_done(self) -> bool:
        return (
            self.unread_articles == 0 and
            self.unmastered_exercises == 0 and
            self.unwatched_videos == 0
        )


async def login_and_capture_context():
    pw = await async_playwright().start()
    browser = await pw.firefox.launch(headless=True)
    context = await browser.new_context()
    await context.add_cookies([
        {"name": "OptanonAlertBoxClosed", "value": "1", "domain": ".khanacademy.org", "path": "/"},
        {"name": "OptanonConsent", "value": "isIABGlobal=false&datestamp=2025-01-01T00:00:00.000Z", "domain": ".khanacademy.org", "path": "/"},
    ])
    page = await context.new_page()

    identifier = input("Khan Academy email or username: ").strip()
    password = getpass.getpass("Password: ").strip()
    if not identifier or not password:
        raise SystemExit("Identifier and password are required")

    console.print("[bold]Logging into Khan Academy... [/]")
    await page.goto("https://www.khanacademy.org/login", wait_until="domcontentloaded")

    # There are multiple login options; pick email/password tab
    email_selector = "input[name='username']"
    password_selector = "input[name='current-password']"
    submit_selector = "button[type='submit']"

    await page.fill(email_selector, identifier)
    await page.fill(password_selector, password)
    await page.click(submit_selector)
    await page.wait_for_load_state("networkidle")

    return context, page


async def traverse_course(page, course_slug: str):
    await page.goto(f"https://www.khanacademy.org{course_slug}", wait_until="networkidle")
    title = await page.locator('h1[data-testid="course-unit-title"]').inner_text()
    if not title:
        console.print(f"[red]Course title not found for slug: {course_slug}[/]")
        return

    console.print(f"[bold]Fetching progress for course: {title}[/]")

    unit_selector = 'a[data-testid="unit-header"]'
    unit_urls = await page.locator(unit_selector).evaluate_all("els => els.map(e => e.href)")
    if not unit_urls:
        console.print(f"[red]No units found for course: {title}[/]")
        return

    statuses = []
    for unit_url in unit_urls:
        status = await fetch_unit_progress(page, title, unit_url)
        if status:
            statuses.append(status)

    table = Table(title=f"Progress for {title}")
    table.add_column("Unit Title", style="cyan", no_wrap=True)
    table.add_column("Completed Articles", justify="right")
    table.add_column("Unread Articles", justify="right")
    table.add_column("Completed Videos", justify="right")
    table.add_column("Unwatched Videos", justify="right")
    table.add_column("Completed Exercises", justify="right")
    table.add_column("Unmastered Exercises", justify="right")
    table.add_column("Status", justify="right", style="green")

    for status in statuses:
        table.add_row(
            status.unit_title,
            str(status.completed_articles),
            str(status.unread_articles),
            str(status.completed_videos),
            str(status.unwatched_videos),
            str(status.completed_exercises),
            str(status.unmastered_exercises),
            "✅" if status.is_done else "❗️",
        )

    console.print(table)


async def fetch_unit_progress(page, course_title: str, url: str):
    captured_responses = []

    def on_response(response):
        # We only care about KA graphql responses for topic progress
        if "/api/internal/graphql/getUserInfoForTopicProgressMastery" in response.request.url:
            captured_responses.append(response)

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="networkidle")
        title = await page.locator('h1[data-testid="course-unit-title"]').inner_text()
        console.print(f"-----> Fetching progress for unit: {title}")
    except PWTimeout:
        console.print(f"[red]Timeout while fetching {url}[/]")
    except Exception as e:
        console.print(f"[red]Error fetching {url}: {e}[/]")

    page.remove_listener("response", on_response)

    if not captured_responses:
        console.print(f"[red]No progress data found for unit: {title}[/]")
        return

    status = UnitStatus(course=course_title, unit_title=title)

    for response in captured_responses:
        try:
            payload = await response.json()
        except Exception as e:
            console.print(f"[red]Error parsing response: {e}[/]")
            continue

        update_unit_status_from_payload(status, payload)

    return status


def update_unit_status_from_payload(status, payload):
    # Extract progress data from the payload
    item_progress = payload.get("data", {}).get("user", {}).get("contentItemProgresses", [])
    for item in item_progress:
        is_completed = item.get("completionStatus", "") == "COMPLETE"
        item_type = item.get("content", {}).get("__typename", "")
        match item_type:
            case "Article":
                if is_completed:
                    status.completed_articles += 1
                else:
                    status.unread_articles += 1
            case "Video":
                if is_completed:
                    status.completed_videos += 1
                else:
                    status.unwatched_videos += 1
            case "Exercise":
                if is_completed:
                    status.completed_exercises += 1
                else:
                    status.unmastered_exercises += 1
            case _:
                console.print(f"[yellow]Unknown item type: {item_type}[/]")


async def main():
    context, page = await login_and_capture_context()
    for slug in MATH_GRADE_SLUGS:
        console.print(f"\n[bold]Traversing course slug: {slug}[/]")
        try:
            await traverse_course(page, slug)
        except Exception as e:
            console.print(f"[red]Error traversing course slug {slug}: {e}[/]")
    await context.close()


if __name__ == "__main__":
    asyncio.run(main())
