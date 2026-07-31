"""Application entry point for the portfolio database importer."""

try:
    from .DB_creation.database_create import main as database_creation_main
except ImportError:
    # PyCharm may execute this file directly instead of as a package module.
    from DB_creation.database_create import main as database_creation_main


def main() -> None:
    """Run the database-creation workflow."""
    database_creation_main()


if __name__ == "__main__":
    main()
