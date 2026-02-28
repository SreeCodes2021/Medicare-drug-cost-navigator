"""Ingestion CLI — seeds demo data or loads external CMS files."""

from medicare_navigator.ingestion.seed import run_seed


def main() -> None:
    run_seed()
    print("Ingestion complete.")


if __name__ == "__main__":
    main()
