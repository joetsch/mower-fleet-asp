"""fleetplanning: solver core, scenarios, and API for the mower fleet-planning demonstrator."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fleetplanning")
except PackageNotFoundError:  # running from source, uninstalled (not the `uv run` path)
    __version__ = "0.0.0+unknown"
