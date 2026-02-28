import uvicorn

from medicare_navigator.api.app import app
from medicare_navigator.config import settings


def main() -> None:
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
