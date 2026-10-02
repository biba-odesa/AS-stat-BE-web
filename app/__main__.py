"""Run the web application using project configuration."""
import sys
import uvicorn
from app.config import ConfigurationError, Settings


def main():
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 2
    uvicorn.run('app.main:app', host=settings.host, port=settings.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
