import asyncio
import dataclasses
import getpass
from typing import Any

from rich.console import Console
from rich.table import Table
import playwright.async_api as pw_api

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
            self.unread_articles == 0
            and self.unmastered_exercises == 0
            and self.unwatched_videos == 0
        )


class KAProgress:
    """Encapsulates Playwright session and progress traversal."""

    def __init__(self, headless: bool = True) -> None:
        self.headless: bool = headless
        self._pw: pw_api.Playwright | None = None
        self._browser: pw_api.Browser | None = None
        self._context: pw_api.BrowserContext | None = None
        self._page: pw_api.Page | None = None

    async def start(self) -> None:
        self._pw = await pw_api.async_playwright().start()
        self._browser = await self._pw.firefox.launch(headless=self.headless)
        self._context = await self._browser.new_context()
        await self.context.add_cookies(
            [
                {
                    "name": "OptanonAlertBoxClosed",
                    "value": "1",
                    "domain": ".khanacademy.org",
                    "path": "/",
                },
                {
                    "name": "OptanonConsent",
                    "value": "isIABGlobal=false&datestamp=2025-01-01T00:00:00.000Z",
                    "domain": ".khanacademy.org",
                    "path": "/",
                },
            ]
        )
        self._page = await self.context.new_page()

    @property
    def pw(self) -> pw_api.Playwright:
        if self._pw is None:
            raise RuntimeError("KAProgress.start() must be called first (Playwright)")
        return self._pw

    @property
    def browser(self) -> pw_api.Browser:
        if self._browser is None:
            raise RuntimeError("KAProgress.start() must be called first (Browser)")
        return self._browser

    @property
    def context(self) -> pw_api.BrowserContext:
        if self._context is None:
            raise RuntimeError("KAProgress.start() must be called first (Context)")
        return self._context

    @property
    def page(self) -> pw_api.Page:
        if self._page is None:
            raise RuntimeError("KAProgress.start() must be called first (Page)")
        return self._page

    async def login(self) -> None:
        identifier = input("Khan Academy email or username: ").strip()
        password = getpass.getpass("Password: ").strip()
        if not identifier or not password:
            raise SystemExit("Identifier and password are required")

        console.print("[bold]Logging into Khan Academy... [/]")
        await self.page.goto(
            "https://www.khanacademy.org/login", wait_until="domcontentloaded"
        )

        # There are multiple login options; pick email/password tab
        email_selector = "input[name='username']"
        password_selector = "input[name='current-password']"
        submit_selector = "button[type='submit']"

        await self.page.fill(email_selector, identifier)
        await self.page.fill(password_selector, password)
        await self.page.click(submit_selector)
        await self.page.wait_for_load_state("networkidle")

    async def close(self) -> None:
        # Best-effort cleanup using properties for consistency
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.pw.stop()
        except Exception:
            pass

    async def traverse_course(self, course_slug: str) -> None:
        await self.page.goto(
            f"https://www.khanacademy.org{course_slug}", wait_until="networkidle"
        )
        title = await self.page.locator(
            'h1[data-testid="course-unit-title"]'
        ).inner_text()
        if not title:
            console.print(
                f"[red]Course title not found for slug: {course_slug}[/]"
            )
            return

        console.print(f"[bold]Fetching progress for course: {title}[/]")

        unit_selector = 'a[data-testid="unit-header"]'
        unit_urls = await self.page.locator(unit_selector).evaluate_all(
            "els => els.map(e => e.href)"
        )
        if not unit_urls:
            console.print(f"[red]No units found for course: {title}[/]")
            return

        statuses: list[UnitStatus] = []
        for unit_url in unit_urls:
            status = await self.fetch_unit_progress(title, unit_url)
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

    async def fetch_unit_progress(
        self, course_title: str, url: str
    ) -> UnitStatus | None:
        captured_responses = []
        title = "Unknown unit"

        def on_response(response):
            # We only care about KA graphql responses for topic progress
            if (
                "/api/internal/graphql/getUserInfoForTopicProgressMastery"
                in response.request.url
            ):
                captured_responses.append(response)

        self.page.on("response", on_response)

        try:
            await self.page.goto(url, wait_until="networkidle")
            title = await self.page.locator(
                'h1[data-testid="course-unit-title"]'
            ).inner_text()
            console.print(f"-----> Fetching progress for unit: {title}")
        except pw_api.TimeoutError:
            console.print(f"[red]Timeout while fetching {url}[/]")
            return None
        except Exception as e:
            console.print(f"[red]Error fetching {url}: {e}[/]")
            return None
        finally:
            # Ensure we always detach the listener
            self.page.remove_listener("response", on_response)

        if not captured_responses:
            console.print(f"[red]No progress data found for unit: {title}[/]")
            return None

        status = UnitStatus(course=course_title, unit_title=title)

        for response in captured_responses:
            try:
                payload = await response.json()
            except Exception as e:
                console.print(f"[red]Error parsing response: {e}[/]")
                continue

            self.update_unit_status_from_payload(status, payload)

        return status

    @staticmethod
    def update_unit_status_from_payload(
        status: UnitStatus, payload: dict[str, Any]
    ) -> None:
        # Extract progress data from the payload
        item_progress = (
            payload.get("data", {}).get("user", {}).get("contentItemProgresses", [])
        )
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


async def main(slugs: list[str] | None = None, headless: bool = True):
    app = KAProgress(headless=headless)
    await app.start()
    await app.login()
    use_slugs = slugs if slugs else MATH_GRADE_SLUGS
    try:
        for slug in use_slugs:
            console.print(f"\n[bold]Traversing course slug: {slug}[/]")
            try:
                await app.traverse_course(slug)
            except Exception as e:
                console.print(f"[red]Error traversing course slug {slug}: {e}[/]")
    finally:
        await app.close()


if __name__ == "__main__":
    asyncio.run(main())
