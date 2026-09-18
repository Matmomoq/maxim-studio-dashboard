"""Local development entry point."""

from pathlib import Path

from dashboard.app import create_app


if __name__ == "__main__":
    dashboard_directory = Path(__file__).resolve().parent / "dashboard"
    watched_assets = [
        *dashboard_directory.joinpath("static").glob("*.js"),
        *dashboard_directory.joinpath("static").glob("*.css"),
        *dashboard_directory.joinpath("templates").glob("*.html"),
    ]
    create_app().run(
        host="127.0.0.1",
        port=8080,
        debug=True,
        extra_files=[str(path) for path in watched_assets],
    )
